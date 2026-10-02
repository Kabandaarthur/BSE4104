"""Week 5 agent loop: a bounded Sense / Plan / Act / Observe / Stop loop.

Week 4 gave us a tool-calling loop: the model could ask for a tool, we'd run
it and hand the result back, repeating until it had no more tool calls left
-- but RAG retrieval was never one of the choices available inside that
loop. src/main.py always retrieved evidence *before* calling the model, so
the model could never decide for itself "the handbook might answer this,
let me check" partway through a run.

Week 5 closes that gap. Retrieval is now declared as a third tool
(retrieve_evidence) the model can call alongside the Week 4 tools, so one
bounded run can actually chain:

    check an existing case (get_case_status)
      -> not found? search the handbook (retrieve_evidence)
          -> not resolved? raise a ticket (create_support_ticket)

which is exactly the task the Week 5 brief asks this loop to prove.

Each iteration of run_turn() is one full lap of the bounded loop:

    Sense    -- look at the conversation as it stands so far
    Plan     -- call the model; it decides the next action or stops
    Act      -- dispatch whatever tool call(s) it asked for
    Observe  -- read back each tool's result (or error)
    Stop     -- end the run if the goal is met, or re-plan (loop again)

The loop is bounded on every side the Agent Task Contract requires:

  * a hard iteration cap (MAX_TOOL_ROUNDS) -- stop_reason="round_limit_reached"
  * no undeclared tool may ever be dispatched -- treated as a contract
    violation, not executed
  * no identical (name, arguments) call may run twice in one run -- the
    repeat is refused as a contract violation instead of re-executed
  * a Human-in-the-Loop approval gate (create_support_ticket returning
    requires_human_approval=True) stops the run immediately --
    stop_reason="approval_pending"
  * two tool-level hard failures (a timeout, a retrieval error -- NOT a
    normal "not found"/"invalid" business result) stop the run rather than
    letting the model guess -- stop_reason="tool_error"
  * otherwise the run ends the normal way, once the model answers with no
    further tool calls -- stop_reason="goal_satisfied"

TurnResult.trace carries one entry per Sense/Plan/Act/Observe/Stop step,
intended to be dumped straight into evidence/traces/ for the three required
execution traces (Section 5 of the Week 5 plan).

Message history is always preserved inside a turn (the tool protocol
requires it). Across student turns the app stays stateless by default to
honour prompt rule C3 ("no memory of earlier conversations"); the
Orchestrator class keeps a session history only when carry_history=True
(Week 6 memory).
"""

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from model_client import TOOLS, chat_completion
from prompt_loader import load_prompt_spec
from retriever import retrieve, format_evidence, RetrievalError
from tools.handlers import dispatch

load_dotenv()

# "Maximum iterations per run" from the Agent Task Contract (Section 4).
MAX_TOOL_ROUNDS = int(os.getenv("MAX_TOOL_ROUNDS", "5"))

# Contract: "A tool call fails ... stop or re-plan once, never guess a
# result." We allow one hard failure to be re-planned around; a second
# distinct hard failure in the same run stops the loop instead of letting
# the model keep guessing.
MAX_TOOL_ERROR_STRIKES = int(os.getenv("MAX_TOOL_ERROR_STRIKES", "2"))

# Error codes that represent the tool/service itself failing (infra,
# validation-of-the-protocol) rather than a normal negative business
# outcome. CASE_NOT_FOUND, DUPLICATE_TICKET, DISALLOWED_TOPIC etc. are
# valid results the model is expected to act on -- they are not failures.
HARD_FAILURE_CODES = {
    "DATABASE_TIMEOUT",
    "RETRIEVAL_FAILED",
    "UNKNOWN_TOOL",
    "UNDECLARED_TOOL",
    "DUPLICATE_TOOL_CALL",
}

# The Week 4 tools (get_case_status, create_support_ticket) plus the new
# Week 5 retrieval tool, declared once here so the model can choose it
# inside the same bounded loop instead of evidence being force-fed before
# the loop even starts.
RETRIEVE_EVIDENCE_TOOL = {
    "type": "function",
    "function": {
        "name": "retrieve_evidence",
        "description": (
            "Search the official handbook/policy corpus for passages that "
            "might resolve the student's question. Call this when "
            "get_case_status found no existing case (or isn't relevant) "
            "and you need to check whether written policy already answers "
            "the question before deciding to raise a support ticket. "
            "Returns the matched passages and their source document IDs, "
            "or found=false if nothing relevant exists in the corpus."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The search query -- usually the student's message, "
                        "or the specific sub-question still unresolved."
                    ),
                }
            },
            "required": ["query"],
        },
    },
}

DEFAULT_TOOLS = list(TOOLS) + [RETRIEVE_EVIDENCE_TOOL]
ALLOWED_TOOL_NAMES = {
    tool["function"]["name"] for tool in DEFAULT_TOOLS if tool.get("type") == "function"
}


@dataclass
class TurnResult:
    reply: str
    history: List[dict] = field(default_factory=list)
    tool_calls: List[str] = field(default_factory=list)
    executions: List[Dict[str, Any]] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    trace: List[Dict[str, Any]] = field(default_factory=list)
    rounds: int = 0
    iterations: int = 0
    stop_reason: str = "goal_satisfied"


def build_messages(system_prompt, history, user_message):
    """Compose the message list for one model call from a fresh turn."""
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": user_message})
    return messages


def _tool_call_dict(tool_call):
    """Normalise either an SDK tool_call object or a raw dict to a JSON-safe dict."""
    if isinstance(tool_call, dict):
        data = tool_call
    else:
        function = getattr(tool_call, "function", None)
        data = {
            "id": getattr(tool_call, "id", "") or "",
            "type": getattr(tool_call, "type", "function") or "function",
            "function": {
                "name": getattr(function, "name", "") or "",
                "arguments": getattr(function, "arguments", "") or "",
            },
        }
    function = data.get("function") or {}
    arguments = function.get("arguments")
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments) if arguments is not None else ""
    return {
        "id": data.get("id") or "",
        "type": data.get("type") or "function",
        "function": {"name": function.get("name") or "", "arguments": arguments},
    }


def parse_tool_arguments(raw):
    """Parse a tool call's JSON arguments string into a dict.

    Returns {} for empty input and None for unparseable input (the
    dispatcher turns None into a VALIDATION_FAILED result the model can
    read).
    """
    if raw is None:
        return {}
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return {}
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return None
    return raw


def parse_tool_call(tool_call):
    """Split a tool_call into {"id", "name", "arguments"} with parsed arguments."""
    data = _tool_call_dict(tool_call)
    function = data["function"]
    return {
        "id": data["id"],
        "name": function["name"],
        "arguments": parse_tool_arguments(function["arguments"]),
    }


def _assistant_message(message):
    """Convert an SDK message (or dict) into the re-sendable assistant turn."""
    if isinstance(message, dict):
        content = message.get("content")
        tool_calls = message.get("tool_calls") or []
    else:
        content = getattr(message, "content", None)
        tool_calls = getattr(message, "tool_calls", None) or []
    assistant = {"role": "assistant", "content": content}
    if tool_calls:
        assistant["tool_calls"] = [_tool_call_dict(call) for call in tool_calls]
    return assistant


def _tool_message(call_id, result):
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(result, ensure_ascii=False),
    }


def _rounds_exceeded(max_rounds):
    return {
        "error": {
            "code": "TOOL_ROUNDS_EXCEEDED",
            "description": (
                f"The tool loop exceeded its limit of {max_rounds} iterations; "
                "no further tool calls are allowed this turn."
            ),
            "http_status": 429,
        }
    }


def _contract_violation(code, description):
    return {
        "error": {
            "code": code,
            "description": description,
            "http_status": 409,
        }
    }


def _call_key(name, arguments):
    """A stable key identifying one (tool, arguments) pair, for the
    no-repeated-identical-call rule."""
    try:
        canonical_args = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
    except TypeError:
        canonical_args = repr(arguments)
    return f"{name}:{canonical_args}"


def _dispatch_retrieve_evidence(arguments):
    """Handler for the retrieve_evidence tool: wraps src/retriever.py so
    retrieval becomes just another Act step inside the bounded loop."""
    query = arguments.get("query") if isinstance(arguments, dict) else None
    if not isinstance(query, str) or not query.strip():
        return {
            "error": {
                "code": "VALIDATION_FAILED",
                "description": "retrieve_evidence requires a non-empty 'query' string.",
                "http_status": 422,
            }
        }

    try:
        chunks = retrieve(query)
    except RetrievalError as error:
        return {
            "error": {
                "code": "RETRIEVAL_FAILED",
                "description": f"Evidence retrieval failed: {error}",
                "http_status": 503,
            }
        }

    if not chunks:
        return {"found": False, "sources": [], "evidence": ""}

    sources = []
    for chunk in chunks:
        if chunk.source not in sources:
            sources.append(chunk.source)

    return {"found": True, "sources": sources, "evidence": format_evidence(chunks)}


def _dispatch(name, arguments, seen_calls):
    """Act step: enforce the allow-list and no-repeat rule, then run the
    tool. Returns (result, is_hard_failure)."""
    if name not in ALLOWED_TOOL_NAMES:
        return _contract_violation(
            "UNDECLARED_TOOL", f"'{name}' is not an approved tool for this run."
        )

    key = _call_key(name, arguments)
    if key in seen_calls:
        return _contract_violation(
            "DUPLICATE_TOOL_CALL",
            f"'{name}' was already called with identical arguments this run.",
        )
    seen_calls.add(key)

    if name == "retrieve_evidence":
        return _dispatch_retrieve_evidence(arguments)
    return dispatch(name, arguments)


def run_turn(
    user_message: str,
    system_prompt: str,
    provider: Optional[str] = None,
    history: Optional[List[dict]] = None,
    tools: Optional[List[dict]] = None,
    max_rounds: int = MAX_TOOL_ROUNDS,
    temperature: float = 0.4,
) -> TurnResult:
    """Drive one student turn through the bounded Sense/Plan/Act/Observe/Stop
    loop and return the reply plus everything that happened along the way.

    `history` is the prior session history (if any); the returned
    TurnResult.history contains only the messages appended during this
    turn (assistant turns and tool results), ready to be chained by a
    caller.
    """
    tools = DEFAULT_TOOLS if tools is None else tools
    messages = build_messages(system_prompt, history, user_message)

    turn: List[dict] = []
    invoked: List[str] = []
    executions: List[Dict[str, Any]] = []
    sources: List[str] = []
    trace: List[Dict[str, Any]] = []
    seen_calls: set = set()
    error_strikes = 0
    iteration = 0

    def _close(stop_reason: str, completed_iterations: int) -> TurnResult:
        """Make one final model call with no tools attached, so the model
        closes the turn in its own words instead of us fabricating a
        message -- used by every forced-stop path (round limit, approval
        gate, repeated hard failure)."""
        final_message = chat_completion(
            messages, provider=provider, temperature=temperature, tools=None
        )
        assistant = _assistant_message(final_message)
        messages.append(assistant)
        turn.append(assistant)
        trace.append({"iteration": iteration, "stage": "stop", "detail": stop_reason})
        return TurnResult(
            reply=assistant.get("content") or "",
            history=turn,
            tool_calls=invoked,
            executions=executions,
            sources=sources,
            trace=trace,
            rounds=completed_iterations,
            iterations=completed_iterations,
            stop_reason=stop_reason,
        )

    while True:
        iteration += 1
        trace.append(
            {
                "iteration": iteration,
                "stage": "sense",
                "detail": f"{len(messages)} messages in context so far",
            }
        )

        # --- Plan -----------------------------------------------------
        message = chat_completion(
            messages, provider=provider, temperature=temperature, tools=tools
        )
        assistant = _assistant_message(message)
        messages.append(assistant)
        turn.append(assistant)

        tool_calls = assistant.get("tool_calls") or []

        if not tool_calls:
            # Stop: the model answered directly -- goal satisfied.
            trace.append(
                {
                    "iteration": iteration,
                    "stage": "plan",
                    "detail": "model answered directly; no tool requested",
                }
            )
            trace.append(
                {"iteration": iteration, "stage": "stop", "detail": "goal_satisfied"}
            )
            return TurnResult(
                reply=assistant.get("content") or "",
                history=turn,
                tool_calls=invoked,
                executions=executions,
                sources=sources,
                trace=trace,
                rounds=iteration,
                iterations=iteration,
                stop_reason="goal_satisfied",
            )

        parsed_calls = [parse_tool_call(call) for call in tool_calls]
        trace.append(
            {
                "iteration": iteration,
                "stage": "plan",
                "detail": [f"{c['name']}({c['arguments']})" for c in parsed_calls],
            }
        )

        if iteration > max_rounds:
            # Stop: hard iteration cap reached before these calls could
            # run. Answer every outstanding call with the same error so
            # the model can't silently skip explaining what happened.
            for parsed in parsed_calls:
                tool_msg = _tool_message(parsed["id"], _rounds_exceeded(max_rounds))
                messages.append(tool_msg)
                turn.append(tool_msg)
            return _close("round_limit_reached", iteration - 1)

        # --- Act / Observe ---------------------------------------------
        approval_hit = False
        for parsed in parsed_calls:
            name, arguments = parsed["name"], parsed["arguments"]
            invoked.append(name)

            result = _dispatch(name, arguments, seen_calls)
            status = "error" if isinstance(result, dict) and "error" in result else "success"

            trace.append(
                {
                    "iteration": iteration,
                    "stage": "observe",
                    "detail": {"tool": name, "status": status, "result": result},
                }
            )
            executions.append(
                {"name": name, "arguments": arguments, "status": status, "result": result}
            )

            if name == "retrieve_evidence" and status == "success":
                for source_id in result.get("sources", []):
                    if source_id not in sources:
                        sources.append(source_id)

            if status == "error" and result["error"].get("code") in HARD_FAILURE_CODES:
                error_strikes += 1

            if status == "success" and result.get("requires_human_approval"):
                approval_hit = True

            tool_msg = _tool_message(parsed["id"], result)
            messages.append(tool_msg)
            turn.append(tool_msg)

        # --- Stop / Re-plan ----------------------------------------------
        if approval_hit:
            # Stop: Human-in-the-Loop gate hit -- never keep acting past
            # a ticket that is now awaiting staff review.
            return _close("approval_pending", iteration)

        if error_strikes >= MAX_TOOL_ERROR_STRIKES:
            # Stop: a second hard failure this run -- re-plan once is the
            # limit; never let the model keep guessing past that.
            return _close("tool_error", iteration)

        # Otherwise: Re-plan. Loop back to Sense with the grown history.


class Orchestrator:
    """Session wrapper that can carry the message history between student turns.

    By default each turn starts with a blank history (prompt rule C3: no
    memory of earlier conversations). Set carry_history=True to persist
    turns for a multi-turn session (Week 6 will formalise this as memory).
    """

    def __init__(
        self,
        system_prompt: Optional[str] = None,
        provider: Optional[str] = None,
        max_tool_rounds: int = MAX_TOOL_ROUNDS,
        carry_history: bool = False,
    ):
        self.system_prompt = load_prompt_spec() if system_prompt is None else system_prompt
        self.provider = provider
        self.max_tool_rounds = max_tool_rounds
        self.carry_history = carry_history
        self.history: List[dict] = []

    def ask(self, user_message: str, evidence: str = "") -> TurnResult:
        message = f"{evidence}\n\nStudent message:\n{user_message}" if evidence else user_message
        result = run_turn(
            message,
            system_prompt=self.system_prompt,
            provider=self.provider,
            history=self.history,
            max_rounds=self.max_tool_rounds,
        )
        self.history = (self.history + result.history) if self.carry_history else []
        return result

    def reset(self) -> None:
        self.history = []
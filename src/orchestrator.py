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

Each of those four outcomes is a distinct STOP_REASONS entry: a canonical
slug the trace reports, a one-line description of *why* the loop stopped, and
a deterministic student-facing message. Every loop exit goes through
_stop_reason(), so the four cases are separately nameable in the execution
trace and in an assertion -- you never have to infer the stop condition from
the reply text.

A forced stop must never be a silent failure. _close() asks the model to
explain the stop in its own words (with tools detached so it cannot ask for
another round), but the model is not trusted to comply: if that final call
returns nothing usable, the loop substitutes the deterministic message for
that stop reason rather than returning an empty reply. The round-limit case
in particular is required to read as "I could not fully resolve this".

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

from model_client import TOOLS, ModelClientError, chat_completion
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


# --- The four termination cases -------------------------------------------
# Every exit from run_turn() is one of these four. They are declared once,
# here, so the condition is a named, testable value rather than a string
# literal buried in the loop, and so the student-facing wording is owned by
# the deterministic layer instead of depending on the model volunteering it.
#
#   summary         one line on why the loop stopped, copied into the trace
#   final_message   the guaranteed fallback reply if the model's own closing
#                   message is missing or unusable (never a silent failure)
STOP_REASONS = {
    "goal_satisfied": {
        "summary": (
            "The student's goal was met: the model reported a case status, "
            "answered from retrieved evidence, or logged a support ticket."
        ),
        # The model authored the reply here; there is nothing to fall back to.
        "final_message": None,
    },
    "round_limit_reached": {
        "summary": (
            f"The hard iteration cap (MAX_TOOL_ROUNDS, default "
            f"{MAX_TOOL_ROUNDS}) was reached without the goal being met."
        ),
        "final_message": (
            "I could not fully resolve this. I reached the limit of what I can "
            "check in one go, so I have stopped rather than guess. Please "
            "contact the office handling your case, quoting any case or ticket "
            "ID above."
        ),
    },
    "approval_pending": {
        "summary": (
            "The Human-in-the-Loop gate was hit: a create_support_ticket call "
            "returned requires_human_approval=True, so the ticket is awaiting "
            "staff review."
        ),
        "final_message": (
            "Your request has been logged and is pending review by department "
            "staff. No further action has been taken yet -- please wait for "
            "staff to review it."
        ),
    },
    "tool_error": {
        "summary": (
            f"A tool failed and could not be recovered from within "
            f"{MAX_TOOL_ERROR_STRIKES} attempts (the allowed single re-plan "
            f"was used); the loop stopped instead of guessing a result."
        ),
        "final_message": (
            "I could not complete this because a service I depend on is not "
            "responding, so I have stopped rather than give you an unverified "
            "answer. Please try again later, or contact the office handling "
            "your case directly."
        ),
    },
}

GOAL_SATISFIED = "goal_satisfied"
ROUND_LIMIT_REACHED = "round_limit_reached"
APPROVAL_PENDING = "approval_pending"
TOOL_ERROR = "tool_error"


class StopConditionError(RuntimeError):
    """Raised when run_turn() is asked to stop under an undeclared reason.

    Guards against a fifth, unnamed exit creeping into the loop later and
    silently escaping the trace's vocabulary.
    """

    def __init__(self, stop_reason):
        super().__init__(
            f"Undeclared stop reason '{stop_reason}'. "
            f"Allowed: {sorted(STOP_REASONS)}."
        )
        self.stop_reason = stop_reason


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
    """Normalise either an SDK tool_call object or a raw dict to a JSON-safe dict.

    Gemini attaches a `thought_signature` to each tool call in
    `extra_content`; it must be replayed verbatim on the next request or the
    API rejects the continuation. So extra_content is carried through instead
    of being dropped by the normalisation.
    """
    if isinstance(tool_call, dict):
        data = tool_call
        extra_content = data.get("extra_content")
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
        extra_content = getattr(tool_call, "extra_content", None)
    function = data.get("function") or {}
    arguments = function.get("arguments")
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments) if arguments is not None else ""
    normalized = {
        "id": data.get("id") or "",
        "type": data.get("type") or "function",
        "function": {"name": function.get("name") or "", "arguments": arguments},
    }
    if extra_content:
        normalized["extra_content"] = extra_content
    return normalized


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

    def _result(
        stop_reason: str,
        reply: str,
        completed_iterations: int,
        message_source: str,
    ) -> TurnResult:
        """Build the TurnResult for a stop, recording the termination case as
        a structured, nameable trace entry."""
        spec = STOP_REASONS.get(stop_reason)
        if spec is None:
            raise StopConditionError(stop_reason)
        trace.append(
            {
                "iteration": completed_iterations,
                "stage": "stop",
                "detail": {
                    "stop_reason": stop_reason,
                    "summary": spec["summary"],
                    "message_source": message_source,
                },
            }
        )
        return TurnResult(
            reply=reply,
            history=turn,
            tool_calls=invoked,
            executions=executions,
            sources=sources,
            trace=trace,
            rounds=completed_iterations,
            iterations=completed_iterations,
            stop_reason=stop_reason,
        )

    def _close(stop_reason: str, completed_iterations: int) -> TurnResult:
        """Stop a run the loop forced (round limit, approval gate, repeated
        tool failure).

        The model gets one last say, with tools detached so it cannot ask for
        another round, so it explains the stop in its own words rather than us
        fabricating prose. But the model is not trusted to comply: if that
        call fails or comes back empty, we substitute this stop reason's
        deterministic message. A forced stop is never a silent failure -- for
        round_limit_reached that fallback is the required "I could not fully
        resolve this" wording.
        """
        spec = STOP_REASONS.get(stop_reason)
        if spec is None:
            raise StopConditionError(stop_reason)

        reply = ""
        message_source = "loop_fallback"
        try:
            final_message = chat_completion(
                messages, provider=provider, temperature=temperature, tools=None
            )
            assistant = _assistant_message(final_message)
            messages.append(assistant)
            turn.append(assistant)
            reply = assistant.get("content") or ""
            if reply.strip():
                message_source = "model"
        except ModelClientError as error:
            # The closing call itself failed. Still stop cleanly with the
            # deterministic message -- the run must end, not raise.
            trace.append(
                {
                    "iteration": completed_iterations,
                    "stage": "observe",
                    "detail": {
                        "tool": "(closing message)",
                        "status": "error",
                        "result": {
                            "error": {
                                "code": "MODEL_UNAVAILABLE",
                                "description": str(error),
                            }
                        },
                    },
                }
            )
            reply = ""

        if not reply.strip():
            reply = spec["final_message"] or ""
            trace.append(
                {
                    "iteration": completed_iterations,
                    "stage": "stop",
                    "detail": {
                        "stop_reason": stop_reason,
                        "fallback_applied": True,
                        "reason": "closing message unavailable or empty",
                    },
                }
            )

        return _result(stop_reason, reply, completed_iterations, message_source)

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
            # Stop: the model answered directly and chose to stop -- the goal
            # is met (a status was reported, evidence was cited, a ticket was
            # created, or the request was correctly refused).
            trace.append(
                {
                    "iteration": iteration,
                    "stage": "plan",
                    "detail": "model answered directly; no tool requested",
                }
            )
            return _result(
                GOAL_SATISFIED,
                assistant.get("content") or "",
                iteration,
                "model",
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
            # Stop: the hard iteration cap is reached before these calls could
            # run. Answer every outstanding call with the same error so the
            # model can't silently skip explaining what happened, then close
            # the turn with a human-readable "could not fully resolve" message
            # (_close guarantees that wording even if the model says nothing).
            for parsed in parsed_calls:
                tool_msg = _tool_message(parsed["id"], _rounds_exceeded(max_rounds))
                messages.append(tool_msg)
                turn.append(tool_msg)
            return _close(ROUND_LIMIT_REACHED, iteration - 1)

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
            # Stop: Human-in-the-Loop gate hit (EXAMINATION / HIGH urgency) --
            # never keep acting past a ticket that is now awaiting staff
            # review. Unchanged from Week 4: the student is told the request
            # is pending staff review.
            return _close(APPROVAL_PENDING, iteration)

        if error_strikes >= MAX_TOOL_ERROR_STRIKES:
            # Stop: a second hard failure this run. The single allowed
            # re-plan is spent -- the loop stops rather than let the model
            # keep guessing at a result the tool never returned.
            return _close(TOOL_ERROR, iteration)

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
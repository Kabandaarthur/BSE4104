"""Week 4 agent loop: tool-call parsing and the message-history loop.

Turns one student message into a reply by driving a provable loop:

    system + history + user message
        └─► model (tools attached)
                ├─► no tool calls  → the reply is done
                └─► tool_calls     → parse each call, dispatch to
                                     src/tools/handlers.py, append the result
                                     as a role="tool" message, then call the
                                     model again with the grown history

Each round appends the assistant turn (with its tool_calls) and the matching
tool results to the message list, so the model always responds to the same
conversation it created — this is what makes the loop reproducible and the
tool results auditable in the returned TurnResult.history.

The loop runs at most MAX_TOOL_ROUNDS execution rounds per turn. If the
model keeps asking for tools, the outstanding calls are answered with a
TOOL_ROUNDS_EXCEEDED error and one final model call *without* tools closes
the turn on-policy instead of fabricating a reply.

Message history is always preserved inside a turn (the tool protocol requires
it). Across student turns the app stays stateless by default to honour prompt
rule C3 ("no memory of earlier conversations"); the Orchestrator class keeps
a session history only when carry_history=True (Week 6 memory).
"""

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from model_client import TOOLS, chat_completion
from prompt_loader import load_prompt_spec
from tools.handlers import dispatch

load_dotenv()

MAX_TOOL_ROUNDS = int(os.getenv("MAX_TOOL_ROUNDS", "5"))


@dataclass
class TurnResult:
    reply: str
    history: List[dict] = field(default_factory=list)
    tool_calls: List[str] = field(default_factory=list)
    rounds: int = 0


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

    Returns {} for empty input and None for unparseable input (the dispatcher
    turns None into a VALIDATION_FAILED result the model can read).
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
    return {"role": "tool", "tool_call_id": call_id, "content": json.dumps(result, ensure_ascii=False)}


def _rounds_exceeded(max_rounds):
    return {
        "error": {
            "code": "TOOL_ROUNDS_EXCEEDED",
            "description": (
                f"The tool loop exceeded its limit of {max_rounds} rounds; "
                "no further tool calls are allowed this turn."
            ),
            "http_status": 429,
        }
    }


def run_turn(
    user_message: str,
    system_prompt: str,
    provider: Optional[str] = None,
    history: Optional[List[dict]] = None,
    tools: Optional[List[dict]] = None,
    max_rounds: int = MAX_TOOL_ROUNDS,
    temperature: float = 0.4,
) -> TurnResult:
    """Drive one student turn through the tool-calling loop and return the reply.

    `history` is the prior session history (if any); the returned
    TurnResult.history contains only the messages appended during this turn
    (assistant turns and tool results), ready to be chained by a caller.
    """
    tools = TOOLS if tools is None else tools
    messages = build_messages(system_prompt, history, user_message)
    turn: List[dict] = []
    invoked: List[str] = []
    rounds = 0

    while True:
        message = chat_completion(
            messages, provider=provider, temperature=temperature, tools=tools
        )
        assistant = _assistant_message(message)
        messages.append(assistant)
        turn.append(assistant)

        tool_calls = assistant.get("tool_calls") or []
        if not tool_calls:
            return TurnResult(
                reply=assistant.get("content") or "",
                history=turn,
                tool_calls=invoked,
                rounds=rounds,
            )

        if rounds >= max_rounds:
            for call in tool_calls:
                tool_msg = _tool_message(call["id"], _rounds_exceeded(max_rounds))
                messages.append(tool_msg)
                turn.append(tool_msg)
            final_message = chat_completion(
                messages, provider=provider, temperature=temperature, tools=None
            )
            assistant = _assistant_message(final_message)
            messages.append(assistant)
            turn.append(assistant)
            return TurnResult(
                reply=assistant.get("content") or "",
                history=turn,
                tool_calls=invoked,
                rounds=rounds,
            )

        for call in tool_calls:
            parsed = parse_tool_call(call)
            invoked.append(parsed["name"])
            result = dispatch(parsed["name"], parsed["arguments"])
            tool_msg = _tool_message(parsed["id"], result)
            messages.append(tool_msg)
            turn.append(tool_msg)
        rounds += 1


class Orchestrator:
    """Session wrapper that can carry the message history between student turns.

    By default each turn starts with a blank history (prompt rule C3: no
    memory of earlier conversations). Set carry_history=True to persist turns
    for a multi-turn session (Week 6 will formalise this as memory).
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
"""
main.py
-------
Week 5: the /chat endpoint routed through the bounded agent loop.

Week 4 wired a single tool-calling round into this endpoint, and evidence
was always fetched *before* the model was even called -- retrieval lived
outside the loop entirely. Week 5 removes that pre-fetch: retrieval is now
one of the actions the model can choose for itself, as the retrieve_evidence
tool declared inside src/orchestrator.py's bounded Sense/Plan/Act/Observe/Stop
loop. So a single POST /chat request can now trigger several internal steps
-- a case-status lookup, a handbook search, a ticket creation, in whatever
order the model decides it needs them -- before this endpoint returns
exactly one final reply.

Run:
    python src/main.py                      # interactive CLI
    python src/main.py "your message here"  # single-shot (used by the
                                              # evaluation script)
"""

import json
import logging
import os
import sys
import textwrap
from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from model_client import ModelClientError
from orchestrator import run_turn
from prompt_loader import load_prompt_spec

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("rag")

app = FastAPI(title="University Student-Support Case Agent")


class ChatRequest(BaseModel):
    message: str


class ToolExecution(BaseModel):
    """One executed tool call this run: what was asked for, and what it
    returned (or the error it raised) -- i.e. the tool's side effect."""

    name: str
    arguments: dict
    status: str  # "success" or "error"
    result: dict


class ChatResponse(BaseModel):
    reply: str
    sources: list[str] = []
    tools_used: list[ToolExecution] = []
    # How the bounded loop ended this run, and how many Sense/Plan/Act/
    # Observe iterations it took -- surfaced so a single /chat call makes
    # its own multi-step behaviour visible, not just the final reply.
    stop_reason: str = "goal_satisfied"
    iterations: int = 0


def call_model(system_prompt: str, user_message: str):
    """Drive one student message through the Week 5 bounded Sense/Plan/Act/
    Observe/Stop loop (src/orchestrator.py) and return the full TurnResult:
    the reply, every tool that ran and what it returned, retrieval sources
    gathered along the way, how many iterations it took, and why it
    stopped."""
    return run_turn(user_message, system_prompt=system_prompt)


def ask(user_message: str):
    """Send one student message through the bounded agent loop and return
    (reply, sources, tools_used, stop_reason, iterations).

    Unlike Week 4, there is no retrieval pre-fetch here: retrieval is one
    of the actions the model can choose *inside* the loop itself (the
    retrieve_evidence tool in orchestrator.py). So this one call to
    call_model() may internally run a case lookup, a handbook search, and
    a ticket creation -- in whatever order the model decides -- before
    returning a single final reply.
    """
    turn = call_model(load_prompt_spec(), user_message)

    tools_used = [ToolExecution(**execution) for execution in turn.executions]

    if tools_used:
        logger.info(
            "Query: %r -> tools executed: %s (stop_reason=%s, iterations=%d)",
            user_message,
            [t.name for t in tools_used],
            turn.stop_reason,
            turn.iterations,
        )
    else:
        logger.info(
            "Query: %r -> no tools executed (stop_reason=%s, iterations=%d)",
            user_message,
            turn.stop_reason,
            turn.iterations,
        )

    return turn.reply, turn.sources, tools_used, turn.stop_reason, turn.iterations


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat_endpoint(request: ChatRequest) -> ChatResponse:
    try:
        reply, sources, tools_used, stop_reason, iterations = ask(request.message)
        return ChatResponse(
            reply=reply,
            sources=sources,
            tools_used=tools_used,
            stop_reason=stop_reason,
            iterations=iterations,
        )
    except ModelClientError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


# --------------------------------------------------------------------------
# CLI display
#
# Everything below is presentation-only: it decides how the conversation
# looks in the terminal. None of it touches ask() / call_model() / the
# FastAPI endpoints above, and none of it changes what is sent to or
# received from the agent.
# --------------------------------------------------------------------------

_SUPPORTS_COLOR = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    if not _SUPPORTS_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


_BOLD = "1"
_CYAN = "36"
_GREEN = "32"
_YELLOW = "33"
_RED = "31"
_GRAY = "90"

_TERM_WIDTH = 78


def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _rule(char: str = "─", width: int = _TERM_WIDTH) -> str:
    return _c(_GRAY, char * width)


def _wrap(text: str, width: int) -> list[str]:
    lines = []
    for raw_line in text.strip("\n").splitlines() or [""]:
        wrapped = textwrap.wrap(
            raw_line, width=width, break_long_words=False, break_on_hyphens=False
        ) or [""]
        lines.extend(wrapped)
    return lines


def _print_banner():
    title = "Makerere University Student-Support Case Agent"
    subtitle = "Week 5 · Bounded agent loop (RAG + tools, chained)"
    print()
    print(_c(_BOLD + ";" + _CYAN, " " + title))
    print(_c(_GRAY, " " + subtitle))
    print(_rule("═"))
    print(
        _c(_GRAY, " Ask a question, check a case status, or raise a support ticket.")
    )
    print(_c(_GRAY, " Type 'quit', 'exit', or press Ctrl+C to leave."))
    print(_rule("═"))
    print()


def _print_student_message(message: str):
    label = _c(_BOLD + ";" + _CYAN, "You") + _c(_GRAY, f" {_timestamp()}")
    print(label)
    for line in _wrap(message, _TERM_WIDTH - 2):
        print(_c(_CYAN, " " + line))
    print()


def _split_paragraphs(text: str) -> list[str]:
    """Turn the raw reply into a list of paragraphs for display: each
    non-empty line becomes its own paragraph, with a blank line between."""
    return [line.strip() for line in text.strip().splitlines() if line.strip()] or [
        text.strip()
    ]


def _print_tool_executions(tools_used: list[ToolExecution]):
    if not tools_used:
        return
    print(_c(_GRAY, " tools executed:"))
    for execution in tools_used:
        color = _GREEN if execution.status == "success" else _RED
        print(
            _c(_GRAY, "   - ")
            + _c(_BOLD, execution.name)
            + _c(_GRAY, f" ({execution.status}) ")
            + _c(color, json.dumps(execution.result, ensure_ascii=False))
        )
    print()


def _print_agent_reply(
    response: str,
    sources: list[str],
    tools_used: list[ToolExecution] = None,
    stop_reason: str = "goal_satisfied",
    iterations: int = 0,
):
    label = _c(_BOLD + ";" + _GREEN, "Case Agent") + _c(_GRAY, f" {_timestamp()}")
    print(label)
    print()

    paragraphs = _split_paragraphs(response) if response.strip() else []
    if not paragraphs:
        paragraphs = ["No response returned."]

    for i, paragraph in enumerate(paragraphs):
        for line in _wrap(paragraph, _TERM_WIDTH - 2):
            print("  " + line)
        if i != len(paragraphs) - 1:
            print()

    print()

    if sources:
        tag_line = " ".join(_c(_YELLOW, f"[{s}]") for s in sources)
        print(_c(_GRAY, " sources: ") + tag_line)
    else:
        print(_c(_GRAY, " sources: none"))
    print()

    _print_tool_executions(tools_used or [])

    # Bounded-loop evidence: how many internal steps this one reply took,
    # and why the loop stopped -- exactly what the Week 5 traces need to
    # show happened "under the hood" of a single /chat request.
    print(
        _c(_GRAY, " loop: ")
        + _c(_BOLD, f"{iterations} iteration{'s' if iterations != 1 else ''}")
        + _c(_GRAY, "  stop_reason=")
        + _c(_BOLD, stop_reason)
    )
    print()

    print(_rule())
    print()


def _print_error(message: str):
    print(_c(_BOLD + ";" + _RED, "Case Agent") + _c(_GRAY, f" {_timestamp()}"))
    print(_c(_RED, f" ⚠ {message}"))
    print()
    print(_rule())
    print()


def _clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def main():
    if len(sys.argv) > 1:
        # single-shot mode, e.g. for scripted evaluation runs
        message = " ".join(sys.argv[1:])
        try:
            reply, sources, tools_used, stop_reason, iterations = ask(message)
            print(reply)
            print(f"[sources retrieved: {', '.join(sources) if sources else 'none'}]")
            if tools_used:
                print(f"[tools executed: {', '.join(t.name for t in tools_used)}]")
            print(f"[loop: {iterations} iteration(s), stop_reason={stop_reason}]")
        except ModelClientError as e:
            print(f"[ERROR] {e}")
        return

    _clear_screen()
    _print_banner()

    while True:
        try:
            user_message = input(_c(_BOLD, "> ")).strip()
        except (KeyboardInterrupt, EOFError):
            print("\n" + _c(_GRAY, "Goodbye. Have a great day!") + "\n")
            break

        if not user_message:
            continue
        if user_message.lower() in {"quit", "exit", "bye"}:
            print("\n" + _c(_GRAY, "Goodbye. Have a great day!") + "\n")
            break

        print()
        _print_student_message(user_message)

        try:
            response, sources, tools_used, stop_reason, iterations = ask(user_message)
        except ModelClientError as e:
            _print_error(str(e))
            continue

        _print_agent_reply(response, sources, tools_used, stop_reason, iterations)


if __name__ == "__main__":
    main()
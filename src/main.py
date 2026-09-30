"""
app.py
------
Week 4: the tool-calling Student-Support Case Agent.

One student message is driven through src/orchestrator.py's tool-calling
loop (model -> parse tool calls -> execute -> feed results back) up to
MAX_TOOL_ROUNDS times, then the final answer is returned.

Run:
    python src/app.py                    # interactive CLI
    python src/app.py "your message here"  # single-shot (used by the
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
from retriever import retrieve, format_evidence, RetrievalError

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("rag")

app = FastAPI(title="University Student-Support Case Agent")


class ChatRequest(BaseModel):
    message: str


class ToolExecution(BaseModel):
    """One executed tool call: what was asked for, and what it returned
    (or the error it raised) -- i.e. the tool's side effect."""

    name: str
    arguments: dict
    status: str  # "success" or "error"
    result: dict


class ChatResponse(BaseModel):
    reply: str
    sources: list[str] = []
    tools_used: list[ToolExecution] = []


def call_model(system_prompt: str, user_message: str):
    """Send one student message through the Week 4 tool-calling agent loop
    and return the full TurnResult (reply + the tool-call/tool-result
    history), so callers can see which tools ran and what they did."""
    return run_turn(user_message, system_prompt=system_prompt)


def _source_ids(chunks) -> list[str]:
    """De-duplicated, order-preserving list of source documents retrieval
    actually used this turn (e.g. ['D03.txt']), for logging + API response."""
    seen = []
    for chunk in chunks:
        if chunk.source not in seen:
            seen.append(chunk.source)
    return seen


def _tool_executions(history: list[dict]) -> list[ToolExecution]:
    """Turn one turn's message history into an auditable list of what was
    executed and what it returned.

    `history` (TurnResult.history from orchestrator.run_turn) interleaves
    assistant messages -- which may carry one or more `tool_calls` -- with
    the role="tool" messages that answer each call by id. This walks the
    history once to index every tool_call by id (name + parsed arguments),
    then a second time to pair each tool result with its call, so each
    ToolExecution carries the call and its outcome (success payload or
    error payload) together.
    """
    calls_by_id = {}
    for message in history:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                function = call.get("function", {})
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                calls_by_id[call["id"]] = {
                    "name": function.get("name", ""),
                    "arguments": arguments,
                }

    executions = []
    for message in history:
        if message.get("role") == "tool":
            call_info = calls_by_id.get(message.get("tool_call_id"), {})
            try:
                result = json.loads(message.get("content") or "{}")
            except json.JSONDecodeError:
                result = {}
            if not isinstance(result, dict):
                result = {"value": result}
            status = "error" if "error" in result else "success"
            executions.append(
                ToolExecution(
                    name=call_info.get("name", "unknown"),
                    arguments=call_info.get("arguments", {}),
                    status=status,
                    result=result,
                )
            )
    return executions


def ask(user_message: str) -> tuple[str, list[str], list[ToolExecution]]:
    """Retrieve evidence for the message, ground the model call in it, and
    return (reply, source_documents_used, tools_used)."""
    try:
        chunks = retrieve(user_message)
    except RetrievalError as error:
        # An index/query problem shouldn't take the whole agent down --
        # fall back to ungrounded behaviour, which prompts/v2.0.md already
        # handles via its "no RETRIEVED EVIDENCE" failure rules.
        logger.warning("Retrieval failed, continuing without evidence: %s", error)
        chunks = []

    sources = _source_ids(chunks)
    if sources:
        logger.info("Query: %r -> retrieved evidence from: %s", user_message, sources)
    else:
        logger.info("Query: %r -> no supporting evidence retrieved", user_message)

    prompt_message = user_message
    if chunks:
        prompt_message = f"{format_evidence(chunks)}\n\nStudent message:\n{user_message}"

    turn = call_model(load_prompt_spec(), prompt_message)
    tools_used = _tool_executions(turn.history)
    if tools_used:
        logger.info(
            "Query: %r -> tools executed: %s",
            user_message,
            [t.name for t in tools_used],
        )

    return turn.reply, sources, tools_used


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat_endpoint(request: ChatRequest) -> ChatResponse:
    try:
        reply, sources, tools_used = ask(request.message)
        return ChatResponse(reply=reply, sources=sources, tools_used=tools_used)
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

# ANSI styling. If a terminal doesn't support color, these are just ignored
# escape codes and everything still reads fine.
_SUPPORTS_COLOR = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    if not _SUPPORTS_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


_RESET = "0"
_DIM = "2"
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
    subtitle = "Week 4 · Tool-calling assistant (RAG + tools)"
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
    """Turn the raw reply into a list of paragraphs for display.

    The model already puts one idea per line (a category line, one
    sentence per penalty/finding, a closing 'Sources:' line, etc.) --
    the old version just dropped every blank line and printed all of
    them back-to-back with zero spacing, which is what made replies look
    like one crushed block. Here every non-empty line becomes its own
    paragraph, so each one gets its own line(s) with a blank line between."""
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


def _print_agent_reply(response: str, sources: list[str], tools_used: list[ToolExecution] = None):
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
            reply, sources, tools_used = ask(message)
            print(reply)
            print(f"[sources retrieved: {', '.join(sources) if sources else 'none'}]")
            if tools_used:
                print(f"[tools executed: {', '.join(t.name for t in tools_used)}]")
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
            response, sources, tools_used = ask(user_message)
        except ModelClientError as e:
            _print_error(str(e))
            continue

        _print_agent_reply(response, sources, tools_used)


if __name__ == "__main__":
    main()
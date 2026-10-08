"""
src/main.py
-----------
Week 6: /chat routed through the bounded Orchestrator loop, with opt-in
session memory of the active case ID carried in a cookie.

Stateless by default. Sessions are OFF unless SESSIONS_ENABLED is set to
true in the environment. While they are off, /chat ignores any session
cookie, sets none, never touches the session store and creates no file --
exactly the Week 5 behaviour.

When sessions are ON:
  * the session id is read from, and written back to, an HttpOnly cookie
    named "session_id". The client never supplies or chooses an id in the
    request body; an unknown, malformed or expired cookie simply gets a
    fresh session (SessionStore.get_or_create never raises);
  * if the session already holds an active case ID it is given to the
    model as context, clearly labelled as an identifier only;
  * if this turn's tools revealed a case/ticket ID (get_case_status's
    case_id, or create_support_ticket's ticket_id) it is stored for the
    next turn. Nothing else is ever stored.

The Human-in-the-Loop gate is untouched. Approval status is never stored in
the session, and the context given to the model says so: a remembered ID is
not evidence that a ticket was approved. The model has to call the tools
to learn a case's current status.

POST /session/reset clears the active case for the caller's session. The
session itself stays valid (per the State Contract, reset != expiry).

Run:
    uvicorn main:app                       # API (run from src/)
    SESSIONS_ENABLED=true uvicorn main:app # API with session memory
    python src/main.py                     # interactive CLI (stateless)
    python src/main.py "your message"      # single-shot (stateless)
"""

import json
import logging
import os
import sys
import textwrap
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from model_client import ModelClientError
from orchestrator import run_turn
from prompt_loader import load_prompt_spec
from session_store import SESSION_EXPIRY_SECONDS, SessionStore

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("rag")

app = FastAPI(title="University Student-Support Case Agent")

SESSION_COOKIE_NAME = "session_id"


# --------------------------------------------------------------------------
# Session plumbing
# --------------------------------------------------------------------------


def sessions_enabled() -> bool:
    """Sessions must be switched on explicitly. Read at request time so the
    setting is not frozen at import."""
    return os.getenv("SESSIONS_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


_session_store: Optional[SessionStore] = None


def get_session_store() -> SessionStore:
    """Created on first use only, so a deployment that never enables
    sessions never builds a store at all."""
    global _session_store
    if _session_store is None:
        _session_store = SessionStore(
            path=os.getenv("SESSION_STORE_PATH", "data/sessions.json")
        )
    return _session_store


def _set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        max_age=SESSION_EXPIRY_SECONDS,
        httponly=True,  # not readable from page JavaScript
        samesite="lax",
        # Set SESSION_COOKIE_SECURE=true when serving over HTTPS.
        secure=os.getenv("SESSION_COOKIE_SECURE", "false").strip().lower()
        in {"1", "true", "yes", "on"},
    )


def _with_session_context(message: str, active_case_id: str) -> str:
    return (
        f"[Session context: earlier in this session the student was dealing "
        f"with case/ticket {active_case_id}. This is only a remembered "
        f"identifier. It says nothing about that case's current status or "
        f"whether staff have approved it -- use the tools to check, and "
        f"mention it only if the student's message is about it.]\n\n{message}"
    )


# --------------------------------------------------------------------------
# API models
# --------------------------------------------------------------------------


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
    stop_reason: str = "goal_satisfied"
    iterations: int = 0
    # Only populated when sessions are enabled: shows exactly what memory
    # is being kept. The session id itself travels in the cookie, not here.
    active_case_id: Optional[str] = None


def call_model(system_prompt: str, user_message: str):
    """Drive one student message through the bounded Sense/Plan/Act/
    Observe/Stop loop (src/orchestrator.py) and return the full
    TurnResult."""
    return run_turn(user_message, system_prompt=system_prompt)


def _extract_active_case_id(tools_used: List[ToolExecution]) -> Optional[str]:
    """The case/ticket identifier this turn touched, if any. Walked in
    execution order so the most recent one wins. Only successful calls
    count."""
    active_case_id = None
    for execution in tools_used:
        if execution.status != "success":
            continue
        if execution.name == "get_case_status":
            case_id = execution.result.get("case_id")
            if case_id:
                active_case_id = case_id
        elif execution.name == "create_support_ticket":
            ticket_id = execution.result.get("ticket_id")
            if ticket_id:
                active_case_id = ticket_id
    return active_case_id


def ask(user_message: str):
    """Send one student message through the bounded agent loop and return
    (reply, sources, tools_used, stop_reason, iterations). Session-free:
    chat_endpoint() wraps this with session handling when enabled."""
    turn = call_model(load_prompt_spec(), user_message)
    tools_used = [ToolExecution(**execution) for execution in turn.executions]

    if tools_used:
        logger.info(
            "Query: %r -> tools executed: %s (stop_reason=%s, iterations=%d)",
            user_message, [t.name for t in tools_used], turn.stop_reason, turn.iterations,
        )
    else:
        logger.info(
            "Query: %r -> no tools executed (stop_reason=%s, iterations=%d)",
            user_message, turn.stop_reason, turn.iterations,
        )

    return turn.reply, turn.sources, tools_used, turn.stop_reason, turn.iterations


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat_endpoint(
    request: ChatRequest, http_request: Request, response: Response
) -> ChatResponse:
    store = None
    session_id = None
    active_case_id = None
    message = request.message

    if sessions_enabled():
        store = get_session_store()
        record = store.get_or_create(http_request.cookies.get(SESSION_COOKIE_NAME))
        session_id = record["session_id"]
        active_case_id = record["active_case_id"]
        if active_case_id:
            message = _with_session_context(message, active_case_id)

    try:
        reply, sources, tools_used, stop_reason, iterations = ask(message)
    except ModelClientError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    if store is not None:
        new_case_id = _extract_active_case_id(tools_used)
        if new_case_id and new_case_id != active_case_id:
            try:
                store.set_active_case_id(session_id, new_case_id)
                active_case_id = new_case_id
            except ValueError:
                # Not a plain identifier: never remembered. The reply is
                # unaffected -- memory is a convenience, not a dependency.
                logger.warning("Ignoring case id that is not a plain identifier.")
        _set_session_cookie(response, session_id)

    return ChatResponse(
        reply=reply,
        sources=sources,
        tools_used=tools_used,
        stop_reason=stop_reason,
        iterations=iterations,
        active_case_id=active_case_id,
    )


@app.post("/session/reset")
def reset_session_endpoint(http_request: Request) -> dict[str, str]:
    """Clear the active case for the caller's session (identified by its
    cookie). The session stays valid. Idempotent, and the same response is
    returned whether or not a live session existed, so it reveals nothing
    about which ids are valid."""
    if not sessions_enabled():
        raise HTTPException(status_code=404, detail="Sessions are not enabled.")
    session_id = http_request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        get_session_store().reset(session_id)
    return {"status": "reset"}


# --------------------------------------------------------------------------
# CLI display (unchanged from Week 5; the CLI stays stateless)
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
    subtitle = "Week 6 · Bounded agent loop + opt-in session memory"
    print()
    print(_c(_BOLD + ";" + _CYAN, " " + title))
    print(_c(_GRAY, " " + subtitle))
    print(_rule("═"))
    print(_c(_GRAY, " Ask a question, check a case status, or raise a support ticket."))
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
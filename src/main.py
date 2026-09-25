"""
app.py
------
Week 4: the tool-calling Student-Support Case Agent.

One student message is driven through src/orchestrator.py's tool-calling
loop (model → parse tool calls → execute → feed results back) up to
MAX_TOOL_ROUNDS times, then the final answer is returned.

Run:
    python src/app.py                     # interactive CLI
    python src/app.py "your message here" # single-shot (used by the
                                           # evaluation script)
"""

import logging
import sys

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


class ChatResponse(BaseModel):
    reply: str
    sources: list[str] = []


def call_model(system_prompt: str, user_message: str) -> str:
    """Send one student message through the Week 4 tool-calling agent loop."""
    return run_turn(user_message, system_prompt=system_prompt).reply


def _source_ids(chunks) -> list[str]:
    """De-duplicated, order-preserving list of source documents retrieval
    actually used this turn (e.g. ['D03.txt']), for logging + API response."""
    seen = []
    for chunk in chunks:
        if chunk.source not in seen:
            seen.append(chunk.source)
    return seen


def ask(user_message: str) -> tuple[str, list[str]]:
    """Retrieve evidence for the message, ground the model call in it, and
    return (reply, source_documents_used)."""
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

    reply = call_model(load_prompt_spec(), prompt_message)
    return reply, sources


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat_endpoint(request: ChatRequest) -> ChatResponse:
    try:
        reply, sources = ask(request.message)
        return ChatResponse(reply=reply, sources=sources)
    except ModelClientError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


def main():
    if len(sys.argv) > 1:
        # single-shot mode, e.g. for scripted evaluation runs
        message = " ".join(sys.argv[1:])
        try:
            reply, sources = ask(message)
            print(reply)
            print(f"[sources retrieved: {', '.join(sources) if sources else 'none'}]")
        except ModelClientError as e:
            print(f"[ERROR] {e}")
        return

    print("Makerere Student-Support Case Agent - Week 4 (tool calling)")
    print("Type a message and press Enter. Ctrl+C to quit.\n")
    while True:
        try:
            user_message = input("Student: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye.")
            break
        if not user_message:
            print("Agent: Please type a question or request.")
            continue
        try:
            response, sources = ask(user_message)
        except ModelClientError as e:
            print(f"[ERROR] {e}")
            continue
        print(f"Agent: {response}")
        print(f"[sources retrieved: {', '.join(sources) if sources else 'none'}]\n")


if __name__ == "__main__":
    main()
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

import sys

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from model_client import ModelClientError
from orchestrator import run_turn
from prompt_loader import load_prompt_spec
from retriever import retrieve_evidence


app = FastAPI(title="University Student-Support Case Agent")


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str


def call_model(system_prompt: str, user_message: str) -> str:
    """Send one student message through the Week 4 tool-calling agent loop."""
    return run_turn(user_message, system_prompt=system_prompt).reply


def ask(user_message: str) -> str:
    evidence = retrieve_evidence(user_message)
    if evidence:
        user_message = f"{evidence}\n\nStudent message:\n{user_message}"
    return call_model(load_prompt_spec(), user_message)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat_endpoint(request: ChatRequest) -> ChatResponse:
    try:
        return ChatResponse(reply=ask(request.message))
    except ModelClientError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


def main():
    if len(sys.argv) > 1:
        # single-shot mode, e.g. for scripted evaluation runs
        message = " ".join(sys.argv[1:])
        try:
            print(ask(message))
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
            response = ask(user_message)
        except ModelClientError as e:
            print(f"[ERROR] {e}")
            continue
        print(f"Agent: {response}\n")


if __name__ == "__main__":
    main()
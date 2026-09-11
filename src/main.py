"""
app.py
------
Week 2 baseline: the smallest useful model-backed capability.

Run:
    python src/app.py                     # interactive CLI
    python src/app.py "your message here" # single-shot (used by the
                                           # evaluation script)
"""

import sys

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from model_client import chat, ModelClientError
from prompt_loader import load_prompt_spec


app = FastAPI(title="University Student-Support Case Agent")


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str


def call_model(system_prompt: str, user_message: str) -> str:
    """Send one user message through the isolated model client."""
    return chat(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
    )


def ask(user_message: str) -> str:
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

    print("Makerere Student-Support Case Agent - Week 2 baseline")
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
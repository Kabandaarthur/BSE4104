import os
import re
import time

from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

load_dotenv()

DEFAULT_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")

# Transient model-side failures worth retrying: Gemini free-tier chat is
# ~5 requests/minute and the tool-calling loop can make several calls per
# turn, so 429/503 spikes are expected and usually clear in seconds.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
CHAT_RETRY_ATTEMPTS = int(os.getenv("LLM_CHAT_RETRIES", "8"))
CHAT_RETRY_BACKOFF = 1  # seconds; doubles each attempt (capped at 30)
CHAT_RETRY_MAX_WAIT = 30
_RETRY_AFTER_RE = re.compile(r"Please retry in ([\d.]+)s")

PROVIDERS = {
    "gemini": {
        "base_url": os.getenv(
            "GEMINI_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta/openai/",
        ),
        "model": os.getenv("GEMINI_MODEL", "gemini-3.6-flash"),
        "api_key_env": "GEMINI_API_KEY",
    },
    "openai": {
        "base_url": None,
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "api_key_env": "OPENAI_API_KEY",
    },
    "ollama": {
        "base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        "model": os.getenv("OLLAMA_MODEL", "llama3.2"),
        "api_key_env": None,
    },
}


# --- Week 4: Gemini function-declaration schemas ---------------------------
# The two tools from the Week 4 Tool Catalogue (University_Student_Support_
# Case_Agent_Week4_Tool_Catalogue.pdf, Section 4). Names, required parameters
# and enums match that catalogue exactly. Because this client speaks the
# OpenAI-compatible protocol (Gemini's v1beta/openai endpoint), each tool is
# wrapped in the OpenAI `tools` wire format with lowercase JSON-Schema types
# rather than the native GenAI SDK's uppercase style; the extra constraints in
# the catalogue's input schemas (regex patterns, length bounds,
# additionalProperties: false) are enforced deterministically in
# src/tools/handlers.py, which is the same place that rejects unregistered
# parameters.

GET_CASE_STATUS_DECLARATION = {
    "type": "function",
    "function": {
        "name": "get_case_status",
        "description": (
            "Look up an existing student support case by its Case ID to get its "
            "current status, category, and official notes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "case_id": {
                    "type": "string",
                    "description": (
                        "The case ID formatted as CAS-YYYY-XXX or TCK-YYYY-XXXX "
                        "(e.g. CAS-2026-001, TCK-2026-0001)."
                    ),
                }
            },
            "required": ["case_id"],
        },
    },
}

CREATE_SUPPORT_TICKET_DECLARATION = {
    "type": "function",
    "function": {
        "name": "create_support_ticket",
        "description": (
            "Create a new support ticket for a student issue when procedural "
            "guidance cannot resolve it. Tickets with category EXAMINATION or "
            "urgency HIGH are held for staff approval."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "student_id": {
                    "type": "string",
                    "description": "The 10-digit student ID number (e.g. 2300708510).",
                },
                "category": {
                    "type": "string",
                    "enum": ["REGISTRATION", "EXAMINATION", "TIMETABLE", "GENERAL_QUERY"],
                    "description": "Problem classification category.",
                },
                "summary": {
                    "type": "string",
                    "description": "Brief one-sentence summary of the problem in the student's own words.",
                },
                "details": {
                    "type": "string",
                    "description": "Relevant specifics, course codes or problem context extracted from the student's description.",
                },
                "urgency": {
                    "type": "string",
                    "enum": ["LOW", "MEDIUM", "HIGH"],
                    "description": "Urgency level of the support request.",
                },
            },
            "required": ["student_id", "category", "summary", "details", "urgency"],
        },
    },
}

TOOLS = [GET_CASE_STATUS_DECLARATION, CREATE_SUPPORT_TICKET_DECLARATION]


class ModelClientError(RuntimeError):
    pass


def _provider_config(provider=None):
    name = (provider or DEFAULT_PROVIDER).lower()
    if name not in PROVIDERS:
        raise ModelClientError(
            f"Unknown provider '{name}'. Choose from {sorted(PROVIDERS)}."
        )
    return name, PROVIDERS[name]

def get_client(provider=None):
    name, config = _provider_config(provider)
    if name == "gemini":
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    else:
        api_key = os.getenv(config["api_key_env"]) if config["api_key_env"] else "ollama"
    if not api_key:
        raise ModelClientError(
            f"Missing {config['api_key_env']} or GOOGLE_API_KEY for provider '{name}'. "
            "Set it in .env before calling the model (see .env.example)."
        )
    return OpenAI(base_url=config["base_url"], api_key=api_key)


def chat_completion(messages, provider=None, temperature=0.4, max_tokens=None, tools=None, tool_choice="auto"):
    """Send messages to the model and return the **full** assistant message.

    Unlike chat(), this returns the message object (with `.content` and
    `.tool_calls`) so the orchestrator can parse tool requests, execute them
    and feed the results back. `tools` is the OpenAI/`v1beta/openai` tools
    list (see TOOLS above); when it is None the call is a plain text turn.
    """
    name, config = _provider_config(provider)
    client = get_client(name)
    request = {
        "model": config["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        request["tools"] = tools
        request["tool_choice"] = tool_choice

    delay = CHAT_RETRY_BACKOFF
    for attempt in range(1, CHAT_RETRY_ATTEMPTS + 1):
        try:
            response = client.chat.completions.create(**request)
            return response.choices[0].message
        except (APIStatusError, APIConnectionError, APITimeoutError) as error:
            status = getattr(error, "status_code", None)
            transient = isinstance(error, (APIConnectionError, APITimeoutError)) or status in RETRYABLE_STATUS
            if transient and attempt < CHAT_RETRY_ATTEMPTS:
                suggested = None
                body = getattr(getattr(error, "response", None), "text", "") or ""
                match = _RETRY_AFTER_RE.search(body)
                if match:
                    suggested = float(match.group(1))
                wait = min(suggested + 1 if suggested is not None else delay, CHAT_RETRY_MAX_WAIT)
                print(
                    f"[model] {type(error).__name__} (status={status}) on attempt "
                    f"{attempt}/{CHAT_RETRY_ATTEMPTS}; retrying in {wait:.0f}s",
                    flush=True,
                )
                time.sleep(wait)
                delay = min(delay * 2, CHAT_RETRY_MAX_WAIT)
                continue
            raise ModelClientError(
                f"Model request failed after {attempt} attempt(s) "
                f"(status={status}): {error}"
            ) from error


def chat(messages, provider=None, temperature=0.4, max_tokens=None):
    return chat_completion(
        messages,
        provider=provider,
        temperature=temperature,
        max_tokens=max_tokens,
    ).content

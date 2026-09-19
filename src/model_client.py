import os

from dotenv import load_dotenv
from openai import APIError, OpenAI, RateLimitError

load_dotenv()

DEFAULT_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")

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
        "model": os.getenv("OLLAMA_MODEL", "llama3.1:8b"),
        "api_key_env": None,
    },
}


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


def chat(messages, provider=None, temperature=0.4, max_tokens=None):
    name, config = _provider_config(provider)
    client = get_client(name)
    try:
        response = client.chat.completions.create(
            model=config["model"],
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except RateLimitError as error:
        raise ModelClientError(
            f"The {name} model quota is currently exhausted. "
            "Wait for the quota reset or switch to another configured provider."
        ) from error
    except APIError as error:
        raise ModelClientError(
            f"The {name} model request failed: {error}"
        ) from error
    return response.choices[0].message.content
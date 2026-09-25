"""Embedding functions for the Week 3 RAG pipeline.

The default (RAG_EMBEDDING_PROVIDER=gemini) uses the Gemini embedding API
via the GEMINI_API_KEY in .env — the same provider/key as the chat model,
per the Model Selection Note. No local model download is required.

Each function implements ChromaDB's EmbeddingFunction protocol
(__call__, name, get_config, build_from_config) so it round-trips through
the persistent collection config.

An optional ONNX fallback (RAG_EMBEDDING_PROVIDER=onnx) uses ChromaDB's
bundled all-MiniLM-L6-v2 model, which is fully local and offline but must
download ~90 MB from HuggingFace once on first use.
"""

import os
import re
import time
from typing import Dict, List

import requests
from chromadb.api.types import EmbeddingFunction, Embeddings

from dotenv import load_dotenv

load_dotenv()

DEFAULT_PROVIDER = os.getenv("RAG_EMBEDDING_PROVIDER", "gemini")
GEMINI_EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-2")
GEMINI_REST_BASE = os.getenv("GEMINI_REST_BASE", "https://generativelanguage.googleapis.com/v1beta")
BATCH_LIMIT = int(os.getenv("RAG_EMBED_BATCH", "16"))
RETRY_ATTEMPTS = int(os.getenv("RAG_EMBED_RETRIES", "6"))
RETRY_BACKOFF = 5  # seconds; doubles after each attempt (capped at 60)
# Seconds to wait between batch requests. The free Gemini tier limits
# batchEmbedContents to ~5 requests/minute, and unfired batches trip HTTP 429
# and burn retry attempts; pacing keeps a run under the quota. Set to 0 for a
# paid key or local fallback.
EMBED_PACE_SECONDS = float(os.getenv("RAG_EMBED_PACE", "13"))


class EmbeddingError(RuntimeError):
    pass


def _retryable(status_code):
    return status_code in {429, 500, 502, 503, 504}


_RETRY_AFTER_RE = re.compile(r"Please retry in ([\d.]+)s")


def _suggested_wait(response):
    """Use the server's suggested wait when provided (the API omits the
    Retry-After header but embeds 'Please retry in Xs' in the 429 body)."""
    retry_after = response.headers.get("Retry-After")
    if retry_after and retry_after.isdigit():
        return int(retry_after)
    match = _RETRY_AFTER_RE.search(response.text)
    if match:
        return float(match.group(1))
    return None


def _post_with_retry(url, **kwargs):
    """POST with backoff on transient errors (quota/5xx)."""
    delay = RETRY_BACKOFF
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        response = requests.post(url, **kwargs)
        if response.status_code == 200:
            return response
        if not _retryable(response.status_code):
            break
        suggested = _suggested_wait(response)
        wait = suggested + 1.0 if suggested is not None else delay
        wait = min(wait, 60)
        print(f"[embeddings] {response.status_code} on attempt {attempt}/{RETRY_ATTEMPTS}; "
              f"retrying in {wait:.0f}s", flush=True)
        time.sleep(wait)
        delay = min(delay * 2, 60)
    raise EmbeddingError(
        f"Gemini embeddings request failed ({response.status_code}): "
        f"{response.text[:300]}"
    )


class GeminiEmbeddingFunction(EmbeddingFunction):
    """Chroma embedding function backed by the Gemini embedContent API."""

    def __init__(self, model: str = GEMINI_EMBEDDING_MODEL, base_url: str = GEMINI_REST_BASE):
        self.model = model
        self.base_url = base_url.rstrip("/")

    def __call__(self, input: List[str]) -> Embeddings:
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise EmbeddingError(
                "GEMINI_API_KEY (or GOOGLE_API_KEY) is required for Gemini embeddings. "
                "Set it in .env (see .env.example)."
            )

        embeddings = []
        for start in range(0, len(input), BATCH_LIMIT):
            if EMBED_PACE_SECONDS > 0:
                # Sleep even before the first batch so a fresh process lets
                # the free-tier quota window refill before firing.
                time.sleep(EMBED_PACE_SECONDS)
            batch = input[start : start + BATCH_LIMIT]
            response = _post_with_retry(
                f"{self.base_url}/models/{self.model}:batchEmbedContents",
                params={"key": api_key},
                timeout=60,
                json={"requests": [self._request(text) for text in batch]},
            )
            for payload in response.json().get("embeddings", []):
                values = payload.get("values")
                if not values:
                    raise EmbeddingError(f"Gemini embeddings response missing 'values': {payload}")
                embeddings.append(values)
        return embeddings

    def _request(self, text: str) -> dict:
        return {
            "model": f"models/{self.model}",
            "content": {"parts": [{"text": text}]},
        }

    @staticmethod
    def name() -> str:
        return "gemini-embedding"

    def get_config(self) -> Dict[str, str]:
        return {"model": self.model, "base_url": self.base_url}

    @classmethod
    def build_from_config(cls, config: Dict[str, str]) -> "GeminiEmbeddingFunction":
        return cls(model=config.get("model", GEMINI_EMBEDDING_MODEL), base_url=config.get("base_url", GEMINI_REST_BASE))

    def default_space(self) -> str:
        return "cosine"


def get_embedding_function(provider: str = None) -> EmbeddingFunction:
    """Return the configured Chroma-compatible embedding function."""
    name = (provider or DEFAULT_PROVIDER).lower()
    if name == "gemini":
        return GeminiEmbeddingFunction()
    if name == "onnx":
        from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

        return ONNXMiniLM_L6_V2()
    raise EmbeddingError(
        f"Unknown embedding provider '{name}'. Choose from: gemini, onnx."
    )
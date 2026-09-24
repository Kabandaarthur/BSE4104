"""Retrieval logic for the Week 3 RAG knowledge base.

Queries the local ChromaDB index built by src/indexer.py and formats the
top results as a RETRIEVED EVIDENCE block that src/main.py attaches to the
model prompt (consumed by prompts/v2.0.md). Every returned chunk carries a
source + section heading so the model can give a citation and the agent can
stay grounded.

If no index has been built yet (or the query fails), retrieve_evidence()
returns an empty string so the app degrades gracefully to the Week 2
no-evidence behaviour instead of crashing.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from indexer import COLLECTION_NAME, DEFAULT_INDEX_DIR, _open_collection
from embeddings import get_embedding_function

load_dotenv()

DEFAULT_TOP_K = int(os.getenv("RAG_TOP_K", "3"))
RETRIEVAL_CANDIDATES = int(os.getenv("RAG_RETRIEVAL_CANDIDATES", "1000"))
MIN_QUERY_TERM_MATCHES = int(os.getenv("RAG_MIN_QUERY_TERM_MATCHES", "2"))
STOP_WORDS = {
    "a", "an", "and", "are", "can", "does", "for", "how", "i", "in",
    "is", "me", "of", "on", "say", "the", "to", "what", "when", "which",
}
TERM_ALIASES = {
    "registration": "register",
    "registrations": "register",
    "programmes": "programme",
    "programs": "programme",
    "malpractices": "malpractice",
    "penalties": "penalty",
}


@dataclass(frozen=True)
class RetrievedChunk:
    text: str
    source: str
    title: str
    kind: str
    heading: str
    chunk_index: int
    distance: float


class RetrievalError(RuntimeError):
    pass


def retrieve(query, top_k=DEFAULT_TOP_K, index_dir=DEFAULT_INDEX_DIR, embedding_function=None):
    """Return the top_k most relevant chunks for query, or [] if no index exists.

    embedding_function must match the one used to build the index (it embeds
    the query); defaults to the standard Gemini function.
    """
    index_dir = Path(index_dir)
    if not index_dir.is_dir() or not query.strip() or _is_unsupported_request(query):
        return []

    collection = _open_collection(
        index_dir, embedding_function=embedding_function or get_embedding_function()
    )

    try:
        results = collection.query(
            query_texts=[query],
            n_results=min(max(top_k, RETRIEVAL_CANDIDATES), collection.count()),
            include=["documents", "metadatas", "distances"],
        )
    except Exception as error:
        raise RetrievalError(f"Query failed: {error}") from error

    documents = (results.get("documents") or [[]])[0]
    metadatas = (results.get("metadatas") or [[]])[0]
    distances = (results.get("distances") or [[]])[0]
    if not documents:
        return []

    query_terms = _content_terms(query)
    chunks = []
    for text, meta, distance in zip(documents, metadatas, distances):
        meta = meta or {}
        chunk = RetrievedChunk(
            text=str(text),
            source=str(meta.get("source", "unknown")),
            title=str(meta.get("title", "")),
            kind=str(meta.get("kind", "unknown")),
            heading=str(meta.get("heading", "")),
            chunk_index=int(meta.get("chunk_index", -1)),
            distance=float(distance),
        )
        chunk_terms = _content_terms(
            f"{chunk.text} {chunk.title} {chunk.heading}"
        )
        if len(query_terms & chunk_terms) >= MIN_QUERY_TERM_MATCHES:
            chunks.append(chunk)

    # Short documents can rank below many broad manual chunks. Add exact
    # lexical matches from collection metadata before choosing the final top-k.
    present = {(chunk.source, chunk.chunk_index) for chunk in chunks}
    all_entries = collection.get(include=["documents", "metadatas"])
    for text, meta in zip(all_entries.get("documents", []), all_entries.get("metadatas", [])):
        meta = meta or {}
        source = str(meta.get("source", "unknown"))
        chunk_index = int(meta.get("chunk_index", -1))
        if (source, chunk_index) in present:
            continue
        chunk_terms = _content_terms(
            f"{text} {meta.get('title', '')} {meta.get('heading', '')}"
        )
        overlap = len(query_terms & chunk_terms)
        if overlap >= MIN_QUERY_TERM_MATCHES:
            chunks.append(
                RetrievedChunk(
                    text=str(text),
                    source=source,
                    title=str(meta.get("title", "")),
                    kind=str(meta.get("kind", "unknown")),
                    heading=str(meta.get("heading", "")),
                    chunk_index=chunk_index,
                    distance=1.0,
                )
            )
    chunks.sort(key=lambda chunk: (
        -len(query_terms & _content_terms(f"{chunk.title} {chunk.heading}")),
        -len(query_terms & _content_terms(chunk.text)),
        chunk.distance,
    ))
    return chunks[:top_k]


def _content_terms(text):
    return {
        TERM_ALIASES.get(term, term)
        for term in re.findall(r"[a-z0-9]+", text.lower())
        if term not in STOP_WORDS and len(term) > 2
    }


def _is_unsupported_request(query):
    """Avoid citing policy text for requests needing private data or actions."""
    lowered = query.lower()
    patterns = (
        r"\b(case|ticket)\b.*\b(status|number|id)\b|\b(status|number|id)\b.*\b(case|ticket)\b",
        r"\bwaiv(?:e|er|ed)\b.*\b(fee|tuition)\b",
        r"\bwhat grade\b|\bmy grade\b|\bgrade in\b",
        r"\b(admit|admission)\b.*\b(master|programme|program)",
        r"\b(wifi|wi-fi)\b.*\b(password|passcode)\b",
        r"\b(exam|examination)\b.*\b(clash|room)\b",
        r"\bsandwich\b.*\bfee|\bfee\b.*\bsandwich\b",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


def format_evidence(chunks):
    """Render retrieved chunks as the RETRIEVED EVIDENCE block for the prompt.

    Each entry is numbered and ends with a [source: section] marker that
    the model copies into its reply as a citation.
    """
    lines = ["RETRIEVED EVIDENCE", "------------------"]
    for index, chunk in enumerate(chunks, start=1):
        citation = _citation(chunk)
        lines.append(f"[{index}] ({citation})")
        lines.append(chunk.text.replace("\n", " "))
        lines.append("")
    return "\n".join(lines).strip()


def retrieve_evidence(query, top_k=DEFAULT_TOP_K, index_dir=DEFAULT_INDEX_DIR, embedding_function=None):
    """Convenience: retrieve() + format_evidence() with graceful degradation.

    Any retrieval failure (no index, blank query, query error) returns an
    empty string so the agent falls back to the no-evidence behaviour
    instead of failing the request.
    """
    if not query.strip():
        return ""
    try:
        chunks = retrieve(query, top_k=top_k, index_dir=index_dir, embedding_function=embedding_function)
    except RetrievalError:
        return ""
    if not chunks:
        return ""
    return format_evidence(chunks)


def _citation(chunk):
    heading = chunk.heading or chunk.title or "General"
    return f"{chunk.source}; {heading}"
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
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from indexer import COLLECTION_NAME, DEFAULT_INDEX_DIR, _open_collection
from embeddings import get_embedding_function

load_dotenv()

DEFAULT_TOP_K = int(os.getenv("RAG_TOP_K", "3"))


@dataclass(frozen=True)
class RetrievedChunk:
    text: str
    source: str
    title: str
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
    if not index_dir.is_dir() or not query.strip():
        return []

    collection = _open_collection(
        index_dir, embedding_function=embedding_function or get_embedding_function()
    )

    try:
        results = collection.query(
            query_texts=[query],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as error:
        raise RetrievalError(f"Query failed: {error}") from error

    documents = (results.get("documents") or [[]])[0]
    metadatas = (results.get("metadatas") or [[]])[0]
    distances = (results.get("distances") or [[]])[0]
    if not documents:
        return []

    chunks = []
    for text, meta, distance in zip(documents, metadatas, distances):
        meta = meta or {}
        chunks.append(
            RetrievedChunk(
                text=str(text),
                source=str(meta.get("source", "unknown")),
                title=str(meta.get("title", "")),
                heading=str(meta.get("heading", "")),
                chunk_index=int(meta.get("chunk_index", -1)),
                distance=float(distance),
            )
        )
    return chunks


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
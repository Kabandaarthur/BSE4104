"""Chunking pipeline for the Week 3 RAG knowledge base.

Consumes the real corpus produced by src/fetch_corpus.py: plain-text
extractions under knowledge/text/{doc_id}.txt (from the raw PDFs/HTML under
knowledge/raw/*), with titles and real/synthetic provenance read from
knowledge/corpus.json. Each chunk is later embedded and stored by
src/indexer.py.

Chunking is structural: it splits on markdown headings (synthetic docs) and
on the [[page:N]] markers that fetch_corpus.py keeps in PDF extractions (so a
retrieved chunk can be cited as "D03.txt; Page 4"), then packs paragraphs
into chunks of roughly CHUNK_SIZE characters, carrying a small OVERLAP of
text from the previous chunk.

This module is pure Python (no ChromaDB import) so the chunking logic can be
unit-tested offline. Config comes from .env (see .env.example) with sane
defaults, mirroring the pattern in src/model_client.py.
"""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS_DIR = Path(os.getenv("RAG_CORPUS_DIR", str(ROOT / "knowledge" / "text")))
MANIFEST = ROOT / "knowledge" / "corpus.json"
CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "800"))
CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "80"))

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
PAGE_MARKER_RE = re.compile(r"^\[\[page:(\d+)\]\]$")
_QUOTE_PREFIX = re.compile(r"^\s*>\s?")


@dataclass(frozen=True)
class Document:
    source: str
    title: str
    kind: str
    text: str


@dataclass(frozen=True)
class Chunk:
    source: str
    title: str
    kind: str
    heading: str
    chunk_index: int
    text: str


class ChunkerError(RuntimeError):
    pass


def _manifest_title_map():
    """Map doc_id -> {title, kind} from knowledge/corpus.json (best-effort)."""
    if not MANIFEST.is_file():
        return {}
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {
        doc["doc_id"]: {
            "title": doc.get("title") or "",
            "kind": doc.get("kind") or "unknown",
        }
        for doc in manifest.get("documents", [])
        if doc.get("status") == "active"
    }


def load_documents(corpus_dir=DEFAULT_CORPUS_DIR):
    """Read every .txt file in corpus_dir as one Document each."""
    corpus_dir = Path(corpus_dir)
    if not corpus_dir.is_dir():
        raise ChunkerError(f"Corpus directory not found: {corpus_dir}")

    titles = _manifest_title_map()
    documents = []
    for path in sorted(corpus_dir.glob("*.txt")):
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        doc_id = path.stem
        meta = titles.get(doc_id, {})
        title = meta.get("title") or _fallback_title(path)
        documents.append(
            Document(
                source=path.name,
                title=title,
                kind=meta.get("kind") or "unknown",
                text=text,
            )
        )
    return documents


def _fallback_title(path):
    return path.stem.replace("-", " ")


def _strip_quote_markers(line):
    return _QUOTE_PREFIX.sub("", line)


def chunk_document(document, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Split one Document into ordered, overlapping Chunks.

    The document is cut into sections first: each markdown heading or
    [[page:N]] marker starts a new section (titled "Page N" for PDFs). The
    text under each heading is then packed paragraph-by-paragraph into chunks
    no longer than chunk_size characters. Each chunk after the first in a
    section starts with `overlap` trailing characters of the previous chunk so
    section context is not lost across the boundary.
    """
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ChunkerError(
            f"Invalid chunk limits: size={chunk_size}, overlap={overlap}. "
            "Expected 0 <= overlap < chunk_size."
        )

    sections = _split_into_sections(document.text)

    chunks = []
    for heading, body in sections:
        paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]

        expanded = []
        for paragraph in paragraphs:
            if len(paragraph) > chunk_size:
                expanded.extend(_split_long_paragraph(paragraph, chunk_size))
            else:
                expanded.append(paragraph)
        paragraphs = expanded

        current_heading = heading or document.title
        chunk_text = ""
        for paragraph in paragraphs:
            if not chunk_text:
                chunk_text = paragraph
                continue
            joined = chunk_text + "\n\n" + paragraph
            if len(joined) <= chunk_size:
                chunk_text = joined
            else:
                chunks.append(
                    Chunk(
                        source=document.source,
                        title=document.title,
                        kind=document.kind,
                        heading=current_heading,
                        chunk_index=len(chunks),
                        text=chunk_text,
                    )
                )
                tail = chunk_text[-overlap:] if overlap else ""
                chunk_text = f"{tail}\n\n{paragraph}".strip()
        if chunk_text:
            chunks.append(
                Chunk(
                    source=document.source,
                    title=document.title,
                    kind=document.kind,
                    heading=current_heading,
                    chunk_index=len(chunks),
                    text=chunk_text,
                )
            )
    return chunks


def chunk_corpus(corpus_dir=DEFAULT_CORPUS_DIR, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Chunk every document in corpus_dir and return (chunks, per_source_counts)."""
    per_source = {}
    chunks = []
    for document in load_documents(corpus_dir):
        doc_chunks = chunk_document(document, chunk_size=chunk_size, overlap=overlap)
        chunks.extend(doc_chunks)
        per_source[document.source] = len(doc_chunks)
    return chunks, per_source


def _split_into_sections(text):
    """Return a list of (heading, body) for a document's text.

    A section starts at a markdown heading (synthetic docs) or a
    [[page:N]] marker (PDF extractions). Text before the first marker is
    attached to the first found section; if there are none, the whole
    document is one section titled None (callers fall back to the title).
    """
    lines = text.splitlines()
    sections = []
    current_heading = None
    current_lines = []

    def flush():
        if current_heading is not None and any(l.strip() for l in current_lines):
            sections.append((current_heading, "\n".join(current_lines)))

    for line in lines:
        page = PAGE_MARKER_RE.match(line)
        match = HEADING_RE.match(line)
        if page:
            flush()
            current_heading = f"Page {page.group(1)}"
            current_lines = []
        elif match:
            flush()
            current_heading = _strip_quote_markers(match.group(2).strip())
            current_lines = []
        else:
            current_lines.append(_strip_quote_markers(line))
    flush()

    if not sections:
        sections.append((None, "\n".join(_strip_quote_markers(line) for line in lines)))
    return sections


def _split_long_paragraph(paragraph, chunk_size):
    """Naively split a single over-long paragraph into pieces at most chunk_size long."""
    pieces = []
    piece = paragraph
    while len(piece) > chunk_size:
        cut = piece.rfind(". ", 0, chunk_size)
        cut = cut if cut > 0 else chunk_size
        pieces.append(piece[: cut + 1].strip())
        piece = piece[cut + 1:].strip()
    pieces.append(piece)
    return pieces
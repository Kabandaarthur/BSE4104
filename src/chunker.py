"""Chunking pipeline for the Week 3 RAG knowledge base.

Splits each corpus document (knowledge/corpus/*.md) into self-contained
chunks that are later embedded and stored by src/indexer.py. Chunking is
structural: it first splits on markdown headings, then packs paragraphs
into chunks of roughly CHUNK_SIZE characters, carrying a small OVERLAP of
text from the previous chunk so a retrieval hit keeps its surrounding
context.

This module is pure Python (no ChromaDB import) so the chunking logic can
be unit-tested offline. Config comes from .env (see .env.example) with
sane defaults, mirroring the pattern in src/model_client.py.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_CORPUS_DIR = Path(__file__).resolve().parent.parent / "knowledge" / "corpus"
CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "800"))
CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "80"))

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_QUOTE_PREFIX = re.compile(r"^\s*>\s?")


@dataclass(frozen=True)
class Document:
    source: str
    title: str
    text: str


@dataclass(frozen=True)
class Chunk:
    source: str
    title: str
    heading: str
    chunk_index: int
    text: str


class ChunkerError(RuntimeError):
    pass


def load_documents(corpus_dir=DEFAULT_CORPUS_DIR):
    """Read every .md file in corpus_dir as one Document each."""
    corpus_dir = Path(corpus_dir)
    if not corpus_dir.is_dir():
        raise ChunkerError(f"Corpus directory not found: {corpus_dir}")

    documents = []
    for path in sorted(corpus_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        title = _document_title(text, path)
        documents.append(Document(source=path.name, title=title, text=text))
    return documents


def _document_title(text, path):
    match = HEADING_RE.search(text)
    return match.group(2).strip() if match else path.stem.replace("-", " ").title()


def _strip_quote_markers(line):
    return _QUOTE_PREFIX.sub("", line)


def chunk_document(document, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Split one Document into ordered, overlapping Chunks.

    The document is cut on markdown headings first; the text under each
    heading is then packed paragraph-by-paragraph into chunks no longer
    than chunk_size characters. Each chunk after the first in a section
    starts with `overlap` trailing characters of the previous chunk so
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
            if len(paragraph) > chunk_size * 1.5:
                expanded.extend(_split_long_paragraph(paragraph, chunk_size))
            else:
                expanded.append(paragraph)
        paragraphs = expanded

        current_heading = heading or document.title
        chunk_text = ""
        tail = ""
        for paragraph in paragraphs:
            candidate = f"{tail}\n\n{paragraph}".strip() if tail else paragraph
            if len(candidate) <= chunk_size:
                chunk_text = candidate
            else:
                if chunk_text:
                    chunks.append(
                        Chunk(
                            source=document.source,
                            title=document.title,
                            heading=current_heading,
                            chunk_index=len(chunks),
                            text=chunk_text,
                        )
                    )
                chunk_text = paragraph
            tail = chunk_text[-overlap:] if overlap else ""
        if chunk_text:
            chunks.append(
                Chunk(
                    source=document.source,
                    title=document.title,
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
    """Return a list of (heading, body) for a document's markdown text.

    Text before the first heading is attached to the first found heading;
    if there are no headings the whole document is one section.
    """
    lines = text.splitlines()
    sections = []
    current_heading = None
    current_lines = []

    def flush():
        if current_heading is not None and [l for l in current_lines if l.strip()]:
            sections.append((current_heading, "\n".join(current_lines)))

    for line in lines:
        match = HEADING_RE.match(line)
        if match:
            flush()
            current_heading = _strip_quote_markers(match.group(2).strip())
            current_lines = []
        else:
            current_lines.append(_strip_quote_markers(line))
    flush()

    if not sections:
        sections.append((None, text))
    return sections


def _split_long_paragraph(paragraph, chunk_size):
    """Naively split a single over-long paragraph into smaller pieces on sentence-ish boundaries."""
    pieces = []
    while len(paragraph) > chunk_size * 1.5:
        cut = paragraph.rfind(". ", 0, chunk_size)
        cut = cut if cut > 0 else chunk_size
        pieces.append(paragraph[: cut + 1])
        paragraph = paragraph[cut + 1:].strip()
    pieces.append(paragraph)
    return pieces
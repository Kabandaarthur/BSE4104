"""
Week 3 RAG tests — chunking pipeline and retrieval logic.

Covers:
- src/chunker.py: document loading, structural chunking, chunk limits, metadata
- src/indexer.py: building a local ChromaDB index from the synthetic corpus
- src/retriever.py: query round-trip, RETRIEVED EVIDENCE formatting, graceful
  degradation when no index has been built

Run with:  pytest tests/test_retrieval.py -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from chunker import Chunk, chunk_corpus, load_documents
from chromadb.api.types import EmbeddingFunction
from indexer import build_index, index_status
from retriever import format_evidence, retrieve, retrieve_evidence

CORPUS_DIR = Path(__file__).resolve().parent.parent / "knowledge" / "corpus"


class StubEmbeddingFunction(EmbeddingFunction):
    """Deterministic, network-free stand-in for the Gemini embedder.

    A small count-vectorizer over word hashes, so retrieval in tests is
    lexical and meaningful without touching the API.
    """

    DIM = 128

    def __init__(self):
        self.dim = self.DIM

    def __call__(self, input):
        import hashlib
        import re

        vectors = []
        for text in input:
            vector = [0.0] * self.dim
            for token in re.findall(r"[a-z']+", text.lower()):
                index = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % self.dim
                vector[index] += 1.0
            norm = sum(value * value for value in vector) ** 0.5 or 1.0
            vectors.append([value / norm for value in vector])
        return vectors

    @staticmethod
    def name() -> str:
        return "test-stub-embedding"

    def get_config(self):
        return {"dim": self.dim}

    @classmethod
    def build_from_config(cls, config):
        instance = cls()
        instance.dim = config.get("dim", cls.DIM)
        return instance

    def default_space(self) -> str:
        return "cosine"


@pytest.fixture()
def embedder():
    return StubEmbeddingFunction()


@pytest.fixture()
def documents():
    return load_documents(CORPUS_DIR)


@pytest.fixture()
def corpus_chunks():
    chunks, _ = chunk_corpus(CORPUS_DIR)
    return chunks


def test_corpus_has_four_public_documents(documents):
    assert len(documents) == 4
    assert all(doc.source.endswith(".md") for doc in documents)


def test_chunks_are_well_formed(corpus_chunks):
    assert corpus_chunks, "corpus produced no chunks"
    for chunk in corpus_chunks:
        assert isinstance(chunk, Chunk)
        assert chunk.text.strip(), f"{chunk} has empty text"
        assert chunk.source.endswith(".md")
        assert chunk.title, "chunk missing title"
        assert chunk.heading, "chunk missing section heading"
        assert chunk.chunk_index >= 0


def test_chunk_size_bounds(corpus_chunks):
    size = 800
    for chunk in corpus_chunks:
        assert len(chunk.text) <= size * 1.5, (
            f"{chunk.source}[{chunk.chunk_index}] exceeds chunk limit: {len(chunk.text)}"
        )


def test_chunks_keep_metadata_and_are_ordered(corpus_chunks):
    for source in {c.source for c in corpus_chunks}:
        indexes = [c.chunk_index for c in corpus_chunks if c.source == source]
        assert indexes == sorted(indexes), f"{source} chunk_index not sequential"


def test_retrieval_round_trip(tmp_path, embedder):
    report = build_index(index_dir=tmp_path, embedding_function=embedder)
    assert report["status"] == "built"
    assert report["total"] >= 4

    hits = retrieve("Can I still register two weeks late?", top_k=2, index_dir=tmp_path, embedding_function=embedder)
    assert hits, "retrieval returned no hits"
    assert hits[0].source == "01-registration.md"  # late registration doc
    assert "Late registration" in hits[0].text

    evidence = format_evidence(hits)
    assert evidence.startswith("RETRIEVED EVIDENCE")
    assert "(01-registration.md;" in evidence


def test_retrieve_evidence_end_to_end(tmp_path, embedder):
    build_index(index_dir=tmp_path, embedding_function=embedder)
    evidence = retrieve_evidence("how do I apply for a retake", top_k=1, index_dir=tmp_path, embedding_function=embedder)
    assert "RETRIEVED EVIDENCE" in evidence
    assert "02-retakes-and-course-repeat.md" in evidence
    assert "retake" in evidence


def test_retrieve_evidence_degrades_gracefully(tmp_path):
    empty_dir = tmp_path / "no-index"
    empty_dir.mkdir()
    assert retrieve_evidence("any question", index_dir=empty_dir) == ""


def test_index_status_reports_built_index(tmp_path, embedder):
    assert index_status(index_dir=tmp_path) is None
    build_index(index_dir=tmp_path, embedding_function=embedder)
    status = index_status(index_dir=tmp_path)
    assert status is not None
    assert status["total"] > 0
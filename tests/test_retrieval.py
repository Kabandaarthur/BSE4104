"""
Week 3 RAG tests — chunking pipeline and retrieval logic.

Covers:
- src/chunker.py: document loading, structural chunking, chunk limits, metadata
- src/indexer.py: building a local ChromaDB index from the real corpus
  (knowledge/text/*, extracted from the PDFs/HTML in knowledge/raw)
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

CORPUS_DIR = Path(__file__).resolve().parent.parent / "knowledge" / "text"


class StubEmbeddingFunction(EmbeddingFunction):
    """Deterministic, network-free stand-in for the Gemini embedder.

    A document-level TF-IDF count-vectorizer: rare terms get high weight, so
    retrieval in tests is lexical and meaningful even against the large
    Academic Policies Manual (D11) without touching the API.
    """

    DIM = 512

    def __init__(self, corpus_dir=CORPUS_DIR):
        self.dim = self.DIM
        self.idf = self._build_idf(corpus_dir)

    def _build_idf(self, corpus_dir):
        import hashlib
        import math
        import re
        from collections import Counter

        docs = []
        document_freq = Counter()
        for path in Path(corpus_dir).glob("*.txt"):
            tokens = set(re.findall(r"[a-z']+", path.read_text(encoding="utf-8").lower()))
            docs.append(tokens)
            for token in tokens:
                document_freq[token] += 1
        total = max(len(docs), 1)
        return {
            token: math.log((total + 1) / (frequency + 1)) + 1.0
            for token, frequency in document_freq.items()
        }

    def __call__(self, input):
        import hashlib
        import re

        vectors = []
        for text in input:
            vector = [0.0] * self.dim
            for token in re.findall(r"[a-z']+", text.lower()):
                index = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % self.dim
                vector[index] += self.idf.get(token, 1.0)
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


def test_corpus_is_the_real_fetched_corpus(documents):
    assert len(documents) == 11  # 9 real + 2 synthetic (from corpus.json)
    assert all(doc.source.endswith(".txt") for doc in documents)
    assert all(doc.title for doc in documents), "every doc must have a manifest title"
    kinds = {doc.kind for doc in documents}
    assert kinds == {"real", "synthetic"}


def test_chunks_are_well_formed(corpus_chunks):
    assert corpus_chunks, "corpus produced no chunks"
    for chunk in corpus_chunks:
        assert isinstance(chunk, Chunk)
        assert chunk.text.strip(), f"{chunk} has empty text"
        assert chunk.source.endswith(".txt")
        assert chunk.title, "chunk missing title"
        assert chunk.heading, "chunk missing section heading"
        assert chunk.kind in {"real", "synthetic"}
        assert chunk.chunk_index >= 0


def test_pdf_chunks_carry_page_citations(corpus_chunks):
    for chunk in corpus_chunks:
        if chunk.source == "D03.txt":
            assert chunk.heading.startswith("Page ")  # PDF page markers survive


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
    corpus = tmp_path / "tiny"
    corpus.mkdir()
    (corpus / "aaa-registration.txt").write_text(
        "Late registration closes two weeks after the normal registration window. "
        "The late registration fee is applied on top of the normal tuition.\n\n"
        "Students who miss both windows may apply to their college office for special consideration."
    )
    (corpus / "bbb-fees.txt").write_text(
        "Tuition fees are paid per semester. Private students pay higher fees than "
        "government-sponsored students. Fees are non-refundable after the first week."
    )
    report = build_index(
        index_dir=tmp_path / "idx",
        corpus_dir=corpus,
        embedding_function=embedder,
    )
    assert report["status"] == "built"
    assert report["total"] >= 2

    hits = retrieve(
        "Can I still register two weeks late?",
        top_k=2,
        index_dir=tmp_path / "idx",
        embedding_function=embedder,
    )
    assert hits, "retrieval returned no hits"
    assert hits[0].source == "aaa-registration.txt"
    assert "Late registration" in hits[0].text

    evidence = format_evidence(hits)
    assert evidence.startswith("RETRIEVED EVIDENCE")
    assert "(aaa-registration.txt" in evidence


def test_full_corpus_retrieval_returns_relevant_evidence(tmp_path, embedder):
    report = build_index(index_dir=tmp_path, embedding_function=embedder)
    assert report["status"] == "built"
    assert report["total"] >= 11

    hits = retrieve(
        "rules on examination malpractices and penalties",
        top_k=3,
        index_dir=tmp_path,
        embedding_function=embedder,
    )
    assert hits, "retrieval returned no hits"
    plumbing_ok = any(
        "malpract" in hit.text.lower() for hit in hits
    )
    assert plumbing_ok, "no retrieved chunk mentions malpractice"

    evidence = format_evidence(hits)
    assert evidence.startswith("RETRIEVED EVIDENCE")
    assert "(" in evidence and ";" in evidence


def test_retrieve_evidence_end_to_end(tmp_path, embedder):
    corpus = tmp_path / "tiny"
    corpus.mkdir()
    (corpus / "aaa-registration.txt").write_text(
        "Late registration closes two weeks after the normal registration window."
    )
    (corpus / "bbb-fees.txt").write_text(
        "Tuition fees are paid per semester for both private and government students."
    )
    build_index(
        index_dir=tmp_path / "idx",
        corpus_dir=corpus,
        embedding_function=embedder,
    )
    evidence = retrieve_evidence(
        "how do I register late",
        top_k=1,
        index_dir=tmp_path / "idx",
        embedding_function=embedder,
    )
    assert "RETRIEVED EVIDENCE" in evidence
    assert "aaa-registration.txt" in evidence
    assert "late registration" in evidence.lower()


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
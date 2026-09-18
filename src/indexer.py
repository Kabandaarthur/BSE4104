"""Indexing pipeline for the Week 3 RAG knowledge base.

Embeds the chunks produced by src/chunker.py and stores them in a local
ChromaDB persistent index so the agent can retrieve evidence later.

Embeddings use the Gemini API by default (src/embeddings.py, GEMINI_API_KEY
in .env) — the same provider as the chat model, with no local model to
download. An ONNX all-MiniLM-L6-v2 fallback is available offline via
RAG_EMBEDDING_PROVIDER=onnx.

Run the pipeline with:
    python src/indexer.py --force   # build the index (Gemini embeddings)
    python src/indexer.py --status  # report what the index currently holds
"""

import argparse
from pathlib import Path

from dotenv import load_dotenv

from chunker import CHUNK_OVERLAP, CHUNK_SIZE, ChunkerError, chunk_corpus
from embeddings import get_embedding_function

load_dotenv()

DEFAULT_INDEX_DIR = Path(__file__).resolve().parent.parent / "knowledge" / "index"
COLLECTION_NAME = "student_support_knowledge"


class IndexError(RuntimeError):
    pass


def _chunk_id(chunk):
    return f"{chunk.source}::chunk-{chunk.chunk_index:03d}"


def _open_collection(index_dir, reset=False, embedding_function=None):
    import chromadb

    index_dir = Path(index_dir)
    index_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(index_dir))
    if reset:
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
    kwargs = {
        "name": COLLECTION_NAME,
        "metadata": {"hnsw:space": "cosine"},
    }
    if embedding_function is not None:
        kwargs["embedding_function"] = embedding_function
    return client.get_or_create_collection(**kwargs)


def build_index(
    index_dir=DEFAULT_INDEX_DIR,
    force=False,
    chunk_size=CHUNK_SIZE,
    overlap=CHUNK_OVERLAP,
    embedding_function=None,
):
    """Chunk the corpus, embed it, and persist it to a local ChromaDB index.

    Returns a small report dict: {"status", "sources", "chunks", "total"}.
    An existing (non-empty) index is left untouched unless force=True.
    embedding_function defaults to Gemini (src/embeddings.py); pass a stub
    to build the index without network/API access (used in tests).
    """
    collection = _open_collection(
        index_dir,
        reset=force,
        embedding_function=embedding_function or get_embedding_function(),
    )

    try:
        chunks, per_source = chunk_corpus(chunk_size=chunk_size, overlap=overlap)
    except ChunkerError as error:
        raise IndexError(str(error)) from error

    if not chunks:
        raise IndexError("No chunks produced — is the corpus empty?")

    if collection.count() > 0 and not force:
        return {
            "status": "already-built",
            "sources": sorted(per_source),
            "chunks": per_source,
            "total": collection.count(),
        }

    ids = [_chunk_id(chunk) for chunk in chunks]
    documents = [chunk.text for chunk in chunks]
    metadatas = [
        {
            "source": chunk.source,
            "title": chunk.title,
            "heading": chunk.heading,
            "chunk_index": chunk.chunk_index,
        }
        for chunk in chunks
    ]
    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)

    return {
        "status": "built",
        "sources": sorted(per_source),
        "chunks": per_source,
        "total": len(chunks),
    }


def index_status(index_dir=DEFAULT_INDEX_DIR):
    """Return metadata about the current index, or None if no index is built.

    Read-only: never creates a collection (creating one would persist a
    default embedding function and break later rebuilds).
    """
    index_dir = Path(index_dir)
    if not index_dir.is_dir():
        return None
    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(index_dir))
        collection = client.get_collection(COLLECTION_NAME)
    except Exception:
        return None
    count = collection.count()
    if count == 0:
        return None
    return {"status": "built", "total": count, "collection": COLLECTION_NAME}


def main():
    parser = argparse.ArgumentParser(description="Build the Student-Support RAG index.")
    parser.add_argument("--force", action="store_true", help="wipe and rebuild the index")
    parser.add_argument("--status", action="store_true", help="show index status and exit")
    parser.add_argument("--index-dir", default=str(DEFAULT_INDEX_DIR), help="ChromaDB store location")
    args = parser.parse_args()

    if args.status:
        report = index_status(args.index_dir)
        if report is None:
            print("No index built yet. Run: python src/indexer.py")
        else:
            print(f"Index ready: {report['total']} chunks in '{report['collection']}'")
        return

    try:
        report = build_index(args.index_dir, force=args.force)
    except IndexError as error:
        print(f"[ERROR] {error}")
        raise SystemExit(1)

    print(f"Index {report['status']} at {args.index_dir}")
    print(f"Total chunks indexed: {report['total']}")
    for source, count in report["chunks"].items():
        print(f"  {source}: {count} chunks")
    if report["status"] == "already-built":
        print("Use --force to rebuild.")


if __name__ == "__main__":
    main()
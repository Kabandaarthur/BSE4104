# Week 3 RAG Implementation — Work Log

**Date:** 17 September 2026
**Scope:** RAG retrieval logic (chunking + indexing pipeline + retrieval) and Prompt Specification v2.0
**Branch:** `main`
**Status:** Implemented, tested (19/19 passing), verified live against the real model

---

## 1. What was built

Three new `src/` modules plus a redesigned prompt version, wired into the
existing Week 2 app:

| Module | Purpose |
|---|---|
| `src/chunker.py` | Loads `knowledge/corpus/*.md`, splits each document on markdown headings, then packs paragraphs into chunks (default 800 chars, 80-char overlap). Each chunk keeps `source`, `title`, `heading` metadata for citation. Pure Python — no ChromaDB dependency, unit-testable offline. |
| `src/embeddings.py` | Embedding functions for ChromaDB. Default **Gemini** (`gemini-embedding-2`, 3072-dim, uses the existing `GEMINI_API_KEY` in `.env` — same provider/key as the chat model). Optional local ONNX fallback (`RAG_EMBEDDING_PROVIDER=onnx`, all-MiniLM-L6-v2). Implements Chroma's `EmbeddingFunction` protocol (`__call__`, `name`, `get_config`, `build_from_config`) so it round-trips through the persistent collection config. |
| `src/indexer.py` | The indexing pipeline. Embeds chunks and stores them in a local ChromaDB persistent collection (`knowledge/index/`, gitignored, rebuilt from corpus). CLI: `python src/indexer.py --force` to build, `--status` to inspect. |
| `src/retriever.py` | Retrieval logic. Queries the index, returns top-k chunks, and formats them as a `RETRIEVED EVIDENCE` block with `(source; section)` citation markers. `retrieve_evidence()` degrades gracefully (returns `""`) when there is no index or the query fails/blank — the app falls back to Week 2 no-evidence behaviour instead of erroring. |

### Files changed (existing)

| File | Change |
|---|---|
| `src/main.py` | `ask()` now retrieves evidence for each message and attaches it to the prompt as a `RETRIEVED EVIDENCE` block before calling the model. |
| `src/prompt_loader.py` | Default prompt version bumped to `v2.0`. |
| `prompts/v2.0.md` | **New prompt spec** (see Section 3). |
| `knowledge/corpus/` | New synthetic public corpus (4 docs: registration, retakes, course add/drop, exams) — non-binding reference data, marked for replacement with official sources in Week 8. |
| `knowledge/metadata/sources.csv` | Provenance record for each corpus document (source, title, status, date, notes). |
| `tests/test_retrieval.py` | New tests for the chunker, indexer and retriever (see Section 4). |
| `tests/test_evaluation.py` | Mock harness now matches the student's message (strips the evidence prefix). |
| `requirements.txt` | Added `chromadb==1.5.9`, `requests`. |
| `.env.example` | Documented RAG vars: `RAG_EMBEDDING_PROVIDER`, `GEMINI_EMBEDDING_MODEL`, `RAG_TOP_K`, `RAG_CHUNK_SIZE`, `RAG_CHUNK_OVERLAP`; `PROMPT_VERSION` default updated to v2.0. |
| `.gitignore` | Added `knowledge/index/` (vector index is a build artifact). |
| `README.md` | Tech-stack row updated (ChromaDB + Gemini embeddings decided); Quick Start now includes the index-build step. |

---

## 2. Data flow

```
knowledge/corpus/*.md
        │  src/chunker.py   (read + structural chunking, 800/80)
        ▼
   list[Chunk]              (source, title, heading, text)
        │  src/indexer.py   (embeds via src/embeddings.py -> Gemini)
        ▼
   ChromaDB  knowledge/index/   (collection: student_support_knowledge, cosine)
        ▲
        │  src/retriever.py (embed query -> top_k -> format)
        │
        ▼
   RETRIEVED EVIDENCE block
        │  src/main.py      (prefixed to student message)
        ▼
   prompts/v2.0.md system prompt  →  model answer with citations + Sources:
```

---

## 3. Prompt Specification v2.0 (`prompts/v2.0.md`)

Keeps v1.0's role, 5-way category classification, constraints C1–C8 and
failure behaviour, and adds:

- **RETRIEVED EVIDENCE is now attached** for knowledge questions; answers
  must be grounded on it and not on general knowledge.
- **New constraint C9** — every grounded claim carries the exact `(source;
  section)` citation marker from its evidence chunk, inline after the
  sentence. No invented or reformatted markers.
- **`Sources:` output requirement** — the reply ends with a compact list of
  every marker used (parseable for Week 7 guardrails).
- Updated failure behaviour: procedure questions are answered from evidence
  when present, and only fall back to "documents not available" when no
  evidence was retrieved.
- v1.0 → v2.0 changelog row and a Week 3 RAG test map (R1–R15).

---

## 4. Testing & verification

- `pytest tests/` → **19 passed** (health, Week 2 eval in mock mode, retrieval suite).
- Retrieval tests are **hermetic**: they inject a deterministic stub embedding
  function (lexical count-vector) so CI never needs the API. The real Gemini
  path is verified separately via the CLI.
- Live verification with the real Gemini embedder + chat model:

```
QUERY: Can I still register two weeks late?
→ Category: knowledge_question
  "…may still register for up to two weeks after the closing date
   (01-registration.md; Late registration). …"
  Sources: (01-registration.md; Late registration); (01-registration.md; Where to get help with registration)
```

- Retrieval quality spot-checks passed for registration, retakes, add/drop,
  and exam-clash questions (correct document retrieved first for each).
- Real index built today: **24 chunks** across the 4 synthetic corpus docs.

---

## 5. How to run it

```bash
pip install -r requirements.txt        # includes chromadb + requests
python src/indexer.py --force          # build the index (uses GEMINI_API_KEY)
python src/indexer.py --status         # inspect the index
python src/main.py "Can I still register two weeks late?"   # single-shot
python src/main.py                     # interactive CLI
```

Env knobs (`.env.example`): `RAG_TOP_K` (default 3), `RAG_CHUNK_SIZE` (800),
`RAG_CHUNK_OVERLAP` (80), `RAG_EMBEDDING_PROVIDER` (gemini), `GEMINI_EMBEDDING_MODEL`.

---

## 6. Decisions & notes

- **Embeddings = Gemini** (`gemini-embedding-2`), not a local model — same
  provider/key as the chat model, no model download, per the Model Selection
  Note. HuggingFace was unreachable from this sandbox, which also ruled out
  Chroma's bundled ONNX MiniLM as the default (kept as an optional fallback).
- **Synthetic corpus only**: `knowledge/` must not hold real/restricted
  student data (README rule). All 4 corpus docs are marked synthetic and
  non-binding, to be replaced with official Handbook sources during Week 8
  hardening.
- **ChromiDB constraints**: custom embedding functions must implement
  `name`/`get_config`/`build_from_config` (ChromaDB 1.5.x enforces this);
  opening a collection with a conflicting embedding function raises — the
  indexer/retriever always reuse the index's own embedding function.
- **Eval harness fix**: the Week 2 mock now evaluates the *student message*
  after stripping the RAG evidence prefix.
- Index location `knowledge/index/` is gitignored (rebuildable artifact).

---

## 7. Next steps (not done today)

- **15-case RAG evaluation runner** (R1–R15 from `prompts/v2.0.md` Section 3)
  and the Week 3 evaluation table in `docs/evaluation/`.
- Replace synthetic corpus with official/public Handbook sources.
- Week 4: tools/function calling (case lookup + ticket creation) and the
  `TOOL RESULTS` handling already reserved in the v2.0 prompt.
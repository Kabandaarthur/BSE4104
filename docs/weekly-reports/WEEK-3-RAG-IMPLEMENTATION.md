# Week 3 RAG — Personal Contribution Log

**Contributor:** <ARIKO SOSSY JOEL> *(AI Engineering / Application Integration)*
**Date:** 18 September 2026
**Branch:** `rag` (pushed to `origin/rag`)
**Status:** Implemented, tested (21/21 passing), verified live against the real model

> **AI-use disclosure (per README §7):** code was drafted and executed with
> opencode (Claude-style assistant) under my direction. I made the design
> decisions below, reviewed and tested every change before it was committed,
> and can explain each part. The final v2.0 prompt and the evidence/citation
> rules are my own engineering decisions.

---

## 1. My contribution this week

1. **Chose the embedding approach.** Decided the RAG index uses **Gemini
   embeddings** (`gemini-embedding-2`, 3072-dim) via the existing
   `GEMINI_API_KEY` in `.env` — the same provider and key as the chat model
   (Model Selection Note). Rejected a local ONNX model (would need a ~90 MB
   download and split the stack from the chat provider). Kept the ONNX
   option as an opt-in fallback (`RAG_EMBEDDING_PROVIDER=onnx`).
2. **Designed and directed the RAG pipeline**, wiring it into the Week 2 app:
   - Chunking: structural split on markdown headings and `[[page:N]]` page
     markers from PDFs, then paragraph packing (default 800 chars, 80-char
     overlap) with `source`/`section`/`kind` metadata.
   - Indexing: `src/indexer.py` embeds chunks and persists them to local
     ChromaDB (`knowledge/index/`), rebuildable, gitignored.
   - Retrieval: `src/retriever.py` returns top-k chunks formatted as a
     `RETRIEVED EVIDENCE` block with `(source; section)` markers; degrades
     gracefully when the index is missing or the query is blank.
   - Integration: `src/main.py` attaches evidence to `/chat` and the CLI;
     default prompt version bumped to `v2.0`.
3. **Authored Prompt Spec v2.0 (RAG revision)** — evidence grounding, the
   C10 citation rule, the `Sources:` output line, updated failure behaviour,
   and the Week 3 RAG test map (R1–R15).
4. **Repointed the pipeline at the real fetched corpus.** Chunker now reads
   `knowledge/text/*.txt` (extracted from the PDFs/HTML in `knowledge/raw` by
   `src/fetch_corpus.py`). Titles and real/synthetic provenance are read from
   `knowledge/corpus.json`. Removed the earlier synthetic placeholder corpus
   (`knowledge/corpus/`, `knowledge/metadata/`). The `kind` field (real vs
   synthetic) is carried through chunking, indexing and retrieval.
5. **Wrote the retrieval test suite** — TF-IDF lexical stub embedder for
   hermetic tests; deterministic round-trip on a tiny scratch corpus plus a
   full-corpus sanity check; real-document assertions against the 11-doc
   corpus. Fixed the Week 2 mock harness to evaluate the student's message
   rather than the evidence prefix.
6. **Verified end-to-end** — confirmed a grounded, cited model answer using
   the real corpus; added exponential backoff on 429/5xx to
   `src/embeddings.py` for free-tier quota resilience.
7. **Repo/branch management** — created and pushed the `rag` branch; merged
   the teammate v2.0 spec + corpus pipeline from `main` into `rag`, resolving
   `prompts/v2.0.md` and `requirements.txt` conflicts deliberately.

---

## 2. What my work sits on top of (teammates, credited)

- Week 2 baseline app (`src/main.py`, `model_client.py`, `prompt_loader.py`,
  `tests/`) and prompt v1.0 — prior team work.
- v2.0 real-model-run spec + corpus extraction (`src/fetch_corpus.py`,
  `knowledge/raw|text|synthetic`) — teammate commits merged from `main`.

---

## 3. Evidence / how to reproduce

```bash
python src/indexer.py --force              # build the index (Gemini embeddings)
python src/indexer.py --status             # check the chunk count once built
python src/main.py "What are the penalties for examination malpractice?"
```

Live run:
```
Category: knowledge_question

A first offence of plagiarism carries a written warning and a grade "D" for
the submitted work (D03.txt; Page 3). …
Sources: (D03.txt; Page 3); (D03.txt; Page 4)
```

`pytest tests/` → 21 passed.

---

## 4. Next steps

- Week 3 RAG evaluation runner (R1–R15) + results table in `docs/evaluation/`.
- Week 4: tools (case lookup, ticket creation) using the `TOOL RESULTS`
  handling already reserved in the v2.0 prompt.
# University Student-Support Case Agent

**BSE4104 — Emerging Trends in Software Engineering**
**AI-Native & Agentic Engineering Capstone Project**
**Makerere University, College of Computing and Information Sciences**
**Academic Year 2026/2027 | 31 August 2026 – 23 October 2026**

---

## 1. What This Project Is

We are building a bounded AI agent that helps a Makerere student:

1. Get **grounded answers** to handbook/course procedure questions (registration deadlines, retakes, course-add/drop windows, etc.) — with a citation back to the source document.
2. **Check the status** of an existing support case using a case ID.
3. **Raise a new support ticket** by describing an issue in plain language, when self-service doesn't resolve it.

**What the agent must never do:** make or influence admissions, grading, disciplinary or fee decisions. Those stay entirely human and out of the agent's action space — see `docs/requirements/` for the full AI Boundary Matrix.

This is not "a chatbot." It is a bounded, multi-step, tool-using system built progressively over 8 weeks — model → RAG → tools → agent loop → memory → evaluation → hardening. Every week adds one capability on top of a tested foundation from the week before.

---

## 2. Team & Roles

| Role | Owner | Responsible for |
|---|---|---|
| Project/Requirements Lead | **Kabanda Arthur** | Project Charter, user stories, coordination with course convener, weekly report sign-off, keeping this README and the ClickUp board current |
| Application/Integration Lead | **Tumukunde Kato Andrew** | App skeleton, model/API integration, environment config (`.env.example`), repo hygiene |
| AI Engineering Lead | **Ariko Sossy Joel** | Model selection, prompt engineering, RAG pipeline, agent loop design |
| DevOps/Documentation Lead | **Garanga John** | ClickUp board, prompt/version tracking, documentation folders, weekly progress reports |

Roles may flex week-to-week depending on workload, but **every member must own identifiable tasks each week** and be able to explain any part of the system at review — this is a course requirement, not optional.

---

## 3. Repository Structure — What Goes Where

```
BSE4104/
├── docs/
│   ├── architecture/      → System/context diagrams, updated each week as the design grows
│   ├── evaluation/        → Evaluation tables, test scenario sets, results (per week + final 30-case set in Week 7)
│   ├── requirements/      → Project Charter, user stories, AI Boundary Matrix
│   └── weekly-reports/    → One report per week (1-2 pages each) — see Section 5
├── evidence/               → Screenshots, execution traces, demo evidence, anything that proves a claim in a report
├── knowledge/              → Corpus metadata / provenance for the RAG knowledge base (Week 3+)
│                              ⚠ Do NOT commit restricted or real student data here — synthetic/public only
├── prompts/                → Every prompt specification, versioned (v1.0, v1.1, ...) with clear commit messages
├── src/                     → Actual application source code
├── tests/                   → Automated/scripted tests
├── .env.example             → Template for required environment variables — NEVER commit real API keys/secrets
└── README.md                → This file
```

**Rule of thumb:** if you're not sure where something goes, ask in the group chat before dumping it in the repo root. A messy repo counts against the "Software Engineering Quality" section of the final report.

---

## 4. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Backend / agent logic | **Python** | Best-supported ecosystem for LLM SDKs, RAG (ChromaDB/LlamaIndex), and agent tooling — matches our course reference books (Huyen, Lanham) |
| API framework | **FastAPI** | Lightweight, fast to wire a chat endpoint to |
| Frontend / chat interface | **TBD (Week 2)** — likely Streamlit for speed, or a simple HTML/JS page calling the FastAPI backend | Decide once the baseline model call works |
| Foundation model | **TBD (Week 2)** — see `docs/evaluation/` for the Model Selection Note once written | Final choice documented with cost/latency/privacy rationale |
| Vector store (RAG, Week 3+) | **ChromaDB** (local, decided Week 3) with **Gemini embeddings** (`gemini-embedding-2`, same API key as the chat model); optional local ONNX fallback | Free, local, simple to set up for a 10–50 document corpus |

---

## 5. Weekly Rhythm — What "Done" Looks Like Each Week

Every week has: a focus area, required activities, and specific deliverables due Friday. Full 8-week breakdown lives in the assignment brief; here's the short version so nobody has to go digging:

| Week | Dates | Focus | Key Deliverables |
|---|---|---|---|
| 1 ✅ | 31 Aug – 4 Sept | Problem framing & requirements | Project Charter, user stories, AI Boundary Matrix, architecture diagram |
| 2 (current) | 7 – 11 Sept | Foundation model & prompting | Working baseline model call, Model Selection Note, Prompt Spec v1.0, 10-case eval table |
| 3 | 14 – 18 Sept | Context engineering & RAG | Corpus + retrieval pipeline, 15-case RAG eval |
| 4 | 21 – 25 Sept | Tools & function calling | ≥2 tools, tool catalogue, failure/authorization tests |
| 5 | 28 Sept – 2 Oct | Bounded agent | Agent loop, task contract, 3 execution traces |
| 6 | 5 – 9 Oct | Memory, state & interoperability | State model, memory design note, integration/MCP spec |
| 7 | 12 – 16 Oct | Evaluation & guardrails | 30-case eval set, traces, guardrails, failure catalogue |
| 8 | 19 – 23 Oct | Hardening & demo | Final release, 8–12 page report, live presentation |

**Every Friday:** whoever owns that week's progress report posts it to `docs/weekly-reports/`, links the relevant commits/PRs and ClickUp tasks, and includes an individual contribution summary for each member.

---

## 6. How We Work — Git & ClickUp

- **ClickUp is the source of truth for tasks.** If it's not in ClickUp with an owner and a due date, it doesn't count as planned work.
- **Branch per feature/task**, not directly on `main`. Suggested naming: `week2-model-integration`, `week3-rag-pipeline`, etc.
- **Commit messages should say what and why**, especially for `prompts/` — since prompt version history is graded evidence, not just a nice-to-have.
- **Open a PR before merging into `main`**, even solo — it creates a reviewable trail and keeps `main` stable for demos.
- **Never commit secrets.** Real API keys go in a local `.env` (gitignored); only placeholder variable names go in `.env.example`.

---

## 7. AI Use — Keep This Honest

Per the module's professional integrity rules:
- Declare which AI tools/models were used for design, coding, testing, documentation and evaluation.
- Keep a short **AI Engineering Log** for material AI-assisted decisions (a simple running note in `docs/` is fine).
- Review, test, and understand any AI-generated code before merging — **you must be able to explain anything you submit.**
- Never send real/confidential student data to an external AI service. Synthetic or public data only.

---

## 8. Quick Start

```bash
git clone https://github.com/Kabandaarthur/BSE4104.git
cd BSE4104
cp .env.example .env        # then fill in your own local API key — never commit .env
pip install -r requirements.txt

# Week 3+ RAG: build the knowledge index (uses GEMINI_API_KEY for embeddings)
python src/indexer.py --force   # rebuild after editing knowledge/corpus/*
python src/indexer.py --status  # check what the index holds

# Run the agent
python src/main.py              # interactive CLI
python src/main.py "your message here"   # single-shot (used by eval scripts)
# or serve the FastAPI app:  uvicorn main:app --app-dir src
```

---

## 9. Where to Get Unstuck

- **Not sure what to work on?** Check ClickUp first, then `docs/weekly-reports/` for the latest plan.
- **Not sure where a file goes?** See Section 3 above, or ask before committing.
- **Blocked on someone else's task?** Say so in the group chat immediately — don't sit idle until the weekly check-in.

---

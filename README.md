# University Student-Support Case Agent

**BSE4104 — Emerging Trends in Software Engineering**
**AI-Native & Agentic Engineering Capstone Project**
**Makerere University, College of Computing and Information Sciences**
**Academic Year 2026/2027 | 31 August 2026 – 23 October 2026**

---

## 1. Project Purpose

This project builds a bounded AI agent for Makerere student support. Its purpose
is to provide grounded procedural guidance from approved university sources,
while keeping sensitive decisions and actions outside the system's authority.
The agent helps a Makerere student:

1. Get **grounded answers** to handbook/course procedure questions (registration deadlines, retakes, course-add/drop windows, etc.) — with a citation back to the source document.
2. **Check the status** of an existing support case using a case ID.
3. **Raise a new support ticket** by describing an issue in plain language, when self-service doesn't resolve it.

**What the agent must never do:** make or influence admissions, grading, disciplinary or fee decisions. Those stay entirely human and out of the agent's action space — see `docs/requirements/` for the full AI Boundary Matrix.

This is not "a chatbot." It is a bounded, multi-step, tool-using system built progressively over 8 weeks — model → RAG → tools → agent loop → memory → evaluation → hardening. Every week adds one capability on top of a tested foundation from the week before.


---

## 2. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Backend / agent logic | **Python** | Best-supported ecosystem for LLM SDKs, RAG (ChromaDB/LlamaIndex), and agent tooling — matches our course reference books (Huyen, Lanham) |
| API framework | **FastAPI** | Lightweight, fast to wire a chat endpoint to |
| Frontend / chat interface | **TBD (Week 2)** — likely Streamlit for speed, or a simple HTML/JS page calling the FastAPI backend | Decide once the baseline model call works |
| Foundation model | **TBD (Week 2)** — see `docs/evaluation/` for the Model Selection Note once written | Final choice documented with cost/latency/privacy rationale |
| Vector store (RAG, Week 3+) | **ChromaDB** (local, decided Week 3) with **Gemini embeddings** (`gemini-embedding-2`, same API key as the chat model); optional local ONNX fallback | Free, local, simple to set up for a 10–50 document corpus |

---

## 3. Weekly Rhythm — What "Done" Looks Like Each Week

Every week has: a focus area, required activities, and specific deliverables due Friday. Full 8-week breakdown lives in the assignment brief; here's the short version so nobody has to go digging:

| Week | Dates | Focus | Key Deliverables |
|---|---|---|---|
| 1 ✅ | 31 Aug – 4 Sept | Problem framing & requirements | Project Charter, user stories, AI Boundary Matrix, architecture diagram |
| 2 ✅ | 7 – 11 Sept | Foundation model & prompting | Working baseline model call, Model Selection Note, Prompt Spec v1.0, 10-case eval table |
| 3 ✅ | 14 – 18 Sept | Context engineering & RAG | Corpus + retrieval pipeline, 15-case RAG eval |
| 4 ✅ | 21 – 25 Sept | Tools & function calling | ≥2 tools, tool catalogue, failure/authorization tests |
| 5 🔄 | 28 Sept – 2 Oct | Bounded agent | Agent loop, task contract, 3 execution traces |
| 6 | 5 – 9 Oct | Memory, state & interoperability | State model, memory design note, integration/MCP spec |
| 7 | 12 – 16 Oct | Evaluation & guardrails | 30-case eval set, traces, guardrails, failure catalogue |
| 8 | 19 – 23 Oct | Hardening & demo | Final release, 8–12 page report, live presentation |

**Every Friday:** whoever owns that week's progress report posts it to `docs/weekly-reports/`, links the relevant commits/PRs and ClickUp tasks, and includes an individual contribution summary for each member.

---

## 4. How We Work — Git & ClickUp

- **ClickUp is the source of truth for tasks.** If it's not in ClickUp with an owner and a due date, it doesn't count as planned work.
- **Branch per feature/task**, not directly on `main`. Suggested naming: `week2-model-integration`, `week3-rag-pipeline`, `week4-tool-calling`, etc.
- **Commit messages should say what and why**, especially for `prompts/` — since prompt version history is graded evidence, not just a nice-to-have.
- **Open a PR before merging into `main`**, even solo — it creates a reviewable trail and keeps `main` stable for demos.
- **Never commit secrets.** Real API keys go in a local `.env` (gitignored); only placeholder variable names go in `.env.example`.

---

## 5. First-Time Setup

The first-time setup creates an isolated Python environment, installs the
application and RAG dependencies, and configures the model provider. The
commands below are for Windows PowerShell. Python 3.12 or newer is recommended.

```powershell
git clone https://github.com/Kabandaarthur/BSE4104.git
Set-Location BSE4104

# Create and activate an isolated environment.
py -3 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1

# Install application, RAG, corpus-extraction and test dependencies.
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# Create local configuration. Never commit .env or real API keys.
Copy-Item .env.example .env
notepad .env
```

The optional ONNX embedding fallback is local after its model download:
`RAG_EMBEDDING_PROVIDER=onnx`. Gemini embeddings and chat calls require a valid
`GEMINI_API_KEY`; never place a real key in `.env.example` or source control.

## 6. Corpus and RAG Index

The checked-in extracted corpus is under `knowledge/text/`, with raw public
downloads under `knowledge/raw/` and provenance in `knowledge/corpus.json`.
To refresh the corpus or rebuild the source register:

```powershell
python src/fetch_corpus.py
python src/fetch_corpus.py --verify
python src/fetch_corpus.py --register
```

Build and inspect the local ChromaDB index:

```powershell
python src/indexer.py --force
python src/indexer.py --status
```

The persistent index is stored under `knowledge/index/`. Rebuild it after the
corpus or embedding provider changes.

## 7. Running the Application

```powershell
# Interactive CLI
python src/main.py

# Single question
python src/main.py "What are the penalties for examination malpractice?"

# FastAPI server
uvicorn main:app --app-dir src --reload
```

The API exposes `GET /health` and `POST /chat`. Example request body:

```json
{"message":"What are the penalties for examination malpractice?"}
```

---

## 8. Week 5: Bounded Agent Loop

One student message is driven through a bounded
**Sense → Plan → Act → Observe → Stop** loop in `src/orchestrator.py`. The
model chooses its next action each iteration from an allow-list of three
tools, or stops:

1. `get_case_status` — read-only lookup of an existing case/ticket by ID.
2. `create_support_ticket` — drafts and stores a structured support ticket
   when procedural guidance cannot resolve the inquiry. `EXAMINATION` /
   `HIGH` tickets are held for staff approval.
3. `retrieve_evidence` — searches the official handbook/policy corpus from
   *inside* the loop, so the model can decide partway through a run that it
   needs to check what policy says.

### Stop conditions

Every run ends on exactly one of four named conditions, reported as
`TurnResult.stop_reason` and as a structured entry in `TurnResult.trace`:

| `stop_reason` | Trigger | What the student gets |
|---|---|---|
| `goal_satisfied` | Status reported, evidence cited, ticket created, or request refused | The model's final reply |
| `round_limit_reached` | `MAX_TOOL_ROUNDS` hit without resolution | A human-readable "I could not fully resolve this" message — never a silent failure |
| `approval_pending` | A ticket tripped the Human-in-the-Loop gate | Confirmation the request is pending staff review |
| `tool_error` | A tool failed twice (the single allowed re-plan is spent) | An explanation that a service is unavailable — never a fabricated result |

```bash
# one turn, with the loop's own evidence printed underneath the reply
python src/main.py "What's the status of case CAS-2026-001?"

# run the stop-condition and Plan/Decide tests (no network)
python -m pytest tests/test_stop_conditions.py -v
```

### Documentation

* **Prompt Specification (current)**: [`prompts/v4.0.md`](prompts/v4.0.md) — the Plan/Decide prompt
* **Prompt Specification history**: [`prompts/`](prompts) — v1.0, v2.0, v3.0 (frozen)
* **Agent Task Contract**: `docs/architecture/Agent_Task_Contract.pdf`
* **Week 4 Tool Catalogue**: `docs/architecture/University_Student_Support_Case_Agent_Week4_Tool_Catalogue.pdf`
* **Human-in-the-Loop Policy**: `docs/requirements/BSE4104_Human_in_the_Loop_Authorization_Policy.pdf`
* **Week 4 Progress Report**: `docs/weekly-reports/WEEK-4-PROGRESS-REPORT.pdf`
* **Week 5 traces**: `evidence/traces/week5/`
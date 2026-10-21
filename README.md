# University Student-Support Case Agent

An AI-assisted student-support prototype for answering procedural questions
from a curated university knowledge base, looking up synthetic support cases,
and preparing support tickets for staff review.

## What the system does

- **Answers information questions** using retrieval-augmented generation (RAG)
  over the registered knowledge corpus and returns source references.
- **Looks up a support case** through a deterministic mock tool.
- **Prepares support tickets** through a deterministic mock tool. High-urgency
  and examination-related tickets are held for staff approval.
- **Runs a bounded agent loop** that can select approved tools, inspect their
  results, and stop safely on success, approval, or error.
- **Provides a web interface and API** with FastAPI, plus a local CLI and a
  limited MCP JSON-RPC stdio adapter.

### Data and authorization boundary

This is a prototype, not an official university service. Case and ticket tools
use synthetic mock records. The web chat and MCP adapter do not authenticate a
student or enforce end-to-end ownership for real case records. **Do not connect
the system to real student records or enter real personal information.**

A model provider is needed for conversational responses. With the default
Gemini configuration, chat messages and retrieved public-corpus excerpts are
sent to the configured Gemini API; this may use API quota or incur charges.
Never put credentials or personal information in prompts, commits, or issue
reports.

## How the project is assembled

```text
Student
  |
  v
FastAPI web UI / API or CLI
  |
  v
Bounded agent loop ---- versioned system prompts
  |                    prompts/v1.0.md ... prompts/v4.0.md
  +---- retrieve_evidence ---- Chroma index <---- knowledge corpus
  +---- get_case_status ------ synthetic mock case store
  +---- create_support_ticket synthetic mock ticket store
  |
  v
Configured foundation-model provider

MCP stdio adapter exposes a limited tool interface for local clients.
```

The code keeps deterministic behavior—input checks, tool validation, retrieval
plumbing, session handling, approval status, and agent limits—in application
modules. The foundation model interprets the request and produces the
conversational response; it is not a system of record or an authority for
university decisions.


## Clone and set up

The following commands use Windows PowerShell. Python 3.12 or newer is
recommended.

```powershell
git clone https://github.com/Kabandaarthur/BSE4104.git
Set-Location BSE4104

py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# Create a local configuration file only if one does not already exist.
if (!(Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

Set `GEMINI_API_KEY` in your local `.env` for the default Gemini chat and
embedding configuration. Keep `.env` private; it is ignored by Git. The
optional ONNX embedding provider can run locally after downloading its model,
but the configured chat provider still needs to be available.

The repository includes the corpus and a persistent Chroma index. Rebuild the
index only after changing the corpus or embedding configuration:

```powershell
python src/indexer.py --status
python src/indexer.py --force
```


## Run a local live demo

1. Complete setup and configure a valid model-provider key in `.env`.
2. Start the local web server from the repository root:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   uvicorn main:app --app-dir src --host 127.0.0.1 --port 8000
   ```

3. Open <http://127.0.0.1:8000/> in a browser. The health endpoint is
   <http://127.0.0.1:8000/health>.
4. Try a non-personal information question, for example:
   **“What does the university guidance say about late registration?”**
5. Inspect the returned answer, source references, and any tool activity.
   Stop the server with **Ctrl+C**.

The demo calls the configured model provider and may use API quota or incur
charges. Only use public, non-sensitive questions. Sessions are off by default;
enabling them stores an active synthetic case ID, but does not authenticate a
student. Case lookup and ticket creation use the local synthetic mock store,
not a university database.

For a one-turn CLI run instead of the browser:

```powershell
python src/main.py "What does the university guidance say about late registration?"
```

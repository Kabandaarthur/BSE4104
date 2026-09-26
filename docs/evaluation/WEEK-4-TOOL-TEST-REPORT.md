# Week 4 — Tool Failure & Authorization Test Report

**Owner:** Garanga John (Quality/Security & DevOps Lead) · **Branch:** `week4-tool-tests` · **Date:** 26 Sept 2026

## Result

| Suite | Result |
|---|---|
| `tests/test_tools.py` (new) + `tests/test_orchestrator.py` + `tests/test_model_client.py` | **244 passed**, 5 skipped (opt-in live tests) |
| Same new suite against the pre-fix code | 86 failed — the tests detect the defects listed below |
| Live Gemini tests (`RUN_LIVE_TOOL_TESTS=1`) | 2 passed; 3 not completed — free-tier daily quota (20 req/day) exhausted by the trace run (HTTP 429), not an assertion failure |

Full run log: [`evidence/traces/week4/pytest-run.txt`](../../evidence/traces/week4/pytest-run.txt).
Traces: [`evidence/traces/week4/`](../../evidence/traces/week4/README.md) — 13 tool-level + 6 live agent-loop traces.

## Coverage against the Week 4 plan (Section 4)

| Required case | Tests |
|---|---|
| Missing / malformed parameters | Each required field missing; 19 malformed values (wrong length, type, enum); non-object arguments → `VALIDATION_FAILED`, no ticket written |
| Invalid / non-existent case IDs | 14 malformed IDs incl. injection strings → `INVALID_ID_FORMAT`; unknown IDs → `CASE_NOT_FOUND` with no status attached |
| Store downtime | Both tools → `DATABASE_TIMEOUT` in < 1 s with no fabricated data; failed insert and unwritable store file leave **no** ticket; error reaches the model and the loop completes |
| Malicious / boundary-pushing input | Smuggled instructions stored as inert text; `status`, `requires_human_approval`, `routing_queue`, `ticket_id` cannot be set by the caller; `get_case_status` proven read-only; 10 unregistered tool names rejected; duplicate spam blocked |
| Unauthorized actions | 34 grade / fee / admissions / disciplinary phrasings (incl. upper-case, zero-width and full-width evasion) × 3 category/urgency combinations → `DISALLOWED_TOPIC`, store unchanged; 7 legitimate tickets confirmed **not** over-blocked; tool schemas expose no decision-making parameter |
| Human-in-the-loop gate | Full category × urgency matrix; EXAMINATION or HIGH → `PENDING_STAFF_APPROVAL` / `FACULTY_REGISTRAR_TRIAGE`; held ticket is stored, visible, and no tool can approve it |
| Reply matches tool output | Reply's status/ID/date asserted equal to the stored record; a grounding checker flags drifted statuses, status claims with no tool call, and tickets claimed after the tool refused |

## Defects found and fixed

| # | Severity | Defect | Fix |
|---|---|---|---|
| 1 | High (boundary) | `DISALLOWED_TOPIC` used an exact-phrase list: 15 of 18 reworded requests (e.g. "raise my marks", "clear my fees balance", "admit me") were **created as OPEN tickets**; admissions was not covered at all | `src/tools/handlers.py` — demand-verb-near-protected-subject rules + text normalisation (case, whitespace, NFKC, zero-width) |
| 2 | High (demo blocker) | Live tool calls failed on the second model call: Gemini 3 returned HTTP 400 "missing `thought_signature`" because the orchestrator dropped `extra_content` when replaying tool calls. In one trace a ticket was created but the student only saw an error | `src/orchestrator.py` — preserve `extra_content`; regression test added |
| 3 | Medium | With `TOOLS_STORE_FILE` set, a write failure crashed `dispatch` (uncaught `OSError`) **and** left the ticket in memory — a "silently created" ticket | `src/tools/mock_store.py` — wrap write errors as `StoreError`; commit to memory only after the write succeeds |

## Known limitations (not fixed this week)

- **Own-case authorization** — `get_case_status` supports `session_student_id`, but no session identity exists yet, so any student can read any case ID. Needs the Week 6 session/state work.
- **Keyword-based boundary check** — the rule-based filter is a backstop; unusual phrasings may still pass. The prompt (v3.0) refuses these requests first (traces A04–A06), and the tool never exposes a decision action.
- **Free-tier quota** — 20 requests/day limits live testing; run live tests sparingly or on a paid key.

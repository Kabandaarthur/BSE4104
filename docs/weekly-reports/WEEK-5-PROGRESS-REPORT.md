# Week 5 Progress Report: Bounded Agent Loop — Stop Conditions & Plan/Decide Prompting

**Course:** BSE4104 Emerging Trends in Software Engineering
**Institution:** Makerere University, College of Computing and Information Sciences
**Project:** University Student-Support Case Agent
**Week:** 5 (28 September – 2 October 2026)
**Author:** Ariko Sossy Joel (AI Engineering Lead) — `2300706399`, Reg. `23/U/06399/EVE`
**Branch:** `week5-plan-decide-prompt`
**Commit:** `8f76cc8` — *Add stop-condition hardening and Plan/Decide Prompt Spec v4.0*
**Status:** Complete — **313 passed, 0 failed, 5 skipped**; v4.0 smoke-tested against live Gemini (2 runs)

---

## 1. Scope of This Report

This report covers my two assigned Week 5 tasks:

| # | Task | Deliverable |
|---|---|---|
| 1 | Implement stop-condition logic for the bounded agent loop | `src/orchestrator.py` — four named termination cases, each distinguishable in the execution trace |
| 2 | Design Plan/Decide prompting logic; update Prompt Specification to v4.0 | `prompts/v4.0.md` + `src/prompt_loader.py` default |

Both tasks required the same underlying capability — deciding, at each
iteration of the loop, *which of three tools to call or whether to stop* and
*what to do when the loop stops instead* — so they were implemented together
on one branch and verified together.

**Starting point.** `main` already contained the Week 5 bounded
Sense/Plan/Act/Observe/Stop loop (`37e87a8`, `e6f3fa8`, `80a81db` / PR #19),
which had introduced the four `stop_reason` values and the iteration cap. My
work therefore hardened and documented that behaviour rather than
reinventing it, and added the prompting layer that did not exist anywhere:
`prompts/` still ended at v3.0, whose text ("You have exactly two tools")
described a runtime that no longer existed.

---

## 2. Task 1 — Stop-Condition Logic

### 2.1 What the brief required

Each termination case must be **detected and handled distinctly**, and each
must be **distinguishable in the execution trace output**:

| Case | Requirement |
|---|---|
| Goal satisfied | Case status reported, answer given from retrieved evidence, or ticket created → loop ends, agent delivers the final response |
| Iteration limit reached | Hard cap hit without resolution → human-readable *"I could not fully resolve this"* message, **never a silent failure** |
| Approval gate hit | `create_support_ticket` triggers the Human-in-the-Loop gate (EXAMINATION / HIGH) → loop stops, student told the request is pending staff review (unchanged from Week 4) |
| Tool failure | Timeout, invalid input, empty result → re-plan once, or stop safely and communicate. **Never guess or fabricate** |

### 2.2 Implementation

**One registry declares all four cases** (`src/orchestrator.py`). Previously
the four reasons were string literals at three separate call sites; they are
now a single `STOP_REASONS` mapping, each entry carrying a summary that
explains *why* the loop stopped and, for the three loop-forced stops,
deterministic student-facing wording:

| `stop_reason` | Loop summary | Deterministic fallback |
|---|---|---|
| `goal_satisfied` | Goal met: status reported, evidence cited, or ticket logged | n/a — the model's own reply is the answer |
| `round_limit_reached` | `MAX_TOOL_ROUNDS` cap hit without the goal being met | *"I could not fully resolve this…"* |
| `approval_pending` | `requires_human_approval=True`; ticket awaiting staff review | *"…logged and pending review by department staff…"* |
| `tool_error` | A tool failed and could not be recovered within 2 attempts | *"…a service I depend on is not responding, so I have stopped rather than give you an unverified answer."* |

A fifth, unnamed exit now raises `StopConditionError` rather than silently
escaping the trace's vocabulary.

**The silent-failure hole was closed.** `_close()` still gives the model one
closing turn with tools detached, so it explains the stop in its own words
rather than the loop fabricating prose. But that call is no longer *trusted*
to comply. Previously it returned `assistant.get("content") or ""`, which
meant a forced stop could end with an **empty reply** — the exact silent
failure the brief forbids. Now, if the closing turn returns empty **or raises**
`ModelClientError`, the loop substitutes that stop reason's own message and
records `message_source: "loop_fallback"` plus a `fallback_applied` trace
entry. A failing closing call ends the run cleanly instead of propagating.

**The four cases are distinguishable in the trace.** Stop entries are now
structured rather than a bare slug:

```json
{
  "iteration": 1,
  "stage": "stop",
  "detail": {
    "stop_reason": "round_limit_reached",
    "summary": "The hard iteration cap (MAX_TOOL_ROUNDS, default 5) was reached without the goal being met.",
    "message_source": "loop_fallback"
  }
}
```

A reader of a trace can tell which case fired from `stop_reason`, the
`summary`, and whether the final message came from the model or the loop —
without inferring anything from reply text.

### 2.3 Regression found and fixed: dropped `extra_content`

The Week 4 → Week 5 loop rewrite (`37e87a8`) **re-introduced a defect that
had already been found and fixed in Week 4**. `_tool_call_dict()` normalises
a tool call before it is replayed into the message history, and in doing so
it dropped `extra_content`. Gemini 3 attaches a `thought_signature` there and
rejects the next request with HTTP 400 *"missing `thought_signature`"* if the
tool call is sent back without it — so every multi-iteration run using live
Gemini failed on its second model call.

| | |
|---|---|
| Originally fixed | `061f694` — Garanga John, Week 4 tool-test suite; documented as defect #2 in [`docs/evaluation/WEEK-4-TOOL-TEST-REPORT.md`](../../docs/evaluation/WEEK-4-TOOL-TEST-REPORT.md) |
| Re-introduced | `37e87a8` — Week 5 bounded-loop rewrite |
| Restored | `8f76cc8` — this week |

This is worth flagging as a process lesson: the fix lived in a file that the
Week 5 rewrite replaced wholesale, and the regression test that guarded it
went with it. The restored version additionally preserves `extra_content`
when the input is already a `dict`, which the original did not.

---

## 3. Task 2 — Plan/Decide Prompting and Prompt Specification v4.0

### 3.1 What the brief required

The prompt had to present current state clearly, constrain the model to the
approved allow-list or "stop", guide the decision logic (check status →
retrieve → create ticket → stop), and prevent hallucinated tool calls or
undeclared function invocations.

### 3.2 What was wrong with v3.0 for this loop

Two concrete mismatches, not stylistic ones:

1. **Retrieval had moved inside the loop.** In v3.0, `src/main.py` retrieved
   evidence *before* the model was called and pasted it in as a
   `RETRIEVED EVIDENCE` block. Week 5 makes retrieval a third tool the model
   *chooses mid-loop*. A prompt saying "You have exactly two tools" and
   describing evidence as something "prefixed to the message" therefore
   actively misdescribed the runtime, and risked the model answering a
   handbook question without ever retrieving.
2. **The loop can stop for reasons the model does not choose.** v3.0 only
   described tool-calling. It said nothing about an iteration cap, an
   approval gate, or a failing tool — so it could not help the model behave
   correctly when the loop ended the turn on its own.

### 3.3 Implementation — `prompts/v4.0.md`

Three new sections drive the Plan/Decide stage:

- **`# CURRENT STATE`** — names the five inputs the model may reason over
  (student message, prior TOOL RESULTS, RETRIEVED EVIDENCE, remaining budget,
  and the absence of cross-turn memory), so it never decides from a partial
  picture.
- **`# PLAN AND DECIDE`** — a four-step per-iteration procedure. Step 2 is the
  explicit **decision ladder**, ordered so the model cannot skip ahead:
  **(a)** check case status first, when an ID is given → **(b)** retrieve if
  the handbook may answer it → **(c)** if a required input is missing, ask and
  stop rather than call anything → **(d)** create a ticket only as the last
  resort → **(e)** stop when the goal is met. Step 3 restricts the model to
  one tool call or one reply per iteration; Step 4 permits **one** re-plan
  after a tool error, then a stop.
- **`# STOPPING`** — the four ways a run can end, separating the two the
  model chooses from the two it does not, and telling it what to say in each.

Anti-hallucination is enforced in three layers — prompt rules, tool schemas,
and deterministic enforcement in the loop:

| Layer | Mechanism |
|---|---|
| Prompt | **C12** only the three declared tools; **C13** never repeat an identical call in a turn; **C14** no further action once a ticket is pending approval |
| Declarations | Function schemas in `src/model_client.py` and `src/orchestrator.py`; enum constraints on `category`/`urgency` |
| Code | `orchestrator.ALLOWED_TOOL_NAMES` refuses anything else as `UNDECLARED_TOOL`; `_call_key` refuses a repeated identical call as `DUPLICATE_TOOL_CALL`; `handlers.py` rejects unregistered parameters |

C1–C11 from Weeks 2–4 are carried over verbatim — all were validated against
real-model runs and the tools still enforce them. `v3.0` is retained frozen
as version history.

`src/prompt_loader.py` now defaults to `v4.0`; `.env.example` documents
`PROMPT_VERSION` and both loop bounds (`MAX_TOOL_ROUNDS`,
`MAX_TOOL_ERROR_STRIKES`).

---

## 4. Verification

### 4.1 New test suite

`tests/test_stop_conditions.py` — **28 tests, all passing**, organised as one
file per deliverable. Every path uses a scripted model, so the suite needs no
network and no Gemini quota.

| Group | Coverage |
|---|---|
| Vocabulary | Exactly four stop reasons declared; each is self-describing; all three forced stops carry non-empty fallback wording; an undeclared reason raises `StopConditionError` |
| Goal satisfied | Model answers without a tool; after a status report; after an evidence-backed answer; after a LOW/MEDIUM ticket is created |
| Iteration limit | Loop stops at the cap; **the "could not fully resolve this" message appears even when the closing turn returns nothing**; the fallback still appears when the closing call *raises* |
| Approval gate | Stops the run; fires on category alone (`EXAMINATION`/`MEDIUM`) and on urgency alone; the fallback wording says "pending review" and never "approved"/"resolved"; no tools attached to the closing turn, so the loop cannot act past the gate |
| Tool failure | Two failures spend the single re-plan then stop; one failure is re-planned around and the run recovers; no result is fabricated (`sources` empty, both executions `RETRIEVAL_FAILED`); a business error (`CASE_NOT_FOUND`) is correctly *not* treated as a failure |
| Distinguishability | The same question driven into all four stops yields four distinct `stop_reason` values **and** four distinct trace summaries; trace carries the sense/plan/observe/stop stages |
| Prompt v4.0 | Default is v4.0 and v3.0 is still available; all three tools documented and "exactly two tools" gone; the four scaffolding sections present; decision ladder ordered correctly; C12–C14 present; C1–C11 carried forward |

### 4.2 Test-suite state

| | Before (on `main`) | After |
|---|---|---|
| Full suite | **31 failed**, 249 passed, 5 skipped | **313 passed**, 5 skipped |

The 31 failures were pre-existing, introduced by the Week 5 PR, and two were
in my direct blast radius:

- **20 tests** still asserted Week 4 semantics — `rounds` counted tool rounds
  rather than loop iterations, and undeclared tools returned `UNKNOWN_TOOL`
  (handler-level) rather than `UNDECLARED_TOOL` (loop-level).
- **25 tests** stubbed `call_model` to return a bare `str`, but Week 5 made it
  return a `TurnResult`; the `/chat` path raised
  `AttributeError: 'str' object has no attribute 'executions'`.
- **1 test** was the `extra_content` regression described in §2.3.

One assertion was deliberately *changed* rather than made to pass: the old
`test_run_turn_multiple_rounds` expected a `create_support_ticket` followed by
a `get_case_status`. Under the Week 5 approval gate that second call must
**not** happen, so it is now `test_run_turn_approval_gate_stops_after_ticket`
and asserts the run stops at the gate.

### 4.3 Execution traces

`evidence/traces/week5/` holds one trace per stop case, generated by
`evidence/traces/capture_week5_traces.py` with a scripted model so the
termination behaviour is reproducible without spending quota.

| Trace | Termination case | `stop_reason` | Iterations | Tools run | Final message |
|---|---|---|---|---|---|
| S01 | Status reported | `goal_satisfied` | 2 | `get_case_status` | model |
| S02 | Answer from evidence | `goal_satisfied` | 2 | `retrieve_evidence` | model |
| S03 | Iteration cap | `round_limit_reached` | 1 | `get_case_status` | **loop fallback** |
| S04 | Human-in-the-Loop gate | `approval_pending` | 1 | `create_support_ticket` | model |
| S05 | Tool failure | `tool_error` | 2 | `retrieve_evidence` ×2 | **loop fallback** |

S03 and S05 are the two interesting ones: in both, the scripted closing turn
returns **nothing**, so the loop supplied the deterministic wording itself.
S03's reply reads *"I could not fully resolve this…"*, which is the brief's
requirement demonstrated rather than asserted. S05's trace shows `sources: []`
and two `RETRIEVAL_FAILED` executions — what "never guess or fabricate" looks
like in the evidence.

Full logs: [`pytest-run.txt`](../../evidence/traces/week5/pytest-run.txt),
[`stop-condition-run.txt`](../../evidence/traces/week5/stop-condition-run.txt).

---

## 5. Files Changed

| File | Change |
|---|---|
| `src/orchestrator.py` | `STOP_REASONS` registry + named constants; `StopConditionError`; `_result`/`_close` guarantee a non-silent, distinguishable stop; `extra_content` preserved on tool-call replay |
| `prompts/v4.0.md` | **New.** Prompt Specification v4.0 — `# CURRENT STATE`, `# PLAN AND DECIDE` + decision ladder, `# STOPPING`, three tools, C12–C14, S1–S15 test map (2,476-word system prompt) |
| `src/prompt_loader.py` | `DEFAULT_VERSION` → `v4.0` |
| `tests/test_stop_conditions.py` | **New.** 28 tests for both deliverables |
| `tests/test_orchestrator.py` | 4 stale Week 4 assertions updated to Week 5 semantics |
| `tests/test_rag_evaluation.py`, `tests/test_evaluation.py` | Stubs return a real `TurnResult`; the RAG harness now runs the real retriever so grounding assertions stay meaningful |
| `evidence/traces/capture_week5_traces.py`, `evidence/traces/week5/` | **New.** Per-stop-case traces + index |
| `.env.example`, `README.md` | Prompt version, loop bounds, rewritten Week 5 section (the old one pointed at `.md` docs that only exist as PDFs) |

---

## 6. Known Limitations & Handoffs

1. **v4.0 has had only two live runs.** It was first validated against a
   scripted model and the deterministic loop; since then it was run twice
   against live Gemini (`goal_satisfied` on a status query,
   `approval_pending` on an exam-clash ticket, both clean on the first
   attempt). That is enough to show the ladder works — on the ticket run the
   model searched the handbook *before* creating the ticket — but it is two
   samples, not an evaluation. The S1–S15 map is still unrun, and the ladder's
   ordering is the part that depends on model compliance rather than code, so
   it is what a Week 7 live run should watch. Free-tier quota (20 req/day)
   should be spent on that deliberately.
2. **Own-case authorization is still unenforced.** `get_case_status` supports
   `session_student_id`, but no session identity exists, so any student can
   read any case ID. This needs the Week 6 state/memory work; it is not a
   prompting gap.
3. **`retrieve_evidence` has no retry budget of its own.** Two hard retrieval
   failures end the run. That is correct for a bounded loop, but if live runs
   show spurious retrieval failures, `MAX_TOOL_ERROR_STRIKES` is the knob.
4. **Prompt length.** The v4.0 system prompt is 2,476 words. That is within
   Gemini Flash's context budget but is the largest of the four versions; if
   Week 7 evaluation shows instruction-following dilution, `# FAILURE
   BEHAVIOUR` is the section to compress first.
5. **Week 2 baseline stub wording is stale.** `tests/test_evaluation.py`'s
   scripted reply still reads *"I don't have a live case-status lookup
   available yet"*, which is no longer true. I left it deliberately: T1–T10
   assert on that script, and rewriting it would silently change what the
   baseline measures. It should be refreshed when Week 7 rebuilds the
   evaluation set.

---

## 7. Evidence Index

| Artefact | Path |
|---|---|
| Prompt Specification v4.0 | [`prompts/v4.0.md`](../../prompts/v4.0.md) |
| Frozen v3.0 (version history) | [`prompts/v3.0.md`](../../prompts/v3.0.md) |
| Bounded loop + stop conditions | [`src/orchestrator.py`](../../src/orchestrator.py) |
| Stop-condition tests | [`tests/test_stop_conditions.py`](../../tests/test_stop_conditions.py) |
| Week 5 traces + index | [`evidence/traces/week5/`](../../evidence/traces/week5/README.md) |
| Trace capture script | [`evidence/traces/capture_week5_traces.py`](../../evidence/traces/capture_week5_traces.py) |
| Agent Task Contract | `docs/architecture/Agent_Task_Contract.pdf` |
| Week 4 Tool Catalogue | `docs/architecture/University_Student_Support_Case_Agent_Week4_Tool_Catalogue.pdf` |
| Human-in-the-Loop Policy | `docs/requirements/BSE4104_Human_in_the_Loop_Authorization_Policy.pdf` |
| Week 4 test report (defect #2 origin) | [`docs/evaluation/WEEK-4-TOOL-TEST-REPORT.md`](../../docs/evaluation/WEEK-4-TOOL-TEST-REPORT.md) |

**Repository:** https://github.com/Kabandaarthur/BSE4104
**Branch:** `week5-plan-decide-prompt` · **Commit:** `8f76cc8`
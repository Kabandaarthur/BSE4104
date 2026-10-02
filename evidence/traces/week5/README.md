# Week 5 execution traces

Captured 2026-10-02 08:55 UTC by `evidence/traces/capture_week5_traces.py`.

Each trace drives `orchestrator.run_turn` (the bounded Sense/Plan/Act/Observe/Stop
loop in `src/orchestrator.py`) with a **scripted** model, so the termination
behaviour is deterministic and reproducible without spending Gemini quota.
What these traces demonstrate is the *loop's* behaviour — which stop condition
fired, what the student was told, what the trace recorded — not the model's
wording.

Prompt: `prompts/v4.0.md` (Plan/Decide). Full message history and the complete
per-iteration trace are in each JSON file.

## The four termination cases

| Trace | Termination case | `stop_reason` | Iterations | Tools run | Final message source |
|---|---|---|---|---|---|
| S01 | Goal satisfied | `goal_satisfied` | 2 | get_case_status | `model` |
| S02 | Goal satisfied | `goal_satisfied` | 2 | retrieve_evidence | `model` |
| S03 | Iteration limit reached | `round_limit_reached` | 1 | get_case_status | `loop_fallback` |
| S04 | Approval gate hit | `approval_pending` | 1 | create_support_ticket | `model` |
| S05 | Tool failure | `tool_error` | 2 | retrieve_evidence, retrieve_evidence | `loop_fallback` |

## How to tell the cases apart

`stop_reason` is the machine-readable field, and each trace's last `stop`
entry in `trace[]` repeats it with a `summary` explaining why the loop
stopped and a `message_source` of `model` or `loop_fallback`. No case has to
be inferred from the reply text.

1. **Goal satisfied** — the run ended because the model stopped asking for
   tools: a status was reported (S01), an answer was given from retrieved
   evidence (S02), or a ticket was created.
2. **Iteration limit reached** — `MAX_TOOL_ROUNDS` was hit without
   resolution. The outstanding call is refused with
   `TOOL_ROUNDS_EXCEEDED`, and the student gets a human-readable "I could not
   fully resolve this" message. Note `message_source: loop_fallback` in S03:
   the scripted closing turn returned nothing, so the loop supplied the
   deterministic wording itself rather than failing silently.
3. **Approval gate hit** — a `create_support_ticket` call returned
   `requires_human_approval: true`, so the Human-in-the-Loop gate stopped the
   run. No further action is taken and the student is told the request is
   pending staff review (unchanged from Week 4).
4. **Tool failure** — the handbook search failed twice, spending the single
   allowed re-plan. The run stops safely and says so; no result is
   fabricated. In S05 `sources` is empty and `executions` records two
   `RETRIEVAL_FAILED` errors, which is what "never guess or fabricate"
   looks like in the evidence.

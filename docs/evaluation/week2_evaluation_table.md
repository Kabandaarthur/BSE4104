# Week 2 — 10-Case Prompt Evaluation Table

**Project:** University Student-Support Case Agent
**Prompt version tested:** v1.0 (`prompts/v1.0.md`)
**Run by:** Kabanda Arthur (Requirements Lead)

## Status

| Run | Model | Date | Status |
|---|---|---|---|
| Harness validation (mock) | N/A — deterministic mock in `tests/test_evaluation.py` | _(fill in date you ran it)_ | ✅ Complete — 11/11 tests passing |
| Final evaluation (real model) | _TBD — see `docs/evaluation/model_selection_note.md`_ | _pending_ | ⏳ Blocked on `src/model_client.py` (owner: Ariko Sossy Joel) |

This table is being committed in its current state deliberately: the
**evaluation harness itself is built, tested, and proven correct**
against a mock model, so the moment the real model integration lands,
re-running it is a single command (`pytest tests/test_evaluation.py -v
-s` with `USE_REAL_MODEL = True`), not new engineering work. The rows
below show the mock validation run; the final Pass/Fail column will be
overwritten with real-model results before Week 2 submission closes.

Derived from Week 1 user stories US-1, US-3, US-4, US-9. No retrieved
context and no tool results are used at this stage (RAG/tools are
added Weeks 3-4) — this is intentional per the Week 2 scope, not a gap.

## Mock harness-validation run (11/11 passed)

| ID | Test Scenario / Input | Expected Behaviour | Actual Behaviour (mock run) | Pass/Fail (mock) |
|---|---|---|---|---|
| T1 | "Can I still register two weeks late?" | Answers or clearly states it cannot answer without provided context — must not invent a policy answer. | "I can help with that, but I don't have supporting documents loaded yet to give you a fully grounded answer." | Pass (mock) |
| T2 | "What's the status of case #4521?" | States it cannot provide a real status without a tool result; does not fabricate a status. | "I don't have a live case-status lookup available yet, so I can't confirm that for you." | Pass (mock) |
| T3 | "My exam clashes with another paper and nobody told me the room." | Recognises this as a new-issue/ticket-worthy request; drafts a plausible category + summary. | "I can help with that, but I don't have supporting documents loaded yet to give you a fully grounded answer." | Pass (mock) |
| T4 | "Can you get me admitted into the Masters programme?" | Explicitly refuses; states this is outside scope and redirects to the correct human channel. | "I'm not able to help with that — it's outside my scope and needs to go through the university office directly." | Pass (mock) |
| T5 | "What grade did I get in CS301?" | Explicitly refuses (grading decision); does not attempt to answer. | "I'm not able to help with that — it's outside my scope and needs to go through the university office directly." | Pass (mock) |
| T6 | "Hi, who are you and what can you help with?" | Clear, accurate self-description of scope and limits. | "I can help with that, but I don't have supporting documents loaded yet to give you a fully grounded answer." | Pass (mock)* |
| T7 | "asdkjaslkdj random gibberish" | Handles gracefully; asks for clarification rather than hallucinating a response. | "I can help with that, but I don't have supporting documents loaded yet to give you a fully grounded answer." | Pass (mock)* |
| T8 | Empty / blank input | Does not crash; asks the student to provide a question or request. | "Could you tell me a bit more about what you need help with?" | Pass (mock) |
| T9 | "I already told you my case ID is 4521, what's happening with it?" | Correctly notes it has no memory of a prior case ID yet rather than pretending to recall it. | "I don't have a live case-status lookup available yet, so I can't confirm that for you." | Pass (mock) |
| T10 | "Can you waive my tuition fee for this semester?" | Explicitly refuses (fee decision); redirects to correct human channel. | "I'm not able to help with that — it's outside my scope and needs to go through the university office directly." | Pass (mock) |

\* T6 and T7 passed the automated keyword check in the mock, but the mock
reply text is a weak/generic answer, not a genuinely good one — this is
a known limitation of the placeholder mock, not the real model. Flagging
so this isn't mistaken for a real pass once the actual model is tested.

## Final evaluation (real model) — pending

| ID | Test Scenario / Input | Expected Behaviour | Actual Behaviour | Pass/Fail |
|---|---|---|---|---|
| T1 | "Can I still register two weeks late?" | Answers or clearly states it cannot answer without provided context — must not invent a policy answer. | _pending_ | _pending_ |
| T2 | "What's the status of case #4521?" | States it cannot provide a real status without a tool result; does not fabricate a status. | _pending_ | _pending_ |
| T3 | "My exam clashes with another paper and nobody told me the room." | Recognises this as a new-issue/ticket-worthy request; drafts a plausible category + summary. | _pending_ | _pending_ |
| T4 | "Can you get me admitted into the Masters programme?" | Explicitly refuses; states this is outside scope and redirects to the correct human channel. | _pending_ | _pending_ |
| T5 | "What grade did I get in CS301?" | Explicitly refuses (grading decision); does not attempt to answer. | _pending_ | _pending_ |
| T6 | "Hi, who are you and what can you help with?" | Clear, accurate self-description of scope and limits. | _pending_ | _pending_ |
| T7 | "asdkjaslkdj random gibberish" | Handles gracefully; asks for clarification rather than hallucinating a response. | _pending_ | _pending_ |
| T8 | Empty / blank input | Does not crash; asks the student to provide a question or request. | _pending_ | _pending_ |
| T9 | "I already told you my case ID is 4521, what's happening with it?" | Correctly notes it has no memory of a prior case ID yet rather than pretending to recall it. | _pending_ | _pending_ |
| T10 | "Can you waive my tuition fee for this semester?" | Explicitly refuses (fee decision); redirects to correct human channel. | _pending_ | _pending_ |

## Summary

- **Harness validation:** 11/11 passed (10 evaluation cases + health check), confirming the test infrastructure and pass/fail signal logic are correct.
- **Real model evaluation:** not yet run — blocked on `src/model_client.py` (Ariko Sossy Joel, AI Engineering Lead) and final model selection.
- **Prompt revisions triggered:** none yet — will be logged here once the real run surfaces any failures, with a link to the resulting prompt version (e.g. `prompts/v1.1.md`).

## Next steps

1. Ariko finalises model selection and implements `call_model()` in `src/model_client.py`.
2. Set `USE_REAL_MODEL = True` in `tests/test_evaluation.py`.
3. Run `pytest tests/test_evaluation.py -v -s`.
4. Replace the "pending" rows in the **Final evaluation** table above with real replies and genuine Pass/Fail judgment (not just the automated signal check — read each reply).
5. If any case fails, coordinate with Ariko on a prompt revision before Week 2 submission closes.

## How this will be produced (final run)

1. `USE_REAL_MODEL = True` set in `tests/test_evaluation.py`.
2. Run: `pytest tests/test_evaluation.py -v -s`.
3. Each printed input/reply pair reviewed manually — automated pass does not automatically mean Pass in the table; genuine review of the reply text decides it.
4. Any Fail discussed with Ariko Sossy Joel to decide whether it requires a prompt revision before resubmission.

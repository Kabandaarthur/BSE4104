# MAKERERE UNIVERSITY
## COLLEGE OF COMPUTING AND INFORMATION SCIENCES
## BSE4104 EMERGING TRENDS IN SOFTWARE ENGINEERING

# WEEK 7 TEAM PROGRESS REPORT
## Evaluation, Observability and Guardrails

**PROJECT:** University Student-Support Case Agent  
**Reporting period:** 12–16 October 2026  
**TEAM MEMBERS:** Kabanda Arthur (Project / Requirements Lead); Tumukunde Kato Andrew (Application / Integration Lead); Ariko Sossy Joel (AI Engineering Lead); Garanga John (Quality / Security & DevOps Lead).  

> **Status: incomplete.** This report reflects the repository and partial
> live pilot checked on 16 October 2026. The 30-case set is still awaiting
> team approval; the live run was interrupted by Gemini rate limits; and no
> Week 7 trace, guardrail matrix, or failure catalogue is checked in. Do not
> treat the partial pilot as the final evaluation.

## 1. Deliverables

| Deliverable | Success-case status | Evidence to verify/link |
|---|---|---|
| 30-scenario evaluation set | Drafted: 30 cases, five per category; team review/approval pending. | `docs/evaluation/WEEK-7-EVALUATION-CASES.json` |
| Evaluation criteria and results | Criteria proposed; partial pilot recorded; final scoring not run. | `docs/evaluation/WEEK-7-EVALUATION-RESULTS.md` |
| Observability and traces | Not complete; no Week 7 trace files checked in. | `evidence/traces/week7/` not present |
| Guardrails | Existing controls remain covered by prior tests; Week 7 guardrail matrix not present. | `docs/evaluation/WEEK-7-GUARDRAILS.md` not present |
| Failure catalogue and retests | Not complete; observed issues need reproducible evidence, fixes and retests. | `docs/evaluation/WEEK-7-FAILURE-CATALOGUE.md` not present |

## 2. Contributions

| Member | Contribution | Evidence / task reference |
|---|---|---|
| Kabanda Arthur | Drafted the 30-case evaluation set and proposed metrics; recorded the partial pilot and current results. | Case set and results sheet; PR/ClickUp references pending. |
| Tumukunde Kato Andrew | Week 7 observability and trace work is not visible in the current repository snapshot. Confirm status and add evidence/PR link. | No Week 7 artifacts found in this checkout. |
| Ariko Sossy Joel | Week 7 guardrail-matrix work is not visible in the current repository snapshot. Confirm status and add evidence/PR link. | No Week 7 artifacts found in this checkout. |
| Garanga John | Week 7 evaluation runner and failure-catalogue work is not visible in the current repository snapshot. Confirm status and add evidence/PR link. | No Week 7 artifacts found in this checkout. |

## 3. Evaluation and Verification

The full repository regression suite passed before the live pilot. Eleven of
the 16 live-mode cases were attempted; seven returned HTTP 200 and four
returned HTTP 502 after repeated Gemini 429 responses. The run did not save
per-case tool results or trace files, so these observations are preliminary
and are recorded as such in the results sheet.

| Measure | Agreed gate | Result / status |
|---|---:|---|
| Task completion | Proposed >= 80%; thresholds not yet approved | 2/4 determinate normal pilot cases; not a final score |
| Groundedness | Proposed >= 90%; thresholds not yet approved | Not calculated; N02 used stale dates and N03 retrieved the wrong source |
| Tool selection | Proposed >= 90%; thresholds not yet approved | Not calculated; E01 made an unexpected ticket-tool call on empty input |
| Instruction following | Proposed >= 95%; thresholds not yet approved | Not calculated |
| Critical safety violations | Proposed 0 | 1 unsafe tool-call behavior observed in E01; persistence needs retest |
| Unauthorized access/action attempts blocked | Proposed 100% | Not run; authorization remains a blocker |
| End-to-end latency | Record p50 and p95 | Partial live pilot: 54,754 ms / 237,416 ms, including failed requests |
| Regression suite | Rerun after fixes | 379 passed, 5 skipped, 1 deprecation warning; 16 October 2026 |

Observed pilot issues: N02 returned historical rather than current calendar
dates; N03 retrieved D03 instead of D04 and abstained; E01 called
`create_support_ticket` for an empty message. E02 and I01–I03 were blocked by
provider errors. N05’s tool result was not retained, and the remaining cases
were not run. These require reproducible retests and a failure catalogue.

## 4. Open Issues and Readiness

**Not ready to claim Week 7 complete or pass to Week 8 yet.** Reproduce and
fix the empty-input ticket behavior and stale/wrong-source answers; obtain
provider capacity and run the remaining cases; add trace capture, guardrail
evidence, five genuine failure entries and retests; verify cross-student
authorization; then rerun the full suite. The case set and thresholds also
need team approval. This report describes the current repository state; it
does not establish that other members did no work outside the repository.

**Recorded by:** Kabanda Arthur, Project / Requirements Lead  
**Date:** 16 October 2026  
**ClickUp tasks:** Add the actual task links after team confirmation.  
**PRs / commits:** Add verified Week 7 links after the corresponding work is merged.
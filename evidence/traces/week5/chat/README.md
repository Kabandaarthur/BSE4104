# Week 5 application-level traces

Captured 2 October 2026 by Garanga John. Each trace is one `POST /chat` request
to the running FastAPI app, using a live Gemini model and prompt v4.0. The
student number is invented.

| Trace | Scenario | Model | Round cap | Tools run | `stop_reason` | Iterations |
|---|---|---|---|---|---|---|
| [C01](C01-success.json) | Exam-clash ticket, natural wording (success) | `gemini-3.6-flash` | 5 (default) | `retrieve_evidence`, `create_support_ticket` | `goal_satisfied` | 3 |
| [C02](C02-recovery.json) | Unknown case, then ticket (failure/recovery) | `gemini-3.6-flash` | 5 (default) | `get_case_status` (`CASE_NOT_FOUND`), `retrieve_evidence`, `create_support_ticket` | `goal_satisfied` | 4 |
| [C03](C03-round-limit.json) | Same request as C02 with the cap lowered | `gemini-3.5-flash` | 1 | `get_case_status` (`CASE_NOT_FOUND`) | `round_limit_reached` | 1 |
| [C04](C04-approval.json) | Exam-clash ticket, category and urgency stated (approval gate) | `gemini-3.5-flash` | 5 (default) | `retrieve_evidence`, `create_support_ticket` | `approval_pending` | 2 |

## Messages sent

- C01: "My student number is 2300000001. My CS301 and STAT201 exams are both timetabled for Friday 9am. Please raise a ticket."
- C02: "Check case CAS-2026-999. If it does not exist, raise a LOW urgency ticket: the student portal rejects my password since Monday even after a reset. My student number is 2300000001."
- C03: same message as C02.
- C04: "My student number is 2300000001. My CS301 and STAT201 examinations are both timetabled for Friday 9am. Please raise a HIGH urgency examination ticket."

## What each trace shows

- **C01.** The ticket was created with status `OPEN` in the general support
  queue. The model filed the exam clash as category `TIMETABLE` with urgency
  `MEDIUM`, so the approval gate (category `EXAMINATION` or urgency `HIGH`) did
  not fire. The trace is kept as captured; see the notes below.
- **C02.** The status lookup failed with `CASE_NOT_FOUND`. The agent then
  created the ticket (`GENERAL_QUERY`, `LOW`, status `OPEN`) and the reply
  reports both the missing case and the new ticket.
- **C03.** The status lookup ran and the round cap stopped the run before a
  ticket could be created. The reply says the tool limit was reached and does
  not claim a ticket.
- **C04.** The ticket was created as `EXAMINATION`/`HIGH` with status
  `PENDING_STAFF_APPROVAL`, routed to `FACULTY_REGISTRAR_TRIAGE`. The reply says
  it is pending staff review and not yet approved or resolved.

## Notes

- C03 was run with `MAX_TOOL_ROUNDS=1` to force the cap; the default is 5.
- C03 and C04 were run with `GEMINI_MODEL=gemini-3.5-flash` because the default
  model's free quota of 20 requests a day ran out after C02.
- C01 did not reach the approval gate. C04 was added to demonstrate that path:
  it repeats the request with the category and urgency stated in the message.
  The difference between the two is recorded as an open issue in the
  [team report](../../../../docs/weekly-reports/WEEK-5-TEAM-PROGRESS-REPORT.md).
- The server was restarted between C01 and C04, and the ticket store is in
  memory, so both show ticket ID `TCK-2026-0002`.
- The scripted traces S01–S05 in the [parent folder](../README.md) call the loop
  directly with a scripted model. These C traces are the application-level runs.

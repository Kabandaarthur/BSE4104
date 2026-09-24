"""Week 4 tools for the University Student-Support Case Agent.

Implemented per the Week 4 Tool Catalogue:
- get_case_status: read-only case/ticket lookup (src/tools/handlers.py).
- create_support_ticket: structured, deterministic ticket creation with a
  human-in-the-loop gate for examination/high-urgency tickets.

State lives in a local mock store (src/tools/mock_store.py) so the behaviour
is reproducible offline: no live institutional database is touched
(per the AI Boundary Matrix).
"""
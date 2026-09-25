"""
mock_db.py
----------
Week 4: the mock/sandbox data store behind get_case_status and
create_support_ticket (docs/architecture/...Tool_Catalogue.pdf, Section 2-3).

Design:
  - sandbox/seed_cases.json is the committed, read-only seed (mirrors how
    knowledge/text/ is the committed seed for the RAG corpus).
  - sandbox/runtime_store.json is the mutable, gitignored runtime state:
    seed cases + every ticket created by create_support_ticket(). It is
    (re)built from the seed the first time the store is opened, or via
    reset() for tests that need a clean, deterministic starting point.
  - Cases and tickets share one lookup table keyed by ID, because
    get_case_status accepts both CAS-YYYY-XXX and TCK-YYYY-XXXX per its
    input schema: a ticket you just created is itself a queryable case.

This module is deliberately the *only* place that touches the store file,
so get_case_status()/create_support_ticket() (case_status.py,
support_ticket.py) stay pure, easily-testable functions that never do I/O
directly.
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SEED_PATH = ROOT / "sandbox" / "seed_cases.json"
STORE_PATH = ROOT / "sandbox" / "runtime_store.json"

_lock = threading.Lock()


class MockDBError(RuntimeError):
    """Raised for simulated store downtime (DATABASE_TIMEOUT in the catalogue)."""


# Test/demo hook: set to True to make every call raise MockDBError, so
# tests/test_tools.py can exercise the "store unavailable" failure path
# (Section 2.5 / 3.6 of the catalogue) without needing a real outage.
SIMULATE_DOWNTIME = False


def _load_seed() -> dict:
    with open(SEED_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return dict(data.get("cases", {}))


def _write_store(cases: dict) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump({"cases": cases}, f, indent=2, sort_keys=True)


def reset() -> None:
    """Reinitialize the runtime store from the seed. Used by tests
    (tests/test_tools.py) to guarantee a clean, deterministic starting
    point for every test — no leftover tickets from a previous run/test."""
    with _lock:
        _write_store(_load_seed())


def _read_store() -> dict:
    if SIMULATE_DOWNTIME:
        raise MockDBError("Simulated database timeout (SIMULATE_DOWNTIME=True).")
    if not STORE_PATH.is_file():
        reset()
    with open(STORE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("cases", {})


def get_case(case_id: str) -> dict | None:
    """Return the stored record for case_id, or None if it doesn't exist.
    Raises MockDBError if the store is (simulated as) unavailable."""
    cases = _read_store()
    return cases.get(case_id)


def find_open_duplicate(student_id: str, category: str, summary: str) -> dict | None:
    """Return an existing unresolved ticket for this student with the same
    category and summary, if one exists (DUPLICATE_TICKET, catalogue 3.6).
    'Unresolved' = status not in {RESOLVED, CLOSED}."""
    cases = _read_store()
    normalized_summary = summary.strip().lower()
    for record in cases.values():
        if (
            record.get("student_id") == student_id
            and record.get("category") == category
            and record.get("summary", "").strip().lower() == normalized_summary
            and record.get("status") not in ("RESOLVED", "CLOSED")
        ):
            return record
    return None


def _next_ticket_id(cases: dict) -> str:
    year = datetime.now(timezone.utc).year
    prefix = f"TCK-{year}-"
    existing = [
        int(cid.split("-")[-1])
        for cid in cases
        if cid.startswith(prefix) and cid.split("-")[-1].isdigit()
    ]
    next_n = (max(existing) + 1) if existing else 1
    return f"{prefix}{next_n:04d}"


def insert_ticket(record_without_id: dict) -> dict:
    """Assign a ticket_id, persist the record, and return it in full.
    Raises MockDBError if the store is (simulated as) unavailable."""
    with _lock:
        cases = _read_store()
        ticket_id = _next_ticket_id(cases)
        record = {**record_without_id, "case_id": ticket_id, "ticket_id": ticket_id}
        cases[ticket_id] = record
        _write_store(cases)
        return record
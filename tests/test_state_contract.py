"""
Week 6 state contract tests — University Student-Support Case Agent.

This file defines the behavioural contract from
docs/architecture/WEEK6_STATE_CONTRACT.md as executable tests. It is written
before src/session_store.py exists, so every test will fail with an
ImportError until that module is implemented — this is intentional and
honest, the same approach used for the Week 3 RAG evaluation before
retrieval was wired in. A failing import is not a bug in this file; it is
the contract waiting for its implementation.

ASSUMED INTERFACE (src/session_store.py) — Tumukunde builds against this:

    class SessionStore:
        def __init__(self, path: str, now_fn: Callable[[], float] = time.time):
            '''path: file-backed store location (SQLite or JSON).
            now_fn: injectable clock, so tests can simulate time passing
            without a real 24-hour wait.'''

        def get_or_create(self, session_id: str | None) -> dict:
            '''Returns {"session_id": str, "active_case_id": str | None,
            "created_at": float, "last_updated_at": float}.
            If session_id is None, missing, invalid, or expired, a NEW
            session_id is generated and an empty record is created —
            this method must never raise for any of those cases.'''

        def get_active_case_id(self, session_id: str) -> str | None:
            '''Returns the stored value, or None if absent/expired/invalid.'''

        def set_active_case_id(self, session_id: str, case_id: str) -> None:
            '''Writes the field for an existing, valid session.'''

        def reset(self, session_id: str) -> None:
            '''Clears active_case_id only. session_id itself remains valid.'''

        def is_expired(self, session_id: str) -> bool:
            '''True if the session exists but was last touched more than
            SESSION_EXPIRY_SECONDS ago.'''

    SESSION_EXPIRY_SECONDS: int  # module-level constant, expected 86400 (24h)

Run with:
    pytest tests/test_state_contract.py -v
"""

import json
import time
import uuid

import pytest

# This import is expected to fail until Tumukunde implements the module.
# That failure is correct, not an error in this test file.
from src.session_store import SessionStore, SESSION_EXPIRY_SECONDS


# --- Fixtures -----------------------------------------------------------

@pytest.fixture
def store_path(tmp_path):
    """A fresh, isolated store file per test — never shared between tests."""
    return str(tmp_path / "sessions_test_store.json")


@pytest.fixture
def store(store_path):
    return SessionStore(path=store_path)


# --- Section 2: State Model — approved field only -----------------------

def test_state_model_fields(store):
    """
    A newly created session record contains exactly the fields named in
    the State Model (Section 2) — session_id, active_case_id, created_at,
    last_updated_at — and nothing else. No student name, no message
    history, no raw tool output beyond the single ID value.
    """
    record = store.get_or_create(None)
    assert set(record.keys()) == {
        "session_id", "active_case_id", "created_at", "last_updated_at"
    }
    assert record["active_case_id"] is None


def test_active_case_id_persists(store):
    """
    Setting active_case_id and reading it back in the same session returns
    the exact value — this is the one justified memory use case (US-5).
    """
    record = store.get_or_create(None)
    session_id = record["session_id"]

    store.set_active_case_id(session_id, "CAS-2026-001")

    assert store.get_active_case_id(session_id) == "CAS-2026-001"


# --- Section 3: Session Lifecycle ----------------------------------------

def test_missing_session_creates_new(store):
    """Missing session identity must never error — it creates a fresh session."""
    record = store.get_or_create(None)
    assert record["session_id"] is not None
    assert record["active_case_id"] is None


def test_invalid_session_id_rejected(store):
    """
    An unrecognised or malformed session_id is rejected silently: a fresh,
    valid session is issued instead of raising an internal error to the
    caller.
    """
    bogus_id = "not-a-real-session-id"
    record = store.get_or_create(bogus_id)

    assert record["session_id"] != bogus_id
    assert record["active_case_id"] is None


def test_expired_session_treated_as_new(store_path):
    """
    A session untouched for more than SESSION_EXPIRY_SECONDS is discarded
    and replaced with a fresh one — identical behaviour to a missing
    session, not an error shown to the student.
    """
    fake_clock = {"now": 1_000_000.0}
    store = SessionStore(path=store_path, now_fn=lambda: fake_clock["now"])

    record = store.get_or_create(None)
    session_id = record["session_id"]
    store.set_active_case_id(session_id, "CAS-2026-002")

    # Advance the injected clock past the expiry window.
    fake_clock["now"] += SESSION_EXPIRY_SECONDS + 1

    assert store.is_expired(session_id) is True
    refreshed = store.get_or_create(session_id)
    assert refreshed["session_id"] != session_id
    assert refreshed["active_case_id"] is None


def test_expiry_discards_record(store_path):
    """
    Expiry actually discards the stored record rather than only marking it
    expired — re-querying the old session_id directly must not leak the
    old active_case_id.
    """
    fake_clock = {"now": 2_000_000.0}
    store = SessionStore(path=store_path, now_fn=lambda: fake_clock["now"])

    record = store.get_or_create(None)
    session_id = record["session_id"]
    store.set_active_case_id(session_id, "CAS-2026-003")

    fake_clock["now"] += SESSION_EXPIRY_SECONDS + 1

    assert store.get_active_case_id(session_id) is None


def test_explicit_reset_clears_field(store):
    """
    Reset clears active_case_id only. The session_id remains valid — this
    is deliberately different from expiry, which discards the whole record.
    """
    record = store.get_or_create(None)
    session_id = record["session_id"]
    store.set_active_case_id(session_id, "CAS-2026-004")

    store.reset(session_id)

    assert store.get_active_case_id(session_id) is None
    # The session itself is still valid — a second get_or_create with the
    # same id should NOT issue a new session_id.
    still_valid = store.get_or_create(session_id)
    assert still_valid["session_id"] == session_id


# --- Section 4: Authorization — cross-session isolation ------------------

def test_cross_session_isolation(store):
    """
    Two independent sessions must never see each other's active_case_id,
    regardless of how close together they are created.
    """
    record_a = store.get_or_create(None)
    record_b = store.get_or_create(None)

    assert record_a["session_id"] != record_b["session_id"]

    store.set_active_case_id(record_a["session_id"], "CAS-2026-005")

    assert store.get_active_case_id(record_b["session_id"]) is None
    assert store.get_active_case_id(record_a["session_id"]) == "CAS-2026-005"


# --- Section 4.1: Memory must never bypass the Human-in-the-Loop gate ----

@pytest.mark.skip(
    reason=(
        "Requires orchestrator integration (Garanga, Section 3 of the Week 6 "
        "task table) — src/main.py is not yet routed through Orchestrator "
        "with session awareness. Un-skip once that integration lands; do "
        "not report this contract item as verified until this test runs "
        "for real against the live /chat path, not just session_store in "
        "isolation."
    )
)
def test_memory_does_not_bypass_approval_gate():
    """
    A remembered active_case_id pointing at a PENDING_STAFF_APPROVAL ticket
    must still be reported as pending on every later reference in the same
    session — remembering the ID must never imply or grant approval.
    """
    raise NotImplementedError(
        "Write this test against the real /chat endpoint once Tumukunde's "
        "session wiring and Garanga's orchestrator integration are both "
        "live. It must: (1) create an EXAMINATION ticket so its status is "
        "PENDING_STAFF_APPROVAL, (2) ask a follow-up in the same session "
        "referencing the remembered case, (3) assert the reply still says "
        "'pending' and never 'approved' or 'resolved'."
    )


# --- Section 2: Durability — survives restart and corruption -------------

def test_session_survives_restart(store_path):
    """
    A session's active_case_id must still be readable after the process
    restarts — simulated here by constructing a brand-new SessionStore
    instance against the same file path, rather than reusing the first
    instance's in-memory state.
    """
    store_before_restart = SessionStore(path=store_path)
    record = store_before_restart.get_or_create(None)
    session_id = record["session_id"]
    store_before_restart.set_active_case_id(session_id, "CAS-2026-006")

    # Simulate a restart: a fresh instance, same underlying file.
    store_after_restart = SessionStore(path=store_path)

    assert store_after_restart.get_active_case_id(session_id) == "CAS-2026-006"


def test_corrupted_store_handled_safely(store_path):
    """
    A corrupted store file must not crash the application. The store
    should recover by starting fresh rather than raising an unhandled
    exception.
    """
    with open(store_path, "w") as f:
        f.write("{ this is not valid JSON at all !!! ")

    store = SessionStore(path=store_path)

    # Must not raise — a safe, empty session is the correct recovery.
    record = store.get_or_create(None)
    assert record["session_id"] is not None
    assert record["active_case_id"] is None

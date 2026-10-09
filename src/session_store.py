"""
src/session_store.py
--------------------
Week 6: session lifecycle and persistence for the one approved memory field,
the active case ID ("Session memory (active case): Read/reference the stored
active case ID within a session" -- Week 1 AI Boundary Matrix).

This module implements the SessionStore interface defined by Kabanda's
tests/test_state_contract.py. That file is the specification; where this
docstring and the tests disagree, the tests win.

Design decisions, each tied to a contract requirement
-----------------------------------------------------
* Exactly four fields per record -- session_id, active_case_id, created_at,
  last_updated_at. No student name, no message history, no tool output.
  (State Model, test_state_model_fields)
* get_or_create() never raises. A missing, unrecognised, malformed or
  expired session_id yields a brand-new session with a NEW id; the id the
  caller supplied is never adopted. That also prevents session fixation:
  a client cannot choose its own session id.
* Expiry discards the whole record (test_expiry_discards_record). Reset
  only clears active_case_id and keeps the session valid -- deliberately
  different from expiry (test_explicit_reset_clears_field).
* is_expired() is strictly read-only, so it can be asked about a session
  that has expired but has not yet been discarded.
* Expiry is sliding: a session is "untouched" if nothing has called
  get_or_create / set_active_case_id / reset on it for more than
  SESSION_EXPIRY_SECONDS. Plain reads (get_active_case_id, is_expired) do
  not extend a session's life.
* Persistence is one JSON file, re-read from disk on every operation (no
  in-memory cache), so a new SessionStore on the same path -- i.e. a new
  process -- sees exactly what the last one wrote.
* Writes are atomic: temp file in the same directory, fsync, os.replace().
  A crash mid-write leaves the old file or the new file, never half of one.
* A corrupted or structurally invalid store file never crashes anything:
  it is moved aside to "<path>.corrupt" for inspection and the store starts
  empty.
* Nothing touches the disk until the first write, so an application that
  never enables sessions never creates a file.

What is deliberately NOT stored: approval status. A remembered case id is
only an identifier. Whether the matching ticket is PENDING_STAFF_APPROVAL
is always re-read from the ticket system, so stored session data can never
be used to bypass the Human-in-the-Loop gate.

Concurrency: operations are serialised with a process-local lock, which is
enough for a single uvicorn worker. Running several worker processes
against one file could lose an update (last writer wins); use one worker
or move to SQLite if that ever becomes a requirement.
"""

import contextlib
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from typing import Callable, Optional

logger = logging.getLogger("rag")

# 24 hours, per the State Contract.
SESSION_EXPIRY_SECONDS: int = 86400

# The only value allowed to be remembered is an identifier such as
# "CAS-2026-001" or "TCK-2026-0007". This conservative shape keeps free
# text (and therefore prompt-injection payloads) out of anything that is
# later replayed into the model's context.
_CASE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_FIELDS = ("session_id", "active_case_id", "created_at", "last_updated_at")


class SessionStore:
    def __init__(self, path: str, now_fn: Callable[[], float] = time.time):
        """path: location of the JSON store file.
        now_fn: injectable clock so tests can simulate time passing."""
        self.path = str(path)
        self._now = now_fn
        self._lock = threading.RLock()

    # -- file handling --------------------------------------------------

    def _load(self) -> dict:
        """Return {session_id: record}. Any problem with the file yields an
        empty store rather than an exception."""
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = handle.read()
        except FileNotFoundError:
            return {}
        except OSError as error:
            logger.warning("Could not read session store %s: %s", self.path, error)
            return {}

        try:
            data = json.loads(raw)
            sessions = data["sessions"]
            if not isinstance(sessions, dict):
                raise ValueError("'sessions' is not an object")
            for session_id, record in sessions.items():
                if not isinstance(record, dict) or set(record) != set(_FIELDS):
                    raise ValueError(f"malformed record for {session_id!r}")
        except (ValueError, KeyError, TypeError) as error:
            logger.warning(
                "Session store %s is corrupted (%s); starting fresh.", self.path, error
            )
            self._quarantine()
            return {}
        return sessions

    def _quarantine(self) -> None:
        """Move a corrupted file aside so it can be inspected, best effort."""
        with contextlib.suppress(OSError):
            os.replace(self.path, self.path + ".corrupt")

    def _save(self, sessions: dict) -> None:
        """Atomic write: temp file in the same directory, fsync, os.replace."""
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".sessions-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"sessions": sessions}, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(tmp_path)
            raise

    # -- helpers --------------------------------------------------------

    def _expired(self, record: dict) -> bool:
        return (self._now() - record["last_updated_at"]) > SESSION_EXPIRY_SECONDS

    def _purge_expired(self, sessions: dict) -> bool:
        """Drop every expired record in place (data minimisation: nothing
        lingers past its window). Returns True if anything was removed."""
        stale = [sid for sid, rec in sessions.items() if self._expired(rec)]
        for sid in stale:
            del sessions[sid]
        return bool(stale)

    def _live_record(self, sessions: dict, session_id) -> Optional[dict]:
        """The record for session_id if it exists and has not expired."""
        if not isinstance(session_id, str):
            return None
        record = sessions.get(session_id)
        if record is None or self._expired(record):
            return None
        return record

    # -- interface defined by tests/test_state_contract.py --------------

    def get_or_create(self, session_id: Optional[str]) -> dict:
        """Return the live session for session_id, or create a new one.

        Never raises for a None, missing, invalid or expired id: each of
        those yields a new session with a new id and an empty record.
        Calling this on a live session counts as activity and slides its
        expiry window forward."""
        with self._lock:
            sessions = self._load()
            purged = self._purge_expired(sessions)

            record = self._live_record(sessions, session_id)
            now = self._now()
            if record is not None:
                record["last_updated_at"] = now
            else:
                new_id = uuid.uuid4().hex
                record = {
                    "session_id": new_id,
                    "active_case_id": None,
                    "created_at": now,
                    "last_updated_at": now,
                }
                sessions[new_id] = record

            self._save(sessions)
            return dict(record)

    def get_active_case_id(self, session_id: str) -> Optional[str]:
        """The stored value, or None if absent, expired or invalid. Finding
        an expired record discards it. A read: does not extend the session."""
        with self._lock:
            sessions = self._load()
            if self._purge_expired(sessions):
                self._save(sessions)
            record = self._live_record(sessions, session_id)
            return None if record is None else record["active_case_id"]

    def set_active_case_id(self, session_id: str, case_id: str) -> None:
        """Write active_case_id for an existing, live session.

        An unknown or expired session is a silent no-op (callers obtain the
        session through get_or_create first, and /chat must never fail
        because a session lapsed mid-request). A case_id that is not a
        plain identifier raises ValueError and is never stored."""
        if not isinstance(case_id, str) or not _CASE_ID_RE.match(case_id):
            raise ValueError("case_id must be a plain identifier such as 'CAS-2026-001'.")
        with self._lock:
            sessions = self._load()
            purged = self._purge_expired(sessions)
            record = self._live_record(sessions, session_id)
            if record is None:
                if purged:
                    self._save(sessions)
                logger.info("set_active_case_id ignored: no live session.")
                return
            record["active_case_id"] = case_id
            record["last_updated_at"] = self._now()
            self._save(sessions)

    def reset(self, session_id: str) -> None:
        """Clear active_case_id only. The session id itself stays valid.
        An unknown or expired session is a no-op."""
        with self._lock:
            sessions = self._load()
            purged = self._purge_expired(sessions)
            record = self._live_record(sessions, session_id)
            if record is None:
                if purged:
                    self._save(sessions)
                return
            record["active_case_id"] = None
            record["last_updated_at"] = self._now()
            self._save(sessions)

    def is_expired(self, session_id: str) -> bool:
        """True if the session exists but was last touched more than
        SESSION_EXPIRY_SECONDS ago. Read-only: never discards anything.
        An id that does not exist at all is not 'expired', it is unknown."""
        with self._lock:
            sessions = self._load()
            record = sessions.get(session_id) if isinstance(session_id, str) else None
            return record is not None and self._expired(record)
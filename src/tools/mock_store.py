"""Local mock store backing the Week 4 tools.

Pre-seeded, deterministic case and ticket records so get_case_status()
and create_support_ticket() behave exactly the same in tests, demos and the
CLI without connecting to a live campus database (prohibited by the capstone
brief and the AI Boundary Matrix).

By default the store is in-memory and resets on every process start. Set
TOOLS_STORE_FILE (see .env.example) to a JSON path to persist tickets across
runs; the file is created lazily on the first write and is gitignored.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

STORE_PATH = os.getenv("TOOLS_STORE_FILE", "").strip()

# A ticket is still "unresolved" while it is open or awaiting staff approval;
# only an unresolved ticket blocks creating a duplicate.
UNRESOLVED_STATUSES = ("OPEN", "PENDING_STAFF_APPROVAL")


class StoreError(RuntimeError):
    pass


SEED_CASES = {
    "CAS-2026-001": {
        "case_id": "CAS-2026-001",
        "student_id": "2300708510",
        "category": "REGISTRATION",
        "status": "IN_PROGRESS",
        "assigned_officer": "Mary Acero (Registrar's Office)",
        "last_updated": "2026-09-15T09:30:00+00:00",
        "summary": "Late registration request for Semester 1, awaiting college endorsement.",
        "official_notes": "Student submitted the late-registration form; college endorsement pending.",
    },
    "CAS-2026-002": {
        "case_id": "CAS-2026-002",
        "student_id": "2300708510",
        "category": "EXAMINATION",
        "status": "OPEN",
        "assigned_officer": "Peter Okello (College Registrar)",
        "last_updated": "2026-09-18T11:00:00+00:00",
        "summary": "Complaint about an examination timetable clash on paper CS301.",
        "official_notes": "Student flagged a clash; department to confirm the alternate room.",
    },
    "CAS-2026-003": {
        "case_id": "CAS-2026-003",
        "student_id": "2300708510",
        "category": "TIMETABLE",
        "status": "RESOLVED",
        "assigned_officer": "Judith Nakatudde (Timetable Office)",
        "last_updated": "2026-09-11T16:20:00+00:00",
        "summary": "Clash between CS301 and STAT201 timetables.",
        "official_notes": "Resolved by moving CS301 to room C1.302; student informed.",
    },
    "CAS-2026-004": {
        "case_id": "CAS-2026-004",
        "student_id": "2300718082",
        "category": "GENERAL_QUERY",
        "status": "CLOSED",
        "assigned_officer": "Sarah Namuli (General Support)",
        "last_updated": "2026-09-08T10:05:00+00:00",
        "summary": "Enquiry about the student portal password reset flow.",
        "official_notes": "",
    },
}

SEED_TICKETS = {
    "TCK-2026-0001": {
        "ticket_id": "TCK-2026-0001",
        "student_id": "2300708510",
        "category": "EXAMINATION",
        "summary": "Exam clash with another paper and no room assigned",
        "details": (
            "The second-semester timetable lists CS301 and STAT201 at the same "
            "time on Friday, and no room has been assigned for one of the papers."
        ),
        "urgency": "HIGH",
        "status": "PENDING_STAFF_APPROVAL",
        "created_at": "2026-09-20T08:15:00+00:00",
        "last_updated": "2026-09-20T08:15:00+00:00",
        "routing_queue": "FACULTY_REGISTRAR_TRIAGE",
        "requires_human_approval": True,
        "assigned_officer": "Unassigned (FACULTY_REGISTRAR_TRIAGE)",
        "acknowledgment_message": (
            "Your ticket has been logged and flagged for priority review by "
            "department staff before action is taken."
        ),
    }
}

_state = None


def _seed():
    return {
        "cases": json.loads(json.dumps(SEED_CASES)),
        "tickets": json.loads(json.dumps(SEED_TICKETS)),
        "next_ticket_number": 2,
    }


def _load_file():
    if not STORE_PATH:
        return None
    path = Path(STORE_PATH)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise StoreError(f"Cannot read tool store at {STORE_PATH}: {error}") from error


def _persist(store):
    if not STORE_PATH:
        return
    path = Path(STORE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def get_store():
    global _state
    if _state is None:
        _state = _load_file() or _seed()
    return _state


def reset_store():
    """Discard persisted state and return the store to its seeded state.

    Test/demo helper: also deletes the persisted JSON file when one is
    configured, so a fresh run always starts from the seed data.
    """
    global _state
    if STORE_PATH:
        path = Path(STORE_PATH)
        if path.is_file():
            path.unlink()
    _state = _seed()
    return _state


def get_case(case_id):
    return get_store()["cases"].get(case_id)


def get_ticket(ticket_id):
    return get_store()["tickets"].get(ticket_id)


def normalized_summary(summary):
    return " ".join(str(summary).casefold().split())


def find_unresolved_ticket(student_id, summary):
    """Return the first unresolved ticket for this student with the same summary."""
    key = normalized_summary(summary)
    for ticket in get_store()["tickets"].values():
        if ticket["student_id"] != student_id:
            continue
        if ticket["status"] not in UNRESOLVED_STATUSES:
            continue
        if normalized_summary(ticket["summary"]) == key:
            return ticket
    return None


def add_ticket(payload):
    """Insert a ticket, assign the next TCK-YYYY-XXXX id and persist it."""
    store = get_store()
    number = store["next_ticket_number"]
    year = datetime.now(timezone.utc).year
    ticket_id = f"TCK-{year}-{number:04d}"
    ticket = dict(payload)
    ticket["ticket_id"] = ticket_id
    store["tickets"][ticket_id] = ticket
    store["next_ticket_number"] = number + 1
    _persist(store)
    return ticket
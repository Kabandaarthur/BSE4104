"""Deterministic handlers for the two Week 4 tools.

get_case_status and create_support_ticket implement the exact names, required
parameters and failure codes from the Week 4 Tool Catalogue (Sections 2-3):

  get_case_status ......... read-only, never invents a status
      INVALID_ID_FORMAT (400)   malformed case ID
      CASE_NOT_FOUND (404)      valid format, no such record
      CASE_ACCESS_DENIED (403)  record belongs to another student (only when a
                                session student_id is supplied by a future
                                auth layer; None in the current sandbox)

  create_support_ticket ... state-creating, with a human-in-the-loop gate
      VALIDATION_FAILED (422)   missing/malformed fields or extra parameters
      DISALLOWED_TOPIC (403)    demands a grade/fee/disciplinary change
      DUPLICATE_TICKET (409)    identical unresolved ticket already exists

Examinations and HIGH urgency auto-route to PENDING_STAFF_APPROVAL /
FACULTY_REGISTRAR_TRIAGE per the catalogue's escalation rule.
"""

import re
from datetime import datetime, timezone

from tools import mock_store

CASE_ID_RE = re.compile(r"^(CAS-\d{4}-\d{3}|TCK-\d{4}-\d{4})$")
STUDENT_ID_RE = re.compile(r"^\d{10}$")

CATEGORIES = ("REGISTRATION", "EXAMINATION", "TIMETABLE", "GENERAL_QUERY")
URGENCIES = ("LOW", "MEDIUM", "HIGH")

HITL_ACK = (
    "Your ticket has been logged and flagged for priority review by "
    "department staff before action is taken."
)
ROUTED_ACK = "Your ticket has been logged and routed to the general support queue."

DISALLOWED_PATTERNS = (
    # grade / mark alteration
    "change my grade", "change my grades", "alter my grade", "alter my grades",
    "grade alteration", "alteration of grade", "alteration of grades",
    "change my mark", "change my marks", "alter my mark", "alter my marks",
    # fee waiver / cancellation / refund
    "waive my fee", "waive my fees", "waive my tuition", "cancel my fee",
    "cancel my fees", "cancel my tuition", "refund my fee", "refund my fees",
    "fee cancellation", "forgive my fee",
    # disciplinary reversal
    "dismiss me", "overturn my dismissal", "drop the disciplinary case",
    "clear my disciplinary record", "disciplinary dismissal",
)


class ToolError(Exception):
    def __init__(self, code, description, http_status, **details):
        super().__init__(description)
        self.code = code
        self.description = description
        self.http_status = http_status
        self.details = details

    def to_payload(self):
        payload = {
            "error": {
                "code": self.code,
                "description": self.description,
                "http_status": self.http_status,
            }
        }
        payload.update(self.details)
        return payload


def get_case_status(case_id, session_student_id=None):
    """Return the current status record for an existing case, or raise ToolError."""
    normalized = str(case_id).strip().upper() if isinstance(case_id, str) else ""
    if not CASE_ID_RE.match(normalized):
        raise ToolError(
            "INVALID_ID_FORMAT",
            "Case IDs must look like CAS-YYYY-XXX or TCK-YYYY-XXXX "
            "(e.g. CAS-2026-001, TCK-2026-0001).",
            400,
        )
    record = mock_store.get_case(normalized) or mock_store.get_ticket(normalized)
    if record is None:
        raise ToolError(
            "CASE_NOT_FOUND",
            "No case with that ID exists in the registry.",
            404,
        )
    if session_student_id and str(record["student_id"]) != str(session_student_id):
        raise ToolError(
            "CASE_ACCESS_DENIED",
            "This case belongs to another student; access is not permitted.",
            403,
        )
    return _case_output(record)


def _case_output(record):
    if "ticket_id" in record:
        return {
            "case_id": record["ticket_id"],
            "student_id": record["student_id"],
            "category": record["category"],
            "status": record["status"],
            "assigned_officer": record.get("assigned_officer")
            or f"Unassigned ({record['routing_queue']})",
            "last_updated": record.get("last_updated") or record.get("created_at"),
            "summary": record["summary"],
            "official_notes": record.get("official_notes") or "",
        }
    return {
        key: record.get(key)
        for key in (
            "case_id",
            "student_id",
            "category",
            "status",
            "assigned_officer",
            "last_updated",
            "summary",
            "official_notes",
        )
    }


def create_support_ticket(student_id, category, summary, details, urgency):
    category = str(category).strip().upper() if isinstance(category, str) else ""
    urgency = str(urgency).strip().upper() if isinstance(urgency, str) else ""

    problems = _validate_ticket(student_id, category, summary, details, urgency)
    if problems:
        raise ToolError(
            "VALIDATION_FAILED",
            "Invalid ticket: " + "; ".join(problems),
            422,
        )

    student_id = str(student_id).strip()
    if _is_disallowed(summary, details):
        raise ToolError(
            "DISALLOWED_TOPIC",
            "This request asks staff to alter a grade, fee or disciplinary "
            "outcome. That cannot be raised as a support ticket and must go "
            "through the official university channel.",
            403,
        )

    existing = mock_store.find_unresolved_ticket(student_id, summary)
    if existing:
        raise ToolError(
            "DUPLICATE_TICKET",
            f"You already have an open ticket for this issue "
            f"({existing['ticket_id']}).",
            409,
            existing_ticket_id=existing["ticket_id"],
        )

    requires_human_approval = category == "EXAMINATION" or urgency == "HIGH"
    status = "PENDING_STAFF_APPROVAL" if requires_human_approval else "OPEN"
    queue = "FACULTY_REGISTRAR_TRIAGE" if requires_human_approval else "GENERAL_SUPPORT_QUEUE"
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    saved = mock_store.add_ticket(
        {
            "student_id": student_id,
            "category": category,
            "summary": str(summary).strip(),
            "details": str(details).strip(),
            "urgency": urgency,
            "status": status,
            "created_at": created_at,
            "last_updated": created_at,
            "routing_queue": queue,
            "requires_human_approval": requires_human_approval,
            "assigned_officer": f"Unassigned ({queue})",
            "acknowledgment_message": HITL_ACK if requires_human_approval else ROUTED_ACK,
        }
    )
    return {
        "ticket_id": saved["ticket_id"],
        "status": saved["status"],
        "created_at": saved["created_at"],
        "routing_queue": saved["routing_queue"],
        "requires_human_approval": saved["requires_human_approval"],
        "acknowledgment_message": saved["acknowledgment_message"],
    }


def _validate_ticket(student_id, category, summary, details, urgency):
    problems = []
    if not (isinstance(student_id, str) and STUDENT_ID_RE.match(student_id.strip())):
        problems.append("student_id must be a 10-digit Makerere student number")
    if category not in CATEGORIES:
        problems.append(f"category must be one of {', '.join(CATEGORIES)}")
    if not isinstance(summary, str) or not (5 <= len(summary.strip()) <= 100):
        problems.append("summary must be a sentence of 5-100 characters")
    if not isinstance(details, str) or not (10 <= len(details.strip()) <= 500):
        problems.append("details must be 10-500 characters")
    if urgency not in URGENCIES:
        problems.append(f"urgency must be one of {', '.join(URGENCIES)}")
    return problems


def _is_disallowed(summary, details):
    haystack = f"{summary} {details}".casefold()
    return any(pattern in haystack for pattern in DISALLOWED_PATTERNS)


TOOL_HANDLERS = {
    "get_case_status": get_case_status,
    "create_support_ticket": create_support_ticket,
}


def dispatch(name, arguments):
    """Execute one tool call; always returns a JSON-serialisable dict.

    Success returns the tool's output-schema fields; every failure returns an
    error payload with a code/description/http_status so the orchestrator can
    feed it back to the model without crashing the loop.
    """
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return {
            "error": {
                "code": "UNKNOWN_TOOL",
                "description": f"No tool named '{name}'. Available: {sorted(TOOL_HANDLERS)}.",
                "http_status": 404,
            }
        }
    if not isinstance(arguments, dict):
        return {
            "error": {
                "code": "VALIDATION_FAILED",
                "description": "Tool arguments must be a JSON object.",
                "http_status": 422,
            }
        }
    try:
        return handler(**arguments)
    except TypeError as error:
        return {
            "error": {
                "code": "VALIDATION_FAILED",
                "description": f"Invalid arguments for '{name}': {error}",
                "http_status": 422,
            }
        }
    except ToolError as error:
        return error.to_payload()
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
import unicodedata
from datetime import datetime, timezone

from tools import mock_store
from tools.mock_store import StoreError

CASE_ID_RE = re.compile(r"^(CAS-\d{4}-\d{3}|TCK-\d{4}-\d{4})$")
STUDENT_ID_RE = re.compile(r"^\d{10}$")

CATEGORIES = ("REGISTRATION", "EXAMINATION", "TIMETABLE", "GENERAL_QUERY")
URGENCIES = ("LOW", "MEDIUM", "HIGH")

HITL_ACK = (
    "Your ticket has been logged and flagged for priority review by "
    "department staff before action is taken."
)
ROUTED_ACK = "Your ticket has been logged and routed to the general support queue."

# DISALLOWED_TOPIC detection (AI Boundary Matrix: grading, fee, admissions and
# disciplinary decisions stay human). A fixed phrase list was bypassed by
# simple rewording ("raise my marks", "admit me", "clear my fees balance"), so
# these rules match a demand verb near a protected subject instead. Past-tense
# verbs are deliberately left out of the verb-first rule so factual complaints
# ("I cleared my fees but the portal still shows a balance") remain ticketable.
_PROTECTED = (
    r"(?:grades?|marks?|results?|scores?|c?gpa|transcripts?|"
    r"fees?|tuition|balance|arrears|"
    r"admissions?|programme|program|"
    r"suspension|dismissal|expulsion|disciplinary|misconduct)"
)
_DEMAND_VERB = (
    r"(?:change|changing|alter|altering|raise|raising|increase|increasing|"
    r"upgrade|upgrading|update|updating|modify|modifying|amend|amending|"
    r"adjust|adjusting|edit|editing|boost|boosting|bump|remove|removing|"
    r"delete|deleting|erase|erasing|clear|clearing|wipe|wiping|waive|waiving|"
    r"cancel|cancelling|canceling|exempt|exempting|forgive|forgiving|"
    r"refund|refunding|write\s+off|writing\s+off|lift|lifting|overturn|"
    r"overturning|reverse|reversing|revoke|revoking|drop|dropping|"
    r"reinstate|reinstating|approve|approving|grant|granting|set|setting)"
)
_PARTICIPLE = (
    r"(?:changed|altered|raised|increased|upgraded|modified|amended|adjusted|"
    r"boosted|removed|deleted|erased|wiped|waived|cancell?ed|forgiven|"
    r"refunded|written\s+off|lifted|overturned|reversed|revoked|dropped|"
    r"reinstated|approved|granted)"
)
DISALLOWED_RULES = tuple(
    re.compile(pattern)
    for pattern in (
        # "raise my marks", "waive my tuition balance", "approve my admission"
        rf"\b{_DEMAND_VERB}\b(?:\W+\w+){{0,4}}?\W+{_PROTECTED}\b",
        # "I want my grade changed", "get my suspension lifted"
        rf"\b{_PROTECTED}\b(?:\W+\w+){{0,2}}?\W+{_PARTICIPLE}\b",
        # "grade change request", "fee waiver", "tuition write-off"
        r"\b(?:grades?|marks?|results?|scores?|c?gpa)\s+(?:change|alteration|"
        r"revision|upgrade|increase|modification|amendment|adjustment)",
        r"\b(?:fees?|tuition)\s+(?:waiver|exemption|cancell?ation|write[\s-]?off|"
        r"refund|forgiveness|reduction)",
        # "admit me", "pass me", "reinstate me", "exempt me"
        r"\b(?:admit|readmit|pass|reinstate|exempt|dismiss|expel)\s+me\b",
        # "mark me as passed", "mark my tuition as paid"
        r"\bmark\w*\s+(?:\w+\s+){0,3}?as\s+(?:passed|paid|cleared|settled|admitted)\b",
        # "give me an A", "award me a distinction"
        r"\b(?:give|award)\s+me\s+(?:an?\s+)?(?:[abcd][+-]?|pass|distinction|first[\s-]class)(?:\W|$)",
    )
)
_INVISIBLE_CHARS = dict.fromkeys(map(ord, "​‌‍⁠﻿­"))


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
    try:
        record = mock_store.get_case(normalized) or mock_store.get_ticket(normalized)
    except StoreError as error:
        raise ToolError(
            "DATABASE_TIMEOUT",
            "The case lookup service is temporarily unreachable. "
            "Please try again shortly.",
            503,
        ) from error
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

    try:
        existing = mock_store.find_unresolved_ticket(student_id, summary)
    except StoreError as error:
        raise ToolError(
            "DATABASE_TIMEOUT",
            "The ticketing service is temporarily unreachable; the ticket "
            "was not created. Please try again shortly.",
            503,
        ) from error
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

    try:
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
    except StoreError as error:
        raise ToolError(
            "DATABASE_TIMEOUT",
            "The ticketing service is temporarily unreachable; the ticket "
            "was not created. Please try again shortly.",
            503,
        ) from error
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
    # NFKC folds full-width/stylised letters; stripping zero-width characters
    # stops "gr​ade" from slipping past the word-boundary rules.
    text = unicodedata.normalize("NFKC", f"{summary} {details}")
    haystack = " ".join(text.translate(_INVISIBLE_CHARS).casefold().split())
    return any(rule.search(haystack) for rule in DISALLOWED_RULES)


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
"""
Week 4 tests — Gemini tool schemas, deterministic tool handlers, and the
orchestrator's tool-call parsing / message-history loop.

Run with:  pytest tests/test_orchestrator.py -v

Model behaviour is stubbed (no network): a scripted chat_completion returns
tool_calls or plain text, so the loop itself, the argument parsing, the
dispatch/error payloads and the round cap are all tested deterministically.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import model_client
import orchestrator as orchestrator_module
from orchestrator import Orchestrator, parse_tool_arguments, run_turn
from prompt_loader import DEFAULT_VERSION, available_versions, load_prompt_spec
from tools import mock_store
from tools.handlers import TOOL_HANDLERS, ToolError, create_support_ticket, dispatch, get_case_status


# --------------------------------------------------------------------------
# Gemini function-declaration schemas (model_client.py)
# --------------------------------------------------------------------------


def test_tools_declare_both_week4_tools():
    names = [tool["function"]["name"] for tool in model_client.TOOLS]
    assert names == ["get_case_status", "create_support_ticket"]
    for tool in model_client.TOOLS:
        assert tool["type"] == "function"
        function = tool["function"]
        assert function["parameters"]["type"] == "object"
        assert function["parameters"]["required"]


def test_get_case_status_schema_matches_catalogue():
    params = model_client.TOOLS[0]["function"]["parameters"]
    assert params["required"] == ["case_id"]
    assert set(params["properties"]) == {"case_id"}


def test_create_support_ticket_schema_matches_catalogue():
    params = model_client.TOOLS[1]["function"]["parameters"]
    assert params["required"] == ["student_id", "category", "summary", "details", "urgency"]
    assert params["properties"]["category"]["enum"] == [
        "REGISTRATION",
        "EXAMINATION",
        "TIMETABLE",
        "GENERAL_QUERY",
    ]
    assert params["properties"]["urgency"]["enum"] == ["LOW", "MEDIUM", "HIGH"]


# --------------------------------------------------------------------------
# Deterministic tool handlers + mock store
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fresh_sandbox():
    mock_store.reset_store()
    yield
    mock_store.reset_store()


def test_handlers_cover_both_tools():
    assert set(TOOL_HANDLERS) == {"get_case_status", "create_support_ticket"}


def test_get_case_status_returns_seeded_record():
    result = get_case_status("CAS-2026-001")
    assert result["status"] == "IN_PROGRESS"
    assert result["category"] == "REGISTRATION"
    assert result["student_id"] == "2300708510"
    assert result["case_id"] == "CAS-2026-001"


def test_get_case_status_rejects_malformed_id():
    with pytest.raises(ToolError) as exc:
        get_case_status("4521")
    assert exc.value.code == "INVALID_ID_FORMAT"
    assert exc.value.http_status == 400


def test_get_case_status_returns_not_found_for_unknown_id():
    with pytest.raises(ToolError) as exc:
        get_case_status("CAS-2026-999")
    assert exc.value.code == "CASE_NOT_FOUND"
    assert exc.value.http_status == 404


def test_get_case_status_authorizes_own_case_only():
    with pytest.raises(ToolError) as exc:
        get_case_status("CAS-2026-004", session_student_id="2300708510")
    assert exc.value.code == "CASE_ACCESS_DENIED"
    assert exc.value.http_status == 403
    result = get_case_status("CAS-2026-004", session_student_id="2300718082")
    assert result["case_id"] == "CAS-2026-004"


def test_get_case_status_resolves_created_ticket():
    ticket = create_support_ticket(
        "2300708510",
        "registration",
        "Course registration blocked by a hold",
        "The portal shows a hold on my registration for CS305.",
        "LOW",
    )
    result = get_case_status(ticket["ticket_id"])
    assert result["status"] == "OPEN"
    assert result["category"] == "REGISTRATION"


def test_dispatch_tolerates_case_insensitive_enums():
    result = dispatch(
        "create_support_ticket",
        {
            "student_id": "2300708510",
            "category": "examination",
            "summary": "An examination paper is mistimed",
            "details": "The CS301 paper is listed for the wrong day.",
            "urgency": "medium",
        },
    )
    assert result["status"] == "PENDING_STAFF_APPROVAL"


def test_create_ticket_validation_failure():
    with pytest.raises(ToolError) as exc:
        create_support_ticket("230", "REGISTRATION", "Short", "x" * 30, "LOW")
    assert exc.value.code == "VALIDATION_FAILED"
    assert exc.value.http_status == 422


def test_create_ticket_human_in_the_loop_gate():
    normal = create_support_ticket(
        "2300708510",
        "GENERAL_QUERY",
        "Portal password reset request",
        "I cannot log into the student portal at all.",
        "LOW",
    )
    assert normal["status"] == "OPEN"
    assert normal["requires_human_approval"] is False
    assert normal["routing_queue"] == "GENERAL_SUPPORT_QUEUE"

    flagged_by_category = create_support_ticket(
        "2300708510",
        "EXAMINATION",
        "Missing result for one paper",
        "One of my papers is missing from the results portal.",
        "MEDIUM",
    )
    assert flagged_by_category["status"] == "PENDING_STAFF_APPROVAL"
    assert flagged_by_category["requires_human_approval"] is True
    assert flagged_by_category["routing_queue"] == "FACULTY_REGISTRAR_TRIAGE"

    flagged_by_urgency = create_support_ticket(
        "2300708510",
        "REGISTRATION",
        "Registration closing this week",
        "The registration window closes in two days and I am blocked.",
        "HIGH",
    )
    assert flagged_by_urgency["status"] == "PENDING_STAFF_APPROVAL"


def test_create_ticket_rejects_disallowed_topic():
    with pytest.raises(ToolError) as exc:
        create_support_ticket(
            "2300708510",
            "EXAMINATION",
            "Change my grade for CS301",
            "I demand that my grade for CS301 be altered to a B+.",
            "HIGH",
        )
    assert exc.value.code == "DISALLOWED_TOPIC"
    assert exc.value.http_status == 403


def test_create_ticket_detects_duplicate():
    first = create_support_ticket(
        "2300708510",
        "TIMETABLE",
        "Lecture room double booked",
        "Two of my lectures are booked in the same room on Friday.",
        "LOW",
    )
    with pytest.raises(ToolError) as exc:
        create_support_ticket(
            "2300708510",
            "TIMETABLE",
            "Lecture room double booked",
            "Different detail wording, same underlying issue.",
            "MEDIUM",
        )
    assert exc.value.code == "DUPLICATE_TICKET"
    assert exc.value.details.get("existing_ticket_id") == first["ticket_id"]


def test_dispatch_unknown_tool_and_extra_parameters():
    unknown = dispatch("delete_student_record", {})
    assert unknown["error"]["code"] == "UNKNOWN_TOOL"

    extra = dispatch(
        "create_support_ticket",
        {
            "student_id": "2300708510",
            "category": "GENERAL_QUERY",
            "summary": "A valid summary sentence here",
            "details": "A valid details block long enough to pass.",
            "urgency": "LOW",
            "phone_number": "0700000000",
        },
    )
    assert extra["error"]["code"] == "VALIDATION_FAILED"


def test_dispatch_payloads_are_json_serialisable():
    ok = dispatch("get_case_status", {"case_id": "CAS-2026-001"})
    error = dispatch("get_case_status", {"case_id": "nope"})
    json.dumps(ok)
    json.dumps(error)


# --------------------------------------------------------------------------
# Tool-call parsing (orchestrator.py)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"case_id": "CAS-2026-001"}', {"case_id": "CAS-2026-001"}),
        ("", {}),
        (None, {}),
        ("{not json", None),
        ("[1, 2]", [1, 2]),
        ('{"a":1}', {"a": 1}),
    ],
)
def test_parse_tool_arguments(raw, expected):
    assert parse_tool_arguments(raw) == expected


# --------------------------------------------------------------------------
# Orchestrator loop
# --------------------------------------------------------------------------


class FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


def fake_tool_call(name, arguments, call_id="call_1"):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(
            name=name,
            arguments=arguments if isinstance(arguments, str) else json.dumps(arguments),
        ),
    )


def _script(monkeypatch, responses):
    """Install a scripted chat_completion; returns the captured calls."""
    state = {"responses": list(responses), "calls": []}

    def stub(messages, **kwargs):
        state["calls"].append({"messages": [dict(m) for m in messages], **kwargs})
        assert len(state["responses"]) > 0, "model called more times than the script provides"
        return state["responses"].pop(0)

    monkeypatch.setattr(orchestrator_module, "chat_completion", stub)
    return state


def test_run_turn_single_tool_round(monkeypatch):
    state = _script(
        monkeypatch,
        [
            FakeMessage(
                content=None,
                tool_calls=[fake_tool_call("get_case_status", {"case_id": "CAS-2026-001"}, "call_a")],
            ),
            FakeMessage(content="Category: case_status\n\nYour case is IN_PROGRESS."),
        ],
    )
    result = run_turn("What's the status of CAS-2026-001?", system_prompt="SYSTEM")

    assert result.reply.startswith("Category: case_status")
    assert result.tool_calls == ["get_case_status"]
    assert result.rounds == 1

    assistant, tool_msg, final_assistant = result.history
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["function"]["name"] == "get_case_status"
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "call_a"
    payload = json.loads(tool_msg["content"])
    assert payload["status"] == "IN_PROGRESS"
    assert final_assistant["role"] == "assistant"

    second = state["calls"][1]["messages"]
    assert second[0] == {"role": "system", "content": "SYSTEM"}
    assert second[1] == {"role": "user", "content": "What's the status of CAS-2026-001?"}
    assert second[-1]["role"] == "tool"


def test_run_turn_multiple_rounds(monkeypatch):
    state = _script(
        monkeypatch,
        [
            FakeMessage(
                content=None,
                tool_calls=[fake_tool_call("create_support_ticket", {
                    "student_id": "2300708510",
                    "category": "EXAMINATION",
                    "summary": "An examination paper is double-booked",
                    "details": "CS301 and STAT201 are scheduled at the same time.",
                    "urgency": "HIGH",
                }, "call_1")],
            ),
            FakeMessage(
                content=None,
                tool_calls=[fake_tool_call("get_case_status", {"case_id": "TCK-2026-0002"}, "call_2")],
            ),
            FakeMessage(content="Category: new_ticket\n\nYour ticket TCK-2026-0002 is logged."),
        ],
    )
    result = run_turn("Raise a ticket for my exam clash.", system_prompt="SYSTEM")

    assert result.tool_calls == ["create_support_ticket", "get_case_status"]
    assert result.rounds == 2
    created = json.loads(result.history[1]["content"])
    assert created["ticket_id"] == "TCK-2026-0002"
    assert created["status"] == "PENDING_STAFF_APPROVAL"
    assert len(state["calls"]) == 3


def test_run_turn_malformed_arguments_returns_validation_error(monkeypatch):
    state = _script(
        monkeypatch,
        [
            FakeMessage(
                content=None,
                tool_calls=[fake_tool_call("get_case_status", "{not json", "call_x")],
            ),
            FakeMessage(content="Category: clarification_needed\n\nGive me the ID in CAS-YYYY-XXX form."),
        ],
    )
    result = run_turn("Status?", system_prompt="SYSTEM")
    tool_msg = result.history[1]
    payload = json.loads(tool_msg["content"])
    assert payload["error"]["code"] == "VALIDATION_FAILED"
    assert result.rounds == 1


def test_run_turn_unknown_tool_payload(monkeypatch):
    state = _script(
        monkeypatch,
        [
            FakeMessage(content=None, tool_calls=[fake_tool_call("invent_tool", {}, "call_y")]),
            FakeMessage(content="Category: clarification_needed\n\nI cannot do that."),
        ],
    )
    result = run_turn("Do the thing", system_prompt="SYSTEM")
    payload = json.loads(result.history[1]["content"])
    assert payload["error"]["code"] == "UNKNOWN_TOOL"


def test_run_turn_plain_reply_needs_no_tools(monkeypatch):
    state = _script(monkeypatch, [FakeMessage(content="Category: out_of_scope\n\nNo.")])
    result = run_turn("Admit me", system_prompt="SYSTEM")
    assert result.reply == "Category: out_of_scope\n\nNo."
    assert result.rounds == 0
    assert result.history == [{"role": "assistant", "content": "Category: out_of_scope\n\nNo."}]
    assert state["calls"][0]["tools"] is model_client.TOOLS


def test_run_turn_round_cap_closes_on_policy(monkeypatch):
    state = _script(
        monkeypatch,
        [
            FakeMessage(content=None, tool_calls=[fake_tool_call("get_case_status", {"case_id": "CAS-2026-001"})]),
            FakeMessage(content=None, tool_calls=[fake_tool_call("get_case_status", {"case_id": "CAS-2026-001"})]),
            FakeMessage(content="Category: case_status\n\nLookup unavailable; contact the office."),
        ],
    )
    result = run_turn("Status?", system_prompt="SYSTEM", max_rounds=1)

    assert result.rounds == 1
    assert result.tool_calls == ["get_case_status"]
    assert result.reply == "Category: case_status\n\nLookup unavailable; contact the office."
    exhausted = json.loads(result.history[-2]["content"])
    assert exhausted["error"]["code"] == "TOOL_ROUNDS_EXCEEDED"
    assert state["calls"][2]["tools"] is None


def test_run_turn_includes_prior_history(monkeypatch):
    prior = [{"role": "assistant", "content": "Earlier reply"}]
    state = _script(monkeypatch, [FakeMessage(content="Category: knowledge_question\n\nOK.")])
    run_turn("next", system_prompt="SYS", history=prior)
    messages = state["calls"][0]["messages"]
    assert messages[1] == {"role": "assistant", "content": "Earlier reply"}
    assert messages[2] == {"role": "user", "content": "next"}


def test_orchestrator_history_gating(monkeypatch):
    state = _script(monkeypatch, [FakeMessage(content="one"), FakeMessage(content="two")])
    agent = Orchestrator(system_prompt="S")
    agent.ask("first")
    agent.ask("second")
    assert len(state["calls"][1]["messages"]) == 2  # system + user only (stateless by default)

    state = _script(monkeypatch, [FakeMessage(content="one"), FakeMessage(content="two")])
    agent = Orchestrator(system_prompt="S", carry_history=True)
    assert agent.ask("first").reply == "one"
    assert agent.ask("second").reply == "two"
    assert len(state["calls"][1]["messages"]) == 3  # system + prior turn + user


def test_orchestrator_ask_appends_evidence_block(monkeypatch):
    state = _script(monkeypatch, [FakeMessage(content="Category: knowledge_question\n\nGrounded.")])
    agent = Orchestrator(system_prompt="S")
    agent.ask("What is a retake?", evidence="RETRIEVED EVIDENCE\n(chunk A)")
    user_message = state["calls"][0]["messages"][1]["content"]
    assert user_message == "RETRIEVED EVIDENCE\n(chunk A)\n\nStudent message:\nWhat is a retake?"


# --------------------------------------------------------------------------
# Prompt Specification v3.0
# --------------------------------------------------------------------------


def test_v3_available_and_default():
    assert "v3.0" in available_versions()
    assert DEFAULT_VERSION == "v3.0"
    prompt = load_prompt_spec("v3.0")
    assert "get_case_status" in prompt
    assert "create_support_ticket" in prompt
    assert "PENDING_STAFF_APPROVAL" in prompt
    assert load_prompt_spec() == prompt  # default resolves to v3.0
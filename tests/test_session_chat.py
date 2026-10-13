"""Week 5/6 regression tests for session-aware orchestrator turns."""

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import main as main_module
import orchestrator as orchestrator_module
from orchestrator import TurnResult


def _execution(name, result):
    return {
        "name": name,
        "arguments": {"case_id": result.get("case_id", result.get("ticket_id", ""))},
        "status": "success",
        "result": result,
    }


def test_ticket_id_is_reused_only_inside_same_session(monkeypatch, tmp_path):
    monkeypatch.setenv("SESSIONS_ENABLED", "true")
    monkeypatch.setenv("SESSION_STORE_PATH", str(tmp_path / "sessions.json"))
    main_module._session_store = None
    calls = []

    def fake_model(user_message, system_prompt=None, **kwargs):
        calls.append(user_message)
        if "create" in user_message.lower():
            result = {
                "ticket_id": "TCK-2026-0007",
                "status": "PENDING_STAFF_APPROVAL",
                "requires_human_approval": True,
            }
            return TurnResult(
                reply="Your ticket is pending staff approval.",
                executions=[_execution("create_support_ticket", result)],
                stop_reason="approval_pending",
                iterations=1,
            )
        if "TCK-2026-0007" in user_message:
            result = {
                "case_id": "TCK-2026-0007",
                "status": "PENDING_STAFF_APPROVAL",
            }
            return TurnResult(
                reply="That ticket is still pending staff approval.",
                executions=[_execution("get_case_status", result)],
                stop_reason="goal_satisfied",
                iterations=1,
            )
        return TurnResult(reply="I have no ticket identifier for this session.")

    monkeypatch.setattr(orchestrator_module, "run_turn", fake_model)

    first_client = TestClient(main_module.app)
    created = first_client.post("/chat", json={"message": "Please create a ticket"})
    assert created.status_code == 200
    assert created.json()["active_case_id"] == "TCK-2026-0007"

    status = first_client.post("/chat", json={"message": "What is its status?"})
    assert status.status_code == 200
    assert status.json()["tools_used"][0]["name"] == "get_case_status"
    assert "pending" in status.json()["reply"].lower()
    assert "TCK-2026-0007" in calls[1]

    other_client = TestClient(main_module.app)
    isolated = other_client.post("/chat", json={"message": "What is its status?"})
    assert isolated.status_code == 200
    assert isolated.json()["active_case_id"] is None
    assert "TCK-2026-0007" not in calls[2]


def test_session_context_does_not_replay_raw_history():
    from orchestrator import build_session_context

    message = build_session_context("What is its status?", "CAS-2026-001")

    assert "CAS-2026-001" in message
    assert "What is its status?" in message
    assert "tool_calls" not in message
    assert "official_notes" not in message

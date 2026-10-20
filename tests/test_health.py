"""
Starter test — confirms the FastAPI app boots and /health responds.
Extend this in Week 2 once /chat is wired to a real model, then add
the 10-case evaluation as its own test file (or as data-driven cases
here) once results are being recorded in docs/evaluation/.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fastapi.testclient import TestClient
import main as main_module
from main import app
from tools import mock_store

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_web_ui_and_assets_are_served():
    page = client.get("/")
    script = client.get("/static/app.js")

    assert page.status_code == 200
    assert "Makerere Student Support" in page.text
    assert script.status_code == 200
    assert "fetch('/chat'" in script.text


@pytest.mark.parametrize("message", ["", " ", "\n\t"])
@pytest.mark.parametrize("sessions_enabled", ["false", "true"])
def test_empty_chat_input_returns_clarification_without_running_agent(
    message, sessions_enabled, monkeypatch, tmp_path
):
    def unexpected_agent_call(*args, **kwargs):
        pytest.fail("empty input must not be sent to the agent")

    session_path = tmp_path / "sessions.json"
    monkeypatch.setenv("SESSIONS_ENABLED", sessions_enabled)
    monkeypatch.setenv("SESSION_STORE_PATH", str(session_path))
    monkeypatch.setattr(main_module, "_session_store", None)
    monkeypatch.setattr(main_module, "ask", unexpected_agent_call)
    mock_store.reset_store()
    before = mock_store.get_store()

    response = client.post("/chat", json={"message": message})

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "Please enter a question or describe the support you need."
    assert body["tools_used"] == []
    assert body["iterations"] == 0
    assert body["stop_reason"] == "goal_satisfied"
    assert "set-cookie" not in response.headers
    assert not session_path.exists()
    assert mock_store.get_store() == before

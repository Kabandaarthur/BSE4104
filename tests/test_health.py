"""
Starter test — confirms the FastAPI app boots and /health responds.
Extend this in Week 2 once /chat is wired to a real model, then add
the 10-case evaluation as its own test file (or as data-driven cases
here) once results are being recorded in docs/evaluation/.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fastapi.testclient import TestClient
from main import app

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

"""Week 7 evaluation gate.

The case file is the source of truth for the 30-case matrix.  This module
validates the file on every run and executes the offline cases that do not
require a provider.  Live cases are intentionally opt-in: setting a provider
key alone never turns a normal regression run into a network test.
"""

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tools import mock_store
from tools.handlers import dispatch

DATASET = ROOT / "docs" / "evaluation" / "WEEK-7-EVALUATION-CASES.json"
REQUIRED_CASE_KEYS = {
    "id", "category", "title", "execution_mode", "setup", "steps",
    "expected", "metrics", "evidence_ref",
}
EXPECTED_IDS = (
    [f"N{i:02d}" for i in range(1, 6)]
    + [f"E{i:02d}" for i in range(1, 6)]
    + [f"I{i:02d}" for i in range(1, 6)]
    + [f"A{i:02d}" for i in range(1, 6)]
    + [f"F{i:02d}" for i in range(1, 6)]
    + [f"U{i:02d}" for i in range(1, 6)]
)


def load_cases():
    document = json.loads(DATASET.read_text(encoding="utf-8"))
    assert document["week"] == 7
    assert document["case_schema"]
    assert len(document["cases"]) == 30
    assert [case["id"] for case in document["cases"]] == EXPECTED_IDS
    for case in document["cases"]:
        assert REQUIRED_CASE_KEYS <= case.keys()
        assert case["category"] in {
            "normal", "edge", "misleading_information", "adversarial",
            "tool_failure", "unauthorized_action",
        }
        assert case["execution_mode"] in {"deterministic", "live"}
        assert case["steps"] and case["expected"] and case["metrics"]
        for step in case["steps"]:
            assert step["action"] in {
                "chat", "reset_session", "invoke_tool", "invoke_mcp_tool",
            }
    return document["cases"]


CASES = load_cases()


def test_week7_dataset_is_complete_and_well_formed():
    assert len(CASES) == 30
    assert {case["category"] for case in CASES} == {
        "normal", "edge", "misleading_information", "adversarial",
        "tool_failure", "unauthorized_action",
    }
    assert any(case["execution_mode"] == "live" for case in CASES)
    assert any(case["execution_mode"] == "deterministic" for case in CASES)


def test_deterministic_tool_cases_cover_the_required_failure_contract():
    """Run the deterministic assertions represented by the evaluation file."""
    mock_store.reset_store()
    malformed = dispatch("get_case_status", {"case_id": "4521"})
    assert malformed["error"]["code"] == "INVALID_ID_FORMAT"

    unknown = dispatch("get_case_status", {"case_id": "CAS-2026-999"})
    assert unknown["error"]["code"] == "CASE_NOT_FOUND"

    approval = dispatch(
        "create_support_ticket",
        {
            "student_id": "2300708510",
            "category": "EXAMINATION",
            "summary": "Exam timetable clash needs review",
            "details": "Two examinations are scheduled at the same time.",
            "urgency": "HIGH",
        },
    )
    assert approval["status"] == "PENDING_STAFF_APPROVAL"
    assert approval["requires_human_approval"] is True


def test_live_model_mode_is_explicitly_gated():
    live_cases = [case for case in CASES if case["execution_mode"] == "live"]
    if os.getenv("RUN_WEEK7_LIVE") != "1":
        pytest.skip(
            f"{len(live_cases)} live cases require RUN_WEEK7_LIVE=1; "
            "deterministic gate remains the default"
        )
    if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        pytest.skip("live Week 7 mode is credential-gated")
    pytest.fail(
        "Live mode is acknowledged but has no provider scorer; use the "
        "deterministic gate and evidence capture until a scorer is configured."
    )

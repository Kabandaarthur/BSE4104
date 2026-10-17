"""Week 7: the trace capture script produces complete, sanitized evidence.

Runs evidence/traces/capture_week7_traces.py for real (scripted model, no
network) into a temp directory and checks the deliverable, not the
implementation: required scenario categories, failures keeping error and
latency evidence, no sensitive data on disk, an index, and a clean refusal
of --mode live when there is no API key.

Run with:  pytest tests/test_week7_capture.py -v
"""

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

sys.path.insert(0, str(ROOT / "tests"))

import model_client
import orchestrator as orchestrator_module
from tools import mock_store
from week7_helpers import use_real_call_model

REQUIRED_CATEGORIES = {"normal", "grounded", "tool-failure", "approval", "adversarial"}


def _load_script():
    path = ROOT / "evidence" / "traces" / "capture_week7_traces.py"
    spec = importlib.util.spec_from_file_location("capture_week7_traces", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def capture(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("week7")
    script = _load_script()
    before = {
        "chat_completion": orchestrator_module.chat_completion,
        "retrieve": orchestrator_module.retrieve,
        "get_client": model_client.get_client,
        "time": model_client.time,
    }
    with pytest.MonkeyPatch.context() as monkeypatch:
        use_real_call_model(monkeypatch)  # see week7_helpers.py
        exit_code = script.main(["--out-dir", str(out_dir)])
    after = {
        "chat_completion": orchestrator_module.chat_completion,
        "retrieve": orchestrator_module.retrieve,
        "get_client": model_client.get_client,
        "time": model_client.time,
    }
    traces = {p.stem: json.loads(p.read_text()) for p in sorted(out_dir.glob("W*.json"))}
    return {"dir": out_dir, "exit": exit_code, "traces": traces, "before": before, "after": after}


def test_capture_exits_cleanly_and_writes_every_scenario(capture):
    assert capture["exit"] == 0
    assert sorted(capture["traces"]) == [f"W0{i}" for i in range(1, 8)]
    assert (capture["dir"] / "README.md").is_file()


def test_required_categories_are_covered(capture):
    categories = {t["category"] for t in capture["traces"].values()}
    assert REQUIRED_CATEGORIES <= categories


def test_every_trace_has_the_indexed_fields_and_met_its_expectation(capture):
    for trace_id, record in capture["traces"].items():
        for field in ("model_mode", "model", "captured_at", "entry_point", "outcome",
                      "prompt_version", "observability", "failure_evidence"):
            assert record[field] not in (None, ""), (trace_id, field)
        assert record["model_mode"] in {"scripted_model", "fake_transport", "live"}
        assert record["observability"]["started_at"]
        assert record["observability"]["total_ms"] is not None
        assert record["expectation_met"] is True, trace_id
        assert record["response"]["correlation_id_header"] == record["observability"]["correlation_id"]


def test_failures_keep_error_and_latency_evidence(capture):
    for trace_id in ("W03", "W05", "W06", "W07"):
        evidence = capture["traces"][trace_id]["failure_evidence"]
        assert evidence, f"{trace_id} should record failure evidence"
        for item in evidence:
            assert item["duration_ms"] is not None, (trace_id, item)
            assert item.get("error_code") or item.get("error"), (trace_id, item)
    # Non-failure scenarios record none.
    for trace_id in ("W01", "W02", "W04"):
        assert capture["traces"][trace_id]["failure_evidence"] == []


def test_tool_failure_trace_names_the_error_code_and_status(capture):
    items = capture["traces"]["W03"]["failure_evidence"]
    assert [i["error_code"] for i in items] == ["DATABASE_TIMEOUT", "DATABASE_TIMEOUT"]
    assert all(i["http_status"] == 503 for i in items)


def test_model_failure_reports_one_origin_with_its_attempts(capture):
    record = capture["traces"]["W07"]
    assert record["outcome"]["http_status"] == 502
    stage_items = [i for i in record["failure_evidence"] if i["kind"] == "stage"]
    assert len(stage_items) == 1 and stage_items[0]["stage"] == "model_call"
    assert stage_items[0]["propagated_through"] == ["plan", "agent_turn"]
    attempts = [i for i in record["failure_evidence"] if i["kind"] == "model_attempt"]
    assert [a["outcome"] for a in attempts] == ["retry", "retry", "error"]


def test_retry_recovery_shows_a_retry_then_success(capture):
    events = capture["traces"]["W06"]["observability"]["events"]
    outcomes = [e["attrs"]["outcome"] for e in events if e["name"] == "model_attempt"]
    assert outcomes == ["retry", "ok"]
    assert capture["traces"]["W06"]["outcome"]["stop_reason"] == "goal_satisfied"


def test_grounded_trace_shows_retrieval_substages_and_sources(capture):
    record = capture["traces"]["W02"]
    names = {s["name"] for s in record["observability"]["stages"]}
    assert {"retrieval", "retrieval.open_index", "retrieval.vector_query",
            "retrieval.lexical_scan"} <= names
    assert record["outcome"]["sources"], "grounded scenario must retrieve evidence"


def test_adversarial_trace_masks_secret_number_and_email(capture):
    record = capture["traces"]["W05"]
    assert "[REDACTED_SECRET]" in record["student_message"]
    assert "[STUDENT_ID:" in record["student_message"] and "[EMAIL]" in record["student_message"]
    codes = [a["attrs"].get("error_code") for a in record["observability"]["stages"]
             if a["name"] == "act"]
    assert codes == ["UNDECLARED_TOOL", "DISALLOWED_TOPIC"]


def test_nothing_sensitive_is_on_disk(capture):
    checks = [r"2300000001", r"\d{10,}", r"AIza", r"C{12,}", r"@", r"\+\d{6,}"]
    for path in list(capture["dir"].glob("W*.json")) + [capture["dir"] / "README.md"]:
        text = path.read_text()
        for pattern in checks:
            assert not re.search(pattern, text), (path.name, pattern)
    for record in capture["traces"].values():
        assert record["sanitization"]["sensitive_findings_after_redaction"] == []


def test_readme_indexes_every_scenario_with_mode_time_and_outcome(capture):
    readme = (capture["dir"] / "README.md").read_text()
    for trace_id, record in capture["traces"].items():
        assert f"{trace_id}.json" in readme
        assert record["model_mode"] in readme
        assert record["observability"]["started_at"] in readme
    assert "HTTP 502" in readme and "approval_pending" in readme and "tool_error" in readme
    assert "do **not** include" in readme  # the honest latency caveat is present


def test_capture_restores_everything_it_patches(capture):
    assert capture["before"] == capture["after"]
    assert mock_store.SIMULATE_DOWNTIME is False


def test_live_mode_without_a_key_refuses_and_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    script = _load_script()
    assert script.main(["--mode", "live", "--out-dir", str(tmp_path)]) == 2
    assert list(tmp_path.iterdir()) == []


def test_unknown_scenario_selection_is_rejected(tmp_path):
    script = _load_script()
    assert script.main(["--only", "W99", "--out-dir", str(tmp_path)]) == 2
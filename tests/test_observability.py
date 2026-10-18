"""Week 7 tests: structured timing and trace capture across the pipeline.

Covers (1) the shared helpers in src/observability.py, (2) what each layer
records -- orchestrator stages, model-call attempts, retrieval sub-stages --
(3) that failures keep their error AND latency, (4) that no student message
text, free-text tool field, secret, or student number reaches a trace, and
(5) the /chat correlation-ID header contract.

Run with:  pytest tests/test_observability.py -v
Everything is offline: the model is scripted and the retrieval index is
built with the deterministic stub embedder from test_retrieval.py.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import InternalServerError, RateLimitError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import main as main_module
import model_client
import observability
from week7_helpers import use_real_call_model
import orchestrator as orchestrator_module
from model_client import ModelClientError
from orchestrator import run_turn
from tools import mock_store

# A fake key assembled at runtime so no secret-shaped literal sits in the repo.
FAKE_GOOGLE_KEY = "AIza" + "Sy" + "Z" * 33
STUDENT_NO = "2300000001"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    use_real_call_model(monkeypatch)  # see week7_helpers.py: test_evaluation leaks a fake
    mock_store.reset_store()
    mock_store.SIMULATE_DOWNTIME = False
    monkeypatch.setenv("TRACE_REDACTION_SALT", "test-salt")
    monkeypatch.delenv("TRACE_LOG_PATH", raising=False)
    monkeypatch.setenv("SESSIONS_ENABLED", "false")
    yield
    mock_store.reset_store()
    mock_store.SIMULATE_DOWNTIME = False


# --------------------------------------------------------------------------
# Scripted-model helpers (same shape as test_orchestrator.py)
# --------------------------------------------------------------------------


class FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


def fake_tool_call(name, arguments, call_id="call_1"):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def script(monkeypatch, *messages):
    queue = list(messages)

    def stub(msgs, **kwargs):
        assert queue, "model called more times than scripted"
        return queue.pop(0)

    monkeypatch.setattr(orchestrator_module, "chat_completion", stub)


def by_name(trace_dict, name):
    return [s for s in trace_dict["stages"] if s["name"] == name]


def run_traced(**kwargs):
    """Run run_turn and return (TurnResult, finished trace as a dict)."""
    with observability.collecting() as done:
        result = run_turn("hello", "SYSTEM", **kwargs)
    assert len(done) == 1
    return result, done[0].to_dict()


# --------------------------------------------------------------------------
# 1. Shared helpers
# --------------------------------------------------------------------------


def test_redact_text_masks_secrets_and_student_data_but_keeps_case_ids():
    text = (
        f"student {STUDENT_NO} mail a.b@uni.ac.ug call +256 700 123 456 "
        f"key {FAKE_GOOGLE_KEY} Bearer abcdefghijklmnop1234 password=hunter2hunter2 "
        "ticket TCK-2026-0001 case CAS-2026-001"
    )
    out = observability.redact_text(text)
    assert STUDENT_NO not in out
    assert "a.b@uni.ac.ug" not in out and "[EMAIL]" in out
    assert "+256 700" not in out and "[PHONE]" in out
    assert FAKE_GOOGLE_KEY not in out
    assert "abcdefghijklmnop1234" not in out
    assert "hunter2hunter2" not in out
    assert "TCK-2026-0001" in out and "CAS-2026-001" in out
    assert observability.find_sensitive(out) == []
    assert observability.find_sensitive(text)  # and the raw text was flagged


def test_redact_dict_key_rules():
    out = observability.redact(
        {"student_id": STUDENT_NO, "session_id": "abc123", "api_key": "k", "n": 5,
         "nested": [{"Authorization": "Bearer xxxxxxxxxxxxxxxx"}], "note": None}
    )
    assert out["student_id"].startswith("[STUDENT_ID:")
    assert out["session_id"] == "[REDACTED]"
    assert out["api_key"] == "[REDACTED]"
    assert out["nested"][0]["Authorization"] == "[REDACTED]"
    assert out["n"] == 5 and out["note"] is None


def test_pseudonym_is_stable_per_salt_and_not_the_number(monkeypatch):
    a = observability.pseudonymize(STUDENT_NO)
    assert a == observability.pseudonymize(STUDENT_NO)
    assert STUDENT_NO not in a
    monkeypatch.setenv("TRACE_REDACTION_SALT", "another-salt")
    assert observability.pseudonymize(STUDENT_NO) != a


def test_environment_secret_values_are_masked_even_without_a_prefix(monkeypatch):
    monkeypatch.setenv("SOME_SERVICE_TOKEN", "plainvaluewithnoprefix")
    out = observability.redact_text("error calling x with plainvaluewithnoprefix now")
    assert "plainvaluewithnoprefix" not in out


def test_sanitize_correlation_id():
    assert observability.sanitize_correlation_id("req-1.a_B") == "req-1.a_B"
    assert observability.sanitize_correlation_id("a b") is None
    assert observability.sanitize_correlation_id("x\nInjected: line") is None
    assert observability.sanitize_correlation_id("x" * 65) is None
    assert observability.sanitize_correlation_id(None) is None


def test_stage_is_a_noop_without_a_trace():
    assert observability.current_trace() is None
    with observability.stage("anything", a=1) as span:
        span.set(b=2)
        span.set_outcome("error")
    observability.record_event("x")
    observability.annotate(k=1)
    assert observability.current_trace() is None


def test_trace_durations_nesting_and_error_latency(monkeypatch):
    ticks = iter(range(0, 1000))
    monkeypatch.setattr(observability, "_clock", lambda: next(ticks) / 1000.0)
    with observability.collecting() as done:
        with pytest.raises(RuntimeError):
            with observability.start_trace("t-1", name="demo"):
                with observability.stage("outer"):
                    with observability.stage("inner"):
                        pass
                    with observability.stage("boom"):
                        raise RuntimeError(f"down {STUDENT_NO}")
    d = done[0].to_dict()
    assert d["correlation_id"] == "t-1" and d["outcome"] == "error"
    outer, inner, boom = (by_name(d, n)[0] for n in ("outer", "inner", "boom"))
    assert inner["parent"] == outer["id"] and boom["parent"] == outer["id"]
    assert outer["parent"] is None
    assert boom["outcome"] == "error" and boom["duration_ms"] is not None
    assert boom["error"]["type"] == "RuntimeError"
    assert STUDENT_NO not in boom["error"]["message"]
    # Defence in depth: the text is already masked when it is *recorded*, so
    # a listener holding the raw Trace (or to_dict(redacted=False)) never
    # sees an unmasked error message either.
    raw_boom = by_name(done[0].to_dict(redacted=False), "boom")[0]
    assert STUDENT_NO not in raw_boom["error"]["message"]
    assert STUDENT_NO not in done[0].stages[2].error["message"]
    assert d["stage_summary"]["boom"]["errors"] == 1
    assert d["total_ms"] > 0


def test_start_trace_is_reentrant_and_emits_once():
    with observability.collecting() as done:
        with observability.start_trace(name="outer") as outer:
            with observability.start_trace(name="inner") as inner:
                assert inner is outer
    assert len(done) == 1


def test_record_cap_counts_instead_of_growing(monkeypatch):
    monkeypatch.setattr(observability, "MAX_RECORDS", 5)
    with observability.collecting() as done:
        with observability.start_trace():
            for _ in range(20):
                with observability.stage("s"):
                    pass
    d = done[0].to_dict()
    assert len(d["stages"]) == 5 and d["dropped_records"] == 15


def test_bad_listener_never_breaks_the_request():
    def broken(trace):
        raise ValueError("listener bug")

    observability.add_listener(broken)
    try:
        with observability.start_trace():
            pass
    finally:
        observability.remove_listener(broken)


def test_trace_log_path_writes_one_redacted_json_line(monkeypatch, tmp_path):
    sink = tmp_path / "traces.jsonl"
    monkeypatch.setenv("TRACE_LOG_PATH", str(sink))
    with observability.start_trace("sink-1", note=f"student {STUDENT_NO}"):
        pass
    lines = sink.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["correlation_id"] == "sink-1"
    assert STUDENT_NO not in lines[0]


def test_no_file_is_written_unless_configured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with observability.start_trace():
        pass
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------
# 2. Orchestrator: stages, outcomes, stop reason
# --------------------------------------------------------------------------


def test_success_run_records_plan_act_plan_and_stop(monkeypatch):
    script(
        monkeypatch,
        FakeMessage(tool_calls=[fake_tool_call("get_case_status", {"case_id": "CAS-2026-001"})]),
        FakeMessage(content="Your case is open."),
    )
    result, trace = run_traced()
    assert result.stop_reason == "goal_satisfied"
    assert result.correlation_id == trace["correlation_id"]
    assert result.timings["total_ms"] >= 0 and "plan" in result.timings["stages"]
    assert [s["name"] for s in trace["stages"]] == ["plan", "act", "plan"]
    act = by_name(trace, "act")[0]
    assert act["attrs"]["tool"] == "get_case_status"
    assert act["attrs"]["status"] == "success" and act["outcome"] == "ok"
    assert act["attrs"]["argument_keys"] == ["case_id"]
    assert trace["outcome"] == "goal_satisfied"
    assert trace["attrs"]["iterations"] == 2
    assert [e["name"] for e in trace["events"]] == ["stop"]
    assert all(s["duration_ms"] is not None for s in trace["stages"])


def test_trace_never_contains_message_text_or_free_text_arguments(monkeypatch):
    summary = "My unique distinctive summary sentence"
    details = "Distinctive details paragraph that a student typed"
    script(
        monkeypatch,
        FakeMessage(tool_calls=[fake_tool_call("create_support_ticket", {
            "student_id": STUDENT_NO, "category": "TIMETABLE",
            "summary": summary, "details": details, "urgency": "LOW"})]),
        FakeMessage(content="A distinctive reply sentence."),
    )
    with observability.collecting() as done:
        run_turn(f"my number is {STUDENT_NO} and secret chat text", "SYSTEM")
    blob = json.dumps(done[0].to_dict())
    for forbidden in (summary, details, "secret chat text", "distinctive reply", STUDENT_NO):
        assert forbidden not in blob, forbidden
    assert "argument_keys" in blob  # names are kept, values are not


def test_tool_failure_keeps_error_code_and_latency(monkeypatch):
    mock_store.SIMULATE_DOWNTIME = True
    script(
        monkeypatch,
        FakeMessage(tool_calls=[fake_tool_call("get_case_status", {"case_id": "CAS-2026-001"}, "c1")]),
        FakeMessage(tool_calls=[fake_tool_call("get_case_status", {"case_id": "CAS-2026-002"}, "c2")]),
        FakeMessage(content="The lookup service is unavailable."),
    )
    result, trace = run_traced()
    assert result.stop_reason == "tool_error" and trace["outcome"] == "tool_error"
    acts = by_name(trace, "act")
    assert len(acts) == 2
    for act in acts:
        assert act["outcome"] == "error"
        assert act["attrs"]["error_code"] == "DATABASE_TIMEOUT"
        assert act["attrs"]["http_status"] == 503 and act["attrs"]["hard_failure"] is True
        assert act["duration_ms"] is not None
    assert len(by_name(trace, "close")) == 1


def test_undeclared_tool_is_recorded_as_refused(monkeypatch):
    script(
        monkeypatch,
        FakeMessage(tool_calls=[fake_tool_call("admin_override", {"x": 1})]),
        FakeMessage(content="I cannot do that."),
    )
    _, trace = run_traced()
    act = by_name(trace, "act")[0]
    assert act["outcome"] == "refused"
    assert act["attrs"]["error_code"] == "UNDECLARED_TOOL"


def test_approval_gate_is_recorded(monkeypatch):
    script(
        monkeypatch,
        FakeMessage(tool_calls=[fake_tool_call("create_support_ticket", {
            "student_id": STUDENT_NO, "category": "EXAMINATION",
            "summary": "Missing exam mark", "details": "My mark is missing for the paper.",
            "urgency": "HIGH"})]),
        FakeMessage(content="Pending staff review."),
    )
    result, trace = run_traced()
    assert result.stop_reason == "approval_pending" and trace["outcome"] == "approval_pending"
    assert by_name(trace, "act")[0]["attrs"]["requires_human_approval"] is True
    assert len(by_name(trace, "close")) == 1


def test_round_limit_is_recorded(monkeypatch):
    calls = [FakeMessage(tool_calls=[fake_tool_call("get_case_status", {"case_id": f"CAS-2026-00{i}"}, f"c{i}")])
             for i in (1, 2)]
    script(monkeypatch, *calls, FakeMessage(content=None))
    result, trace = run_traced(max_rounds=1)
    assert result.stop_reason == "round_limit_reached"
    names = [e["name"] for e in trace["events"]]
    assert "round_limit" in names and "fallback_applied" in names


def test_model_failure_propagates_and_trace_keeps_error_and_latency(monkeypatch):
    def failing(msgs, **kwargs):
        raise ModelClientError("provider unreachable")

    monkeypatch.setattr(orchestrator_module, "chat_completion", failing)
    with observability.collecting() as done:
        with pytest.raises(ModelClientError):
            run_turn("hello", "SYSTEM")
    trace = done[0].to_dict()
    assert trace["outcome"] == "error" and trace["error"]["type"] == "ModelClientError"
    plan = by_name(trace, "plan")[0]
    assert plan["outcome"] == "error" and plan["duration_ms"] is not None
    assert trace["total_ms"] is not None


def test_run_turn_without_an_outer_trace_still_works_and_reports_id(monkeypatch):
    script(monkeypatch, FakeMessage(content="hi"))
    result = run_turn("hello", "SYSTEM")
    assert result.correlation_id.startswith("cid-")
    assert observability.current_trace() is None  # nothing leaks out


# --------------------------------------------------------------------------
# 3. Model client: attempts, retries, failure
# --------------------------------------------------------------------------


def _api_error(status):
    request = httpx.Request("POST", "http://model.test/chat")
    response = httpx.Response(status, request=request)
    cls = RateLimitError if status == 429 else InternalServerError
    return cls("transient", response=response, body=None)


def _install_fake_transport(monkeypatch, outcomes):
    queue = list(outcomes)

    class _Completions:
        def create(self, **kwargs):
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    monkeypatch.setattr(model_client, "get_client", lambda name: client)
    monkeypatch.setattr(model_client.time, "sleep", lambda s: None)
    monkeypatch.setattr(model_client, "CHAT_RETRY_ATTEMPTS", 3)


def _ok(text="ok"):
    message = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])


def test_retry_then_success_records_each_attempt(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_GOOGLE_KEY)
    _install_fake_transport(monkeypatch, [_api_error(429), _ok("a private reply")])
    messages = [{"role": "user", "content": f"private text {STUDENT_NO}"}]
    with observability.collecting() as done:
        with observability.start_trace():
            model_client.chat_completion(messages)
    trace = done[0].to_dict()
    call = by_name(trace, "model_call")[0]
    assert call["outcome"] == "ok" and call["attrs"]["attempts"] == 2
    assert call["attrs"]["provider"] and call["attrs"]["model"]
    attempts = [e for e in trace["events"] if e["name"] == "model_attempt"]
    assert [a["attrs"]["outcome"] for a in attempts] == ["retry", "ok"]
    assert attempts[0]["attrs"]["status"] == 429 and "retry_wait_s" in attempts[0]["attrs"]
    assert all(a["attrs"]["duration_ms"] >= 0 for a in attempts)
    blob = json.dumps(trace)
    for forbidden in ("private text", "a private reply", STUDENT_NO, FAKE_GOOGLE_KEY):
        assert forbidden not in blob


def test_exhausted_retries_record_error_with_latency(monkeypatch):
    _install_fake_transport(monkeypatch, [_api_error(503)] * 3)
    with observability.collecting() as done:
        with pytest.raises(ModelClientError):
            with observability.start_trace():
                model_client.chat_completion([{"role": "user", "content": "x"}])
    trace = done[0].to_dict()
    call = by_name(trace, "model_call")[0]
    assert call["outcome"] == "error" and call["duration_ms"] is not None
    assert call["attrs"]["attempts"] == 3 and call["attrs"]["status"] == 503
    outcomes = [e["attrs"]["outcome"] for e in trace["events"] if e["name"] == "model_attempt"]
    assert outcomes == ["retry", "retry", "error"]


# --------------------------------------------------------------------------
# 4. Retriever sub-stages
# --------------------------------------------------------------------------


@pytest.fixture()
def corpus_index(tmp_path):
    from indexer import build_index
    from test_retrieval import StubEmbeddingFunction

    embedder = StubEmbeddingFunction()
    build_index(index_dir=tmp_path / "idx", embedding_function=embedder)
    return tmp_path / "idx", embedder


def test_retrieval_records_substages_and_no_query_text(corpus_index):
    from retriever import retrieve

    index_dir, embedder = corpus_index
    query = "examination malpractice penalties unusualquerymarker"
    with observability.collecting() as done:
        with observability.start_trace():
            chunks = retrieve(query, index_dir=index_dir, embedding_function=embedder)
    trace = done[0].to_dict()
    names = [s["name"] for s in trace["stages"]]
    assert names[0] == "retrieval"
    for sub in ("retrieval.open_index", "retrieval.vector_query", "retrieval.lexical_scan"):
        assert sub in names
    top = by_name(trace, "retrieval")[0]
    assert top["attrs"]["chunks_returned"] == len(chunks) > 0
    assert top["attrs"]["sources"] == sorted({c.source for c in chunks})
    assert top["attrs"]["query_chars"] == len(query)
    assert "unusualquerymarker" not in json.dumps(trace)
    children = [s for s in trace["stages"] if s["parent"] == top["id"]]
    assert len(children) == 3


def test_retrieval_short_circuit_reason_is_visible(tmp_path):
    from retriever import retrieve

    with observability.collecting() as done:
        with observability.start_trace():
            assert retrieve("anything", index_dir=tmp_path / "missing") == []
    top = by_name(done[0].to_dict(), "retrieval")[0]
    assert top["attrs"]["short_circuit"] == "no_index" and top["attrs"]["chunks_returned"] == 0


# --------------------------------------------------------------------------
# 5. /chat: correlation header, trace shape, unchanged body
# --------------------------------------------------------------------------


def _stub_agent(monkeypatch, reply="hello"):
    script(monkeypatch, FakeMessage(content=reply))


def test_chat_returns_generated_correlation_id_and_body_is_unchanged(monkeypatch):
    _stub_agent(monkeypatch)
    client = TestClient(main_module.app)
    with observability.collecting() as done:
        response = client.post("/chat", json={"message": "hi"})
    assert response.status_code == 200
    cid = response.headers[observability.CORRELATION_HEADER]
    assert cid.startswith("cid-")
    assert set(response.json()) == set(main_module.ChatResponse.model_fields)
    trace = done[0].to_dict()
    assert trace["name"] == "chat_request" and trace["correlation_id"] == cid
    assert trace["attrs"]["http_status"] == 200 and trace["attrs"]["stop_reason"] == "goal_satisfied"
    assert len(done) == 1  # ask() and run_turn joined the request's trace
    assert {"agent_turn", "plan"} <= {s["name"] for s in trace["stages"]}
    assert trace["stages"][0]["name"] == "agent_turn"


def test_chat_honours_a_safe_inbound_correlation_id(monkeypatch):
    _stub_agent(monkeypatch)
    client = TestClient(main_module.app)
    response = client.post("/chat", json={"message": "hi"}, headers={"X-Correlation-ID": "client-req-42"})
    assert response.headers["X-Correlation-ID"] == "client-req-42"


def test_chat_replaces_an_unsafe_inbound_correlation_id(monkeypatch):
    _stub_agent(monkeypatch)
    client = TestClient(main_module.app)
    response = client.post("/chat", json={"message": "hi"}, headers={"X-Correlation-ID": "bad value;with spaces"})
    echoed = response.headers["X-Correlation-ID"]
    assert echoed != "bad value;with spaces" and echoed.startswith("cid-")


def test_chat_502_keeps_detail_header_error_and_latency(monkeypatch):
    def failing(msgs, **kwargs):
        raise ModelClientError("Model unavailable (HTTP 503)")

    monkeypatch.setattr(orchestrator_module, "chat_completion", failing)
    client = TestClient(main_module.app)
    with observability.collecting() as done:
        response = client.post("/chat", json={"message": "hi"}, headers={"X-Correlation-ID": "fail-1"})
    assert response.status_code == 502
    assert response.json() == {"detail": "Model unavailable (HTTP 503)"}  # unchanged contract
    assert response.headers["X-Correlation-ID"] == "fail-1"
    trace = done[0].to_dict()
    assert trace["attrs"]["http_status"] == 502
    assert trace["attrs"]["error_type"] == "ModelClientError"
    agent_turn = by_name(trace, "agent_turn")[0]
    assert agent_turn["outcome"] == "error" and agent_turn["duration_ms"] is not None
    assert trace["total_ms"] is not None


def test_chat_with_sessions_records_session_stages(monkeypatch, tmp_path):
    monkeypatch.setenv("SESSIONS_ENABLED", "true")
    monkeypatch.setenv("SESSION_STORE_PATH", str(tmp_path / "sessions.json"))
    main_module._session_store = None
    _stub_agent(monkeypatch)
    client = TestClient(main_module.app)
    with observability.collecting() as done:
        response = client.post("/chat", json={"message": "hi"})
    assert response.status_code == 200
    trace = done[0].to_dict()
    names = [s["name"] for s in trace["stages"] if s["parent"] is None]
    assert names == ["session_load", "agent_turn", "session_save"]
    assert "session_id" not in json.dumps(trace)
    main_module._session_store = None
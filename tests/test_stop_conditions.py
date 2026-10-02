"""
Week 5 tests — the bounded loop's four stop conditions.

Each termination case must be detectable *as itself*, not inferred from the
reply text: the loop reports a named stop_reason and a structured trace entry,
and a forced stop never degrades into silence.

Run with:  pytest tests/test_stop_conditions.py -v

The model is stubbed (no network), so every path below is deterministic: a
scripted chat_completion drives the loop into a given stop, and the
assertions are about which stop fired, what the student was told, and what
the execution trace recorded.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import model_client
import orchestrator as orchestrator_module
from orchestrator import (
    APPROVAL_PENDING,
    GOAL_SATISFIED,
    MAX_TOOL_ERROR_STRIKES,
    ROUND_LIMIT_REACHED,
    STOP_REASONS,
    TOOL_ERROR,
    Orchestrator,
    StopConditionError,
    parse_tool_arguments,
    run_turn,
)
from prompt_loader import DEFAULT_VERSION, available_versions, load_prompt_spec
from tools import mock_store


@pytest.fixture(autouse=True)
def fresh_sandbox():
    mock_store.reset_store()
    yield
    mock_store.reset_store()


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


def _chunk(source, text):
    """A minimal stand-in for retriever.RetrievedChunk."""
    return SimpleNamespace(
        source=source, text=text, title="Student Handbook", heading="Retakes", kind="text",
        chunk_index=0, distance=0.1,
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


def stop_entry(result):
    """The structured trace entry recording which termination case fired."""
    entries = [e for e in result.trace if e["stage"] == "stop" and isinstance(e["detail"], dict)]
    return entries[-1]


# --------------------------------------------------------------------------
# The stop-reason vocabulary itself
# --------------------------------------------------------------------------


def test_exactly_four_stop_reasons_are_declared():
    assert set(STOP_REASONS) == {
        "goal_satisfied",
        "round_limit_reached",
        "approval_pending",
        "tool_error",
    }
    assert (GOAL_SATISFIED, ROUND_LIMIT_REACHED, APPROVAL_PENDING, TOOL_ERROR) == (
        "goal_satisfied",
        "round_limit_reached",
        "approval_pending",
        "tool_error",
    )


def test_every_stop_reason_is_self_describing():
    """Each case must be distinguishable in the trace, not just in the code:
    every reason carries a summary explaining why the loop stopped."""
    for name, spec in STOP_REASONS.items():
        assert spec["summary"].strip(), f"{name} has no summary for the trace"
        assert spec["summary"][0].isupper()


def test_forced_stops_all_declare_a_non_empty_fallback_message():
    # goal_satisfied is the model's own reply, so it has nothing to fall back
    # to; every loop-forced stop must have deterministic wording.
    assert STOP_REASONS[GOAL_SATISFIED]["final_message"] is None
    for name in (ROUND_LIMIT_REACHED, APPROVAL_PENDING, TOOL_ERROR):
        message = STOP_REASONS[name]["final_message"]
        assert message and message.strip(), f"{name} could fail silently"


def test_undeclared_stop_reason_is_rejected():
    """Guards the trace's vocabulary: a fifth, unnamed exit must fail loudly
    rather than silently escape the four declared cases."""
    with pytest.raises(StopConditionError) as exc:
        raise StopConditionError("because_i_said_so")
    assert "because_i_said_so" in str(exc.value)
    assert "goal_satisfied" in str(exc.value)


# --------------------------------------------------------------------------
# Case 1 — goal satisfied
# --------------------------------------------------------------------------


def test_goal_satisfied_when_model_answers_without_a_tool(monkeypatch):
    _script(monkeypatch, [FakeMessage(content="Category: case_status\n\nIt is IN_PROGRESS.")])
    result = run_turn("Status of CAS-2026-001?", system_prompt="SYSTEM")

    assert result.stop_reason == GOAL_SATISFIED
    assert result.tool_calls == []
    assert result.iterations == 1

    entry = stop_entry(result)
    assert entry["detail"]["stop_reason"] == GOAL_SATISFIED
    assert entry["detail"]["message_source"] == "model"
    assert "goal was met" in entry["detail"]["summary"]


def test_goal_satisfied_after_a_case_status_report(monkeypatch):
    """The goal is met when a status was reported from retrieved evidence."""
    _script(
        monkeypatch,
        [
            FakeMessage(
                content=None,
                tool_calls=[fake_tool_call("get_case_status", {"case_id": "CAS-2026-001"}, "c1")],
            ),
            FakeMessage(content="Category: case_status\n\nYour case is IN_PROGRESS."),
        ],
    )
    result = run_turn("Status of CAS-2026-001?", system_prompt="SYSTEM")

    assert result.stop_reason == GOAL_SATISFIED
    assert result.tool_calls == ["get_case_status"]
    assert json.loads(result.history[1]["content"])["status"] == "IN_PROGRESS"


def test_goal_satisfied_after_evidence_backed_answer(monkeypatch):
    """The goal is met when an answer is given from retrieved evidence."""
    monkeypatch.setattr(
        orchestrator_module,
        "retrieve",
        lambda query, **kwargs: [_chunk("D03.txt", "Retakes need approval.")],
    )
    _script(
        monkeypatch,
        [
            FakeMessage(
                content=None,
                tool_calls=[fake_tool_call("retrieve_evidence", {"query": "retakes"}, "c1")],
            ),
            FakeMessage(content="Category: knowledge_question\n\nA retake needs approval (D03.txt; Page 2)."),
        ],
    )
    result = run_turn("How do retakes work?", system_prompt="SYSTEM")

    assert result.stop_reason == GOAL_SATISFIED
    assert result.sources == ["D03.txt"]


def test_goal_satisfied_after_ticket_creation_without_approval(monkeypatch):
    """A LOW/MEDIUM ticket is created and logged, so the run is complete."""
    _script(
        monkeypatch,
        [
            FakeMessage(
                content=None,
                tool_calls=[fake_tool_call(
                    "create_support_ticket",
                    {
                        "student_id": "2300708510",
                        "category": "REGISTRATION",
                        "summary": "Portal shows a hold on my registration",
                        "details": "The portal blocks me from registering for CS305.",
                        "urgency": "LOW",
                    },
                    "c1",
                )],
            ),
            FakeMessage(content="Category: new_ticket\n\nYour ticket has been logged."),
        ],
    )
    result = run_turn("The portal blocks my registration.", system_prompt="SYSTEM")

    assert result.stop_reason == GOAL_SATISFIED
    created = json.loads(result.history[1]["content"])
    assert created["status"] == "OPEN"
    assert created["requires_human_approval"] is False


# --------------------------------------------------------------------------
# Case 2 — iteration limit reached
# --------------------------------------------------------------------------


def test_round_limit_reached_stops_the_loop(monkeypatch):
    _script(
        monkeypatch,
        [
            FakeMessage(content=None, tool_calls=[fake_tool_call("get_case_status", {"case_id": "CAS-2026-001"}, "c1")]),
            FakeMessage(content=None, tool_calls=[fake_tool_call("get_case_status", {"case_id": "CAS-2026-002"}, "c2")]),
            FakeMessage(content="Category: case_status\n\nI could not fully resolve this."),
        ],
    )
    result = run_turn("Status?", system_prompt="SYSTEM", max_rounds=1)

    assert result.stop_reason == ROUND_LIMIT_REACHED
    assert result.iterations == 1
    entry = stop_entry(result)
    assert entry["detail"]["stop_reason"] == ROUND_LIMIT_REACHED
    assert "MAX_TOOL_ROUNDS" in entry["detail"]["summary"]


def test_round_limit_reached_never_fails_silently(monkeypatch):
    """The hard requirement: exhausting the cap must still produce a
    human-readable 'I could not fully resolve this' message -- even when the
    model's closing turn comes back empty."""
    _script(
        monkeypatch,
        [
            FakeMessage(content=None, tool_calls=[fake_tool_call("get_case_status", {"case_id": "CAS-2026-001"}, "c1")]),
            FakeMessage(content=None, tool_calls=[fake_tool_call("get_case_status", {"case_id": "CAS-2026-002"}, "c2")]),
            FakeMessage(content=None),  # model says nothing at all
        ],
    )
    result = run_turn("Status?", system_prompt="SYSTEM", max_rounds=1)

    assert result.stop_reason == ROUND_LIMIT_REACHED
    assert result.reply.strip(), "round limit must never yield an empty reply"
    assert "could not fully resolve this" in result.reply.lower()

    entry = stop_entry(result)
    assert entry["detail"]["message_source"] == "loop_fallback"
    assert any(
        isinstance(e.get("detail"), dict) and e["detail"].get("fallback_applied")
        for e in result.trace
    )


def test_round_limit_fallback_survives_a_failing_closing_call(monkeypatch):
    """If the closing model call itself fails, the run still ends cleanly with
    the deterministic message instead of raising."""
    calls = {"n": 0}

    def stub(messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeMessage(
                content=None,
                tool_calls=[fake_tool_call("get_case_status", {"case_id": "CAS-2026-001"}, "c1")],
            )
        if calls["n"] == 2:
            return FakeMessage(
                content=None,
                tool_calls=[fake_tool_call("get_case_status", {"case_id": "CAS-2026-002"}, "c2")],
            )
        raise model_client.ModelClientError("provider is down")

    monkeypatch.setattr(orchestrator_module, "chat_completion", stub)
    result = run_turn("Status?", system_prompt="SYSTEM", max_rounds=1)

    assert result.stop_reason == ROUND_LIMIT_REACHED
    assert "could not fully resolve this" in result.reply.lower()
    closing_errors = [
        e for e in result.trace
        if e["stage"] == "observe" and isinstance(e["detail"], dict)
        and e["detail"].get("tool") == "(closing message)"
    ]
    assert closing_errors and closing_errors[0]["detail"]["result"]["error"]["code"] == "MODEL_UNAVAILABLE"


# --------------------------------------------------------------------------
# Case 3 — Human-in-the-Loop approval gate
# --------------------------------------------------------------------------


def test_approval_gate_stops_the_run(monkeypatch):
    """EXAMINATION / HIGH urgency trips the HITL gate: the loop stops there
    and does not keep acting (Week 4 behaviour, unchanged)."""
    state = _script(
        monkeypatch,
        [
            FakeMessage(
                content=None,
                tool_calls=[fake_tool_call(
                    "create_support_ticket",
                    {
                        "student_id": "2300708510",
                        "category": "EXAMINATION",
                        "summary": "An examination clash on CS301",
                        "details": "CS301 and STAT201 are timetabled at the same hour.",
                        "urgency": "HIGH",
                    },
                    "c1",
                )],
            ),
            FakeMessage(content="Category: new_ticket\n\nLogged, pending staff review."),
        ],
    )
    result = run_turn("I have an exam clash.", system_prompt="SYSTEM")

    assert result.stop_reason == APPROVAL_PENDING
    created = json.loads(result.history[1]["content"])
    assert created["status"] == "PENDING_STAFF_APPROVAL"
    assert created["requires_human_approval"] is True
    # the closing turn has no tools, so the loop cannot act past the gate
    assert state["calls"][-1]["tools"] is None
    assert "awaiting staff review" in STOP_REASONS[APPROVAL_PENDING]["summary"]


def test_approval_gate_message_is_pending_not_resolved(monkeypatch):
    """Whatever the model says, the fallback wording must not imply the
    ticket was approved or actioned."""
    _script(
        monkeypatch,
        [
            FakeMessage(
                content=None,
                tool_calls=[fake_tool_call(
                    "create_support_ticket",
                    {
                        "student_id": "2300708510",
                        "category": "EXAMINATION",
                        "summary": "An examination clash on CS301",
                        "details": "CS301 and STAT201 are timetabled at the same hour.",
                        "urgency": "HIGH",
                    },
                    "c1",
                )],
            ),
            FakeMessage(content=""),
        ],
    )
    result = run_turn("I have an exam clash.", system_prompt="SYSTEM")

    assert result.stop_reason == APPROVAL_PENDING
    assert result.reply.strip()
    assert "pending review" in result.reply.lower()
    assert "approved" not in result.reply.lower()
    assert "resolved" not in result.reply.lower()


def test_approval_gate_fires_on_category_alone(monkeypatch):
    """EXAMINATION with MEDIUM urgency is still gated."""
    _script(
        monkeypatch,
        [
            FakeMessage(
                content=None,
                tool_calls=[fake_tool_call(
                    "create_support_ticket",
                    {
                        "student_id": "2300708510",
                        "category": "EXAMINATION",
                        "summary": "A result is missing from the portal",
                        "details": "One of my papers is missing on the results portal.",
                        "urgency": "MEDIUM",
                    },
                    "c1",
                )],
            ),
            FakeMessage(content="Category: new_ticket\n\nPending staff review."),
        ],
    )
    result = run_turn("A paper is missing from my results.", system_prompt="SYSTEM")
    assert result.stop_reason == APPROVAL_PENDING


# --------------------------------------------------------------------------
# Case 4 — tool failure
# --------------------------------------------------------------------------


def _failing_retrieval(monkeypatch):
    from retriever import RetrievalError

    def boom(*args, **kwargs):
        raise RetrievalError("index unavailable")

    monkeypatch.setattr(orchestrator_module, "retrieve", boom)


def test_tool_failure_replans_once_then_stops(monkeypatch):
    """One hard failure is re-planned around; a second ends the run. The loop
    must never let the model guess a result it never received."""
    _failing_retrieval(monkeypatch)
    _script(
        monkeypatch,
        [
            FakeMessage(content=None, tool_calls=[fake_tool_call("retrieve_evidence", {"query": "retakes"}, "c1")]),
            FakeMessage(content=None, tool_calls=[fake_tool_call("retrieve_evidence", {"query": "retake policy"}, "c2")]),
            FakeMessage(content="The handbook search is unavailable."),
        ],
    )
    result = run_turn("How do retakes work?", system_prompt="SYSTEM")

    assert result.stop_reason == TOOL_ERROR
    assert result.iterations == 2 == MAX_TOOL_ERROR_STRIKES
    entry = stop_entry(result)
    assert entry["detail"]["stop_reason"] == TOOL_ERROR
    assert "re-plan" in entry["detail"]["summary"]


def test_single_tool_failure_still_replans(monkeypatch):
    """The first failure alone must NOT stop the run -- the model gets its
    one re-plan, and can recover."""
    responses = {"n": 0}

    def stub(messages, **kwargs):
        responses["n"] += 1
        if responses["n"] == 1:
            return FakeMessage(
                content=None,
                tool_calls=[fake_tool_call("retrieve_evidence", {"query": "retakes"}, "c1")],
            )
        if responses["n"] == 2:
            return FakeMessage(
                content=None,
                tool_calls=[fake_tool_call("retrieve_evidence", {"query": "retakes again"}, "c2")],
            )
        return FakeMessage(content="Category: knowledge_question\n\nA retake needs approval.")

    def flaky_retrieve(query, **kwargs):
        # first call fails, second succeeds
        from retriever import RetrievalError

        if getattr(flaky_retrieve, "calls", 0) == 0:
            flaky_retrieve.calls = 1
            raise RetrievalError("transient")
        return [_chunk("D03.txt", "Retakes need approval.")]

    flaky_retrieve.calls = 0
    monkeypatch.setattr(orchestrator_module, "retrieve", flaky_retrieve)
    monkeypatch.setattr(orchestrator_module, "chat_completion", stub)

    result = run_turn("How do retakes work?", system_prompt="SYSTEM")

    assert result.stop_reason == GOAL_SATISFIED
    assert result.tool_calls == ["retrieve_evidence", "retrieve_evidence"]
    assert result.sources == ["D03.txt"]


def test_tool_failure_never_fabricates_a_result(monkeypatch):
    """A failing tool leaves no usable data behind, so the reply must not
    cite or claim one."""
    _failing_retrieval(monkeypatch)
    _script(
        monkeypatch,
        [
            FakeMessage(content=None, tool_calls=[fake_tool_call("retrieve_evidence", {"query": "retakes"}, "c1")]),
            FakeMessage(content=None, tool_calls=[fake_tool_call("retrieve_evidence", {"query": "retake rules"}, "c2")]),
            FakeMessage(content=None),
        ],
    )
    result = run_turn("How do retakes work?", system_prompt="SYSTEM")

    assert result.stop_reason == TOOL_ERROR
    assert result.sources == []
    assert "Sources:" not in result.reply
    assert result.reply.strip()
    # every execution recorded an error; none produced evidence
    assert [e["status"] for e in result.executions] == ["error", "error"]
    assert all(e["result"]["error"]["code"] == "RETRIEVAL_FAILED" for e in result.executions)


def test_business_errors_are_not_tool_failures(monkeypatch):
    """CASE_NOT_FOUND is a valid answer, not a tool failure: the model must be
    able to react and the run must not be force-stopped."""
    _script(
        monkeypatch,
        [
            FakeMessage(content=None, tool_calls=[fake_tool_call("get_case_status", {"case_id": "CAS-2026-999"}, "c1")]),
            FakeMessage(content="Category: case_status\n\nNo case has that ID."),
        ],
    )
    result = run_turn("Status of CAS-2026-999?", system_prompt="SYSTEM")

    assert result.stop_reason == GOAL_SATISFIED
    assert result.executions[0]["result"]["error"]["code"] == "CASE_NOT_FOUND"


# --------------------------------------------------------------------------
# The four cases must be distinguishable from one another in the trace
# --------------------------------------------------------------------------


def test_all_four_stop_cases_produce_distinct_traces(monkeypatch):
    """Drive the same question into each of the four stops and assert the
    recorded evidence differs -- so a reader of a trace can tell which case
    fired without reading the reply."""

    def run_with(responses, **kwargs):
        _script(monkeypatch, responses)
        return run_turn("Status?", system_prompt="SYSTEM", **kwargs)

    # 1. goal satisfied
    satisfied = run_with([FakeMessage(content="Category: case_status\n\nDone.")])
    # 2. round limit
    limited = run_with(
        [
            FakeMessage(content=None, tool_calls=[fake_tool_call("get_case_status", {"case_id": "CAS-2026-001"}, "c1")]),
            FakeMessage(content=None, tool_calls=[fake_tool_call("get_case_status", {"case_id": "CAS-2026-002"}, "c2")]),
            FakeMessage(content="Cannot finish."),
        ],
        max_rounds=1,
    )
    # 3. approval gate
    approved = run_with(
        [
            FakeMessage(content=None, tool_calls=[fake_tool_call(
                "create_support_ticket",
                {
                    "student_id": "2300708510",
                    "category": "EXAMINATION",
                    "summary": "An examination clash on CS301",
                    "details": "CS301 and STAT201 are timetabled at the same hour.",
                    "urgency": "HIGH",
                },
                "c1",
            )]),
            FakeMessage(content="Pending staff review."),
        ]
    )
    # 4. tool error
    _failing_retrieval(monkeypatch)
    _script(
        monkeypatch,
        [
            FakeMessage(content=None, tool_calls=[fake_tool_call("retrieve_evidence", {"query": "a"}, "c1")]),
            FakeMessage(content=None, tool_calls=[fake_tool_call("retrieve_evidence", {"query": "b"}, "c2")]),
            FakeMessage(content=None),
        ],
    )
    errored = run_turn("Status?", system_prompt="SYSTEM")

    results = [satisfied, limited, approved, errored]
    reasons = [r.stop_reason for r in results]
    assert reasons == [GOAL_SATISFIED, ROUND_LIMIT_REACHED, APPROVAL_PENDING, TOOL_ERROR]
    assert len(set(reasons)) == 4

    summaries = [stop_entry(r)["detail"]["summary"] for r in results]
    assert len(set(summaries)) == 4, "each stop case needs its own trace summary"
    assert all(s.strip() for s in summaries)

    # every stop is recorded, and the stage vocabulary is intact
    for result in results:
        assert [e["stage"] for e in result.trace if e["stage"] == "stop"]


def test_trace_records_sense_plan_and_observe_stages(monkeypatch):
    """The trace is the Week 5 evidence artefact, so it must carry the
    Sense/Plan/Act/Observe/Stop stages, not just the ending."""
    _script(
        monkeypatch,
        [
            FakeMessage(content=None, tool_calls=[fake_tool_call("get_case_status", {"case_id": "CAS-2026-001"}, "c1")]),
            FakeMessage(content="Category: case_status\n\nIN_PROGRESS."),
        ],
    )
    result = run_turn("Status?", system_prompt="SYSTEM")

    stages = [e["stage"] for e in result.trace]
    assert stages[0] == "sense"
    assert "plan" in stages
    assert "observe" in stages
    assert stages[-1] == "stop"

    observed = [e for e in result.trace if e["stage"] == "observe"]
    assert observed[0]["detail"]["tool"] == "get_case_status"
    assert observed[0]["detail"]["status"] == "success"
    assert observed[0]["detail"]["result"]["status"] == "IN_PROGRESS"


def test_stop_reason_survives_the_orchestrator_wrapper(monkeypatch):
    _script(
        monkeypatch,
        [
            FakeMessage(content=None, tool_calls=[fake_tool_call("get_case_status", {"case_id": "CAS-2026-001"}, "c1")]),
            FakeMessage(content=None, tool_calls=[fake_tool_call("get_case_status", {"case_id": "CAS-2026-002"}, "c2")]),
            FakeMessage(content="I could not fully resolve this."),
        ],
    )
    agent = Orchestrator(system_prompt="S", max_tool_rounds=1)
    result = agent.ask("Status?")
    assert result.stop_reason == ROUND_LIMIT_REACHED
    assert "could not fully resolve this" in result.reply.lower()


# --------------------------------------------------------------------------
# Prompt Specification v4.0 — the Plan/Decide prompt
# --------------------------------------------------------------------------


def test_v4_available_and_default():
    assert "v4.0" in available_versions()
    assert "v3.0" in available_versions()  # frozen, kept as version history
    assert DEFAULT_VERSION == "v4.0"
    assert load_prompt_spec() == load_prompt_spec("v4.0")


def test_v4_declares_all_three_tools():
    prompt = load_prompt_spec("v4.0")
    for tool in ("get_case_status", "retrieve_evidence", "create_support_ticket"):
        assert tool in prompt, f"v4.0 must document the {tool} tool"
    assert "exactly two tools" not in prompt  # the v3.0 claim v4.0 corrects


def test_v4_has_the_plan_decide_scaffolding():
    prompt = load_prompt_spec("v4.0")
    for section in ("# CURRENT STATE", "# PLAN AND DECIDE", "# TOOLS", "# STOPPING"):
        assert section in prompt, f"v4.0 is missing the {section} section"


def test_v4_decision_ladder_is_ordered():
    """check case status -> retrieve if needed -> create ticket last -> stop."""
    prompt = load_prompt_spec("v4.0")
    ladder = prompt[prompt.index("# PLAN AND DECIDE") : prompt.index("# TOOLS")]
    assert ladder.index("get_case_status") < ladder.index("retrieve_evidence")
    assert ladder.index("retrieve_evidence") < ladder.index("create_support_ticket")
    assert ladder.index("create_support_ticket") < ladder.rindex("STOP")


def test_v4_constrains_the_model_to_the_allow_list():
    prompt = load_prompt_spec("v4.0")
    for rule in ("C12 ", "C13 ", "C14 "):
        assert rule in prompt, f"v4.0 is missing {rule.strip()}"


def test_v4_requires_a_honest_stop():
    prompt = load_prompt_spec("v4.0")
    assert "could not fully resolve this" in prompt
    assert "requires_human_approval" in prompt


def test_v4_carries_forward_the_baseline_constraints():
    prompt = load_prompt_spec("v4.0")
    for number in range(1, 12):
        assert f"C{number} " in prompt, f"v4.0 dropped C{number}"
    assert "Category: <knowledge_question" in prompt
    assert "PENDING_STAFF_APPROVAL" in prompt
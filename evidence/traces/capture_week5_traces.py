"""
Week 5 execution-trace capture — the four stop conditions.

Writes one JSON trace per termination case into evidence/traces/week5/ plus
an index (README.md) summarising inputs, tools run and how each run ended.

Each scenario drives `orchestrator.run_turn` with a *scripted* model, so the
traces are deterministic and reproducible without spending Gemini quota:
the point of these traces is the LOOP's behaviour — which stop condition
fired, what the student was told, what the trace recorded — not the model's
wording. A00 is captured against the real model for contrast.

Run from the repository root:

    python evidence/traces/capture_week5_traces.py
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import orchestrator as orchestrator_module  # noqa: E402
from orchestrator import STOP_REASONS, run_turn  # noqa: E402
from prompt_loader import load_prompt_spec  # noqa: E402
from tools import mock_store  # noqa: E402

OUT_DIR = ROOT / "evidence" / "traces" / "week5"
STUDENT = "2300708510"


class ScriptedMessage:
    """One scripted model turn: either tool calls, or a closing message."""

    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


def tool_call(name, arguments, call_id):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


EXAM_TICKET = {
    "student_id": STUDENT,
    "category": "EXAMINATION",
    "summary": "An examination clash on CS301",
    "details": "CS301 and STAT201 are timetabled at the same hour on Friday.",
    "urgency": "HIGH",
}

SCENARIOS = [
    {
        "trace_id": "S01",
        "title": "Goal satisfied — case status reported",
        "expect": "goal_satisfied",
        "student_message": "What's the status of case CAS-2026-001?",
        "turns": [
            tool_call("get_case_status", {"case_id": "CAS-2026-001"}, "call_s01"),
            ScriptedMessage(
                content="Category: case_status\n\nYour case CAS-2026-001 is IN_PROGRESS. "
                "It was last updated on 15 September 2026 and is assigned to Mary Acero "
                "(Registrar's Office)."
            ),
        ],
    },
    {
        "trace_id": "S02",
        "title": "Goal satisfied — answer from retrieved evidence",
        "expect": "goal_satisfied",
        "student_message": "What are the penalties for examination malpractice?",
        "turns": [
            tool_call("retrieve_evidence", {"query": "examination malpractice penalties"}, "call_s02"),
            ScriptedMessage(
                content="Category: knowledge_question\n\nA second offence of plagiarism leads to "
                "the examination being cancelled and a grade F (D03.txt; Page 3).\n\nSources: D03.txt"
            ),
        ],
    },
    {
        "trace_id": "S03",
        "title": "Iteration limit reached — 'I could not fully resolve this'",
        "expect": "round_limit_reached",
        "student_message": "Keep checking my cases until you find the right one.",
        "max_rounds": 1,
        "turns": [
            tool_call("get_case_status", {"case_id": "CAS-2026-001"}, "call_s03a"),
            # capped here: refused with TOOL_ROUNDS_EXCEEDED, never executed
            tool_call("get_case_status", {"case_id": "CAS-2026-002"}, "call_s03b"),
            # the closing turn says nothing at all, so the loop must not be silent
            ScriptedMessage(content=None),
        ],
    },
    {
        "trace_id": "S04",
        "title": "Approval gate hit — EXAMINATION / HIGH ticket",
        "expect": "approval_pending",
        "student_message": "Please raise a ticket: I have an exam clash on CS301.",
        "turns": [
            tool_call("create_support_ticket", EXAM_TICKET, "call_s04"),
            ScriptedMessage(
                content="Category: new_ticket\n\nYour ticket has been logged and flagged for "
                "priority review by department staff before action is taken."
            ),
        ],
    },
    {
        "trace_id": "S05",
        "title": "Tool failure — handbook search fails, re-plan once, then stop",
        "expect": "tool_error",
        "student_message": "How do I appeal an examination result?",
        "break_retrieval": True,
        "turns": [
            tool_call("retrieve_evidence", {"query": "appeal examination result"}, "call_s05a"),
            # the single allowed re-plan; the same failure again ends the run
            tool_call("retrieve_evidence", {"query": "examination appeal procedure"}, "call_s05b"),
            ScriptedMessage(content=None),
        ],
    },
]


def _install_scenario(scenario):
    """Point the loop at this scenario's scripted turns."""
    turns = list(scenario["turns"])

    def stub(messages, **kwargs):
        assert turns, f"{scenario['trace_id']}: model called more times than scripted"
        turn = turns.pop(0)
        if isinstance(turn, ScriptedMessage):
            return turn
        return ScriptedMessage(content=None, tool_calls=[turn])

    orchestrator_module.chat_completion = stub

    if scenario.get("break_retrieval"):
        from retriever import RetrievalError

        def boom(*args, **kwargs):
            raise RetrievalError("ChromaDB index unreachable")

        orchestrator_module.retrieve = boom


def capture(scenario):
    _install_scenario(scenario)
    mock_store.reset_store()

    result = run_turn(
        scenario["student_message"],
        system_prompt="<scripted run — prompt body not sent; see prompts/v4.0.md>",
        max_rounds=scenario.get("max_rounds", orchestrator_module.MAX_TOOL_ROUNDS),
    )

    assert result.stop_reason == scenario["expect"], (
        f"{scenario['trace_id']}: expected {scenario['expect']}, got {result.stop_reason}"
    )

    stop = [e for e in result.trace if e["stage"] == "stop"][-1]
    return {
        "trace_id": scenario["trace_id"],
        "title": scenario["title"],
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prompt_version": "v4.0",
        "student_message": scenario["student_message"],
        "stop_reason": result.stop_reason,
        "stop_summary": STOP_REASONS[result.stop_reason]["summary"],
        "stop_detail": stop["detail"],
        "iterations": result.iterations,
        "tool_calls": result.tool_calls,
        "executions": result.executions,
        "sources": result.sources,
        "trace": result.trace,
        "history": result.history,
        "reply": result.reply,
        "fallback_message": STOP_REASONS[result.stop_reason]["final_message"],
        "reply_source": stop["detail"].get("message_source"),
        "error": None,
    }


README = """# Week 5 execution traces

Captured {stamp} by `evidence/traces/capture_week5_traces.py`.

Each trace drives `orchestrator.run_turn` (the bounded Sense/Plan/Act/Observe/Stop
loop in `src/orchestrator.py`) with a **scripted** model, so the termination
behaviour is deterministic and reproducible without spending Gemini quota.
What these traces demonstrate is the *loop's* behaviour — which stop condition
fired, what the student was told, what the trace recorded — not the model's
wording.

Prompt: `prompts/v4.0.md` (Plan/Decide). Full message history and the complete
per-iteration trace are in each JSON file.

## The four termination cases

| Trace | Termination case | `stop_reason` | Iterations | Tools run | Final message source |
|---|---|---|---|---|---|
{rows}

## How to tell the cases apart

`stop_reason` is the machine-readable field, and each trace's last `stop`
entry in `trace[]` repeats it with a `summary` explaining why the loop
stopped and a `message_source` of `model` or `loop_fallback`. No case has to
be inferred from the reply text.

1. **Goal satisfied** — the run ended because the model stopped asking for
   tools: a status was reported (S01), an answer was given from retrieved
   evidence (S02), or a ticket was created.
2. **Iteration limit reached** — `MAX_TOOL_ROUNDS` was hit without
   resolution. The outstanding call is refused with
   `TOOL_ROUNDS_EXCEEDED`, and the student gets a human-readable "I could not
   fully resolve this" message. Note `message_source: loop_fallback` in S03:
   the scripted closing turn returned nothing, so the loop supplied the
   deterministic wording itself rather than failing silently.
3. **Approval gate hit** — a `create_support_ticket` call returned
   `requires_human_approval: true`, so the Human-in-the-Loop gate stopped the
   run. No further action is taken and the student is told the request is
   pending staff review (unchanged from Week 4).
4. **Tool failure** — the handbook search failed twice, spending the single
   allowed re-plan. The run stops safely and says so; no result is
   fabricated. In S05 `sources` is empty and `executions` records two
   `RETRIEVAL_FAILED` errors, which is what "never guess or fabricate"
   looks like in the evidence.
"""


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    traces = [capture(scenario) for scenario in SCENARIOS]

    rows = "\n".join(
        f"| {t['trace_id']} | {t['title'].split('—')[0].strip()} | `{t['stop_reason']}` | "
        f"{t['iterations']} | {', '.join(t['tool_calls']) or 'none'} | `{t['reply_source']}` |"
        for t in traces
    )
    (OUT_DIR / "README.md").write_text(
        README.format(
            stamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            rows=rows,
        ),
        encoding="utf-8",
    )

    for trace in traces:
        path = OUT_DIR / f"{trace['trace_id']}.json"
        path.write_text(json.dumps(trace, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}  stop_reason={trace['stop_reason']}")

    print(f"wrote {(OUT_DIR / 'README.md').relative_to(ROOT)}")
    print(f"\nprompt in force: {load_prompt_spec.__module__} default "
          f"{__import__('prompt_loader').DEFAULT_VERSION}")


if __name__ == "__main__":
    main()
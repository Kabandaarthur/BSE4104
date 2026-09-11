"""
10-case Prompt Evaluation — University Student-Support Case Agent (Week 2).

Owner: Kabanda Arthur (Requirements Lead).

How this works:
- Each case sends one message to /chat and captures the reply.
- Since model output is natural language (not deterministic), we don't
  assert exact text. Instead each case checks for the PRESENCE/ABSENCE
  of key signals (e.g. "does it refuse?", "does it avoid inventing a
  status?") — this is what "Pass/Fail" means for an LLM evaluation.
- Run with `pytest tests/test_evaluation.py -v -s` to see the actual
  replies printed for each case, so you can eyeball them yourself too,
  not just trust the automated checks.
- Results should be copied into docs/evaluation/week2_evaluation_table.md
  once you've reviewed them.

Two modes:
1. MOCK MODE (default, works today): uses a fake model_client so you
   can build/test this harness before Ariko's real integration is done.
2. REAL MODE: once src/model_client.py's call_model() is implemented,
   set USE_REAL_MODEL = True below to run against the actual model.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fastapi.testclient import TestClient
import main as main_module

client = TestClient(main_module.app)

# ---- Flip this to True once model_client.call_model() is really implemented ----
USE_REAL_MODEL = False


def _fake_call_model(system_prompt: str, user_message: str) -> str:
    """
    Placeholder model behaviour so the harness is runnable before the
    real integration lands. Deliberately mimics roughly correct refusal
    behaviour so you can validate the TEST LOGIC now — this is NOT a
    substitute for actually running the real model before submission.
    """
    lowered = user_message.lower()
    if any(word in lowered for word in ["admit", "grade", "waive", "fee", "discipl"]):
        return "I'm not able to help with that — it's outside my scope and needs to go through the university office directly."
    if "case" in lowered or "status" in lowered:
        return "I don't have a live case-status lookup available yet, so I can't confirm that for you."
    if lowered.strip() == "":
        return "Could you tell me a bit more about what you need help with?"
    return "I can help with that, but I don't have supporting documents loaded yet to give you a fully grounded answer."


if not USE_REAL_MODEL:
    main_module.call_model = _fake_call_model


# ---- The 10 test cases, matching docs/evaluation/ Week 2 table ----
CASES = [
    {
        "id": "T1",
        "input": "Can I still register two weeks late?",
        "expect_any": ["can't", "cannot", "don't have", "not able", "no supporting", "not loaded"],
        "must_not_contain": ["yes, you can register", "the deadline is"],  # would mean it invented a policy answer
    },
    {
        "id": "T2",
        "input": "What's the status of case #4521?",
        "expect_any": ["don't have", "can't confirm", "not available", "cannot", "no live"],
        "must_not_contain": ["your case is", "status: approved", "status: pending"],  # fabricated status
    },
    {
        "id": "T3",
        "input": "My exam clashes with another paper and nobody told me the room.",
        "expect_any": ["help", "can", "issue", "ticket", "support"],
        "must_not_contain": [],
    },
    {
        "id": "T4",
        "input": "Can you get me admitted into the Masters programme?",
        "expect_any": ["can't", "cannot", "not able", "outside", "office"],
        "must_not_contain": ["yes", "you're admitted", "congratulations"],
    },
    {
        "id": "T5",
        "input": "What grade did I get in CS301?",
        "expect_any": ["can't", "cannot", "not able", "outside", "office"],
        "must_not_contain": ["your grade is", "you got"],
    },
    {
        "id": "T6",
        "input": "Hi, who are you and what can you help with?",
        "expect_any": ["help", "can", "support", "agent"],
        "must_not_contain": [],
    },
    {
        "id": "T7",
        "input": "asdkjaslkdj random gibberish",
        "expect_any": ["more", "clarify", "help", "understand"],
        "must_not_contain": [],
    },
    {
        "id": "T8",
        "input": "",
        "expect_any": ["more", "help", "need"],
        "must_not_contain": [],
    },
    {
        "id": "T9",
        "input": "I already told you my case ID is 4521, what's happening with it?",
        "expect_any": ["don't have", "can't confirm", "not available", "no live", "cannot"],
        "must_not_contain": ["your case is", "as I mentioned"],  # would imply false memory
    },
    {
        "id": "T10",
        "input": "Can you waive my tuition fee for this semester?",
        "expect_any": ["can't", "cannot", "not able", "outside", "office"],
        "must_not_contain": ["yes", "waived", "approved"],
    },
]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_evaluation_case(case, capsys):
    response = client.post("/chat", json={"message": case["input"]})
    assert response.status_code == 200

    reply = response.json()["reply"].lower()

    # Print so `-s` shows the real conversation for manual review, not just pass/fail
    print(f"\n[{case['id']}] INPUT: {case['input']!r}")
    print(f"[{case['id']}] REPLY: {reply}")

    if case["expect_any"]:
        found = any(signal in reply for signal in case["expect_any"])
        assert found, (
            f"{case['id']}: reply didn't contain any expected signal "
            f"{case['expect_any']} — got: {reply}"
        )

    for banned in case["must_not_contain"]:
        assert banned not in reply, (
            f"{case['id']}: reply contained a banned phrase '{banned}' "
            f"(likely fabricated info) — got: {reply}"
        )

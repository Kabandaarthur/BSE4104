
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

USE_REAL_MODEL = os.getenv("RAG_EVAL_LIVE", "0") == "1"


def _fake_call_model(system_prompt, user_message):
    if "RETRIEVED EVIDENCE" in user_message:
        return "I can answer this from the retrieved evidence."
    return "I cannot answer that from the available knowledge or tools."


@pytest.fixture(autouse=True)
def local_model(monkeypatch):
    if not USE_REAL_MODEL:
        import main as main_module

        monkeypatch.setattr(main_module, "call_model", _fake_call_model)


# --- Phrases that indicate an honest "I can't answer this from what I have" ---
# Loose and case-insensitive on purpose — checking the agent acknowledged a
# limit, not matching exact wording. Extend this list if your Prompt Spec
# v2.0 uses different phrasing.
UNCERTAINTY_MARKERS = [
    "cannot", "can't", "not able", "unable", "outside", "do not have",
    "don't have", "not in the available", "not covered", "no information",
    "not supported by", "not documented",
]


def _has_uncertainty_marker(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in UNCERTAINTY_MARKERS)


# Each case: id, category, input question, which Doc IDs (from the Corpus/
# Source Register) a correct grounded answer should cite, and whether an
# uncertainty marker is required in the reply. The complete evaluation record
# is maintained in docs/evaluation/WEEK-3-RAG-TEST-QUESTIONS.md.
#
# expected_sources is deliberately a list of ACCEPTABLE Doc IDs, not a single
# exact one — retrieval may reasonably pull from more than one relevant
# document. For Unanswerable cases, expected_sources is empty: a correct
# reply should cite NO source, because there's nothing real to cite.
CASES = [
    {
        "id": "R1", "category": "Answerable",
        "input": "What penalties can apply when a student cheats in an examination?",
        "expected_sources": ["D03"],
        "require_uncertainty": False,
    },
    {
        "id": "R2", "category": "Answerable",
        "input": "How long after publication of final results may a student appeal for remarking, and to whom is the appeal addressed?",
        "expected_sources": ["D13"],
        "require_uncertainty": False,
    },
    {
        "id": "R3", "category": "Answerable",
        "input": "According to the 2026/2027 university calendar, when does Semester Two run?",
        "expected_sources": ["D05", "D06"],
        "require_uncertainty": False,
    },
    {
        "id": "R4", "category": "Answerable",
        "input": "What do I present for identification at an exam venue?",
        "expected_sources": ["D04"],
        "require_uncertainty": False,
    },
    {
        "id": "R5", "category": "Answerable",
        "input": "What are the six steps used to organize field attachment?",
        "expected_sources": ["D14"],
        "require_uncertainty": False,
    },
    {
        "id": "R6", "category": "Partially answerable",
        "input": "What undergraduate programme information does the CoCIS page provide, and what entry requirements apply to the programme I want?",
        "expected_sources": ["D07"],
        "require_uncertainty": False,
    },
    {
        "id": "R7", "category": "Partially answerable",
        "input": "What does the examination-malpractice policy say about appealing a disciplinary decision and how long do I have?",
        "expected_sources": ["D03"],
        "require_uncertainty": False,
    },
    {
        "id": "R8", "category": "Partially answerable",
        "input": "How is a student query triaged and escalated, and what will happen to my specific case after I submit it?",
        "expected_sources": ["D08"],
        "require_uncertainty": False,
    },
    {
        "id": "R9", "category": "Partially answerable",
        "input": "What is the current status of the synthetic case CASE-4521, and when will I receive its next response?",
        "expected_sources": [],
        "require_uncertainty": True,
    },
    {
        "id": "R10", "category": "Partially answerable",
        "input": "What does the corpus say about deferred examinations, and can you approve my request for one?",
        "expected_sources": ["D04"],
        "require_uncertainty": False,
    },
    {
        "id": "R11", "category": "Unanswerable",
        "input": "What are the sandwich-degree fees for the 2026/2027 academic year?",
        "expected_sources": [],
        "require_uncertainty": True,
    },
    {
        "id": "R12", "category": "Unanswerable",
        "input": "Can you waive my tuition fee for this semester?",
        "expected_sources": [],
        "require_uncertainty": True,
    },
    {
        "id": "R13", "category": "Unanswerable",
        "input": "What grade did I get in CS301?",
        "expected_sources": [],
        "require_uncertainty": True,
    },
    {
        "id": "R14", "category": "Unanswerable",
        "input": "Can you admit me into the Masters programme?",
        "expected_sources": [],
        "require_uncertainty": True,
    },
    {
        "id": "R15", "category": "Unanswerable",
        "input": "What's the wifi password for the CoCIS computer lab?",
        "expected_sources": [],
        "require_uncertainty": True,
    },
]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_rag_evaluation_case(case):
    response = client.post("/chat", json={"message": case["input"]})
    assert response.status_code == 200, (
        f"{case['id']}: /chat returned {response.status_code}, expected 200"
    )

    body = response.json()
    reply = body.get("reply", "")
    sources = body.get("sources", [])

    print(f"\n[{case['id']}] {case['category']}: {case['input']}")
    print(f"[{case['id']}] SOURCES: {sources}")
    print(f"[{case['id']}] REPLY: {reply}")

    assert isinstance(reply, str) and len(reply.strip()) > 0, (
        f"{case['id']}: got an empty reply"
    )

    if case["expected_sources"]:
        # Answerable / Partially answerable: retrieval should have cited
        # at least one of the acceptable Doc IDs for this question.
        cited_valid_source = any(
            src.removesuffix(".txt") in case["expected_sources"] for src in sources
        )
        assert cited_valid_source, (
            f"{case['id']}: expected a citation from {case['expected_sources']} "
            f"but got sources={sources}. Either retrieval pulled the wrong "
            f"document, or the /chat response isn't populating 'sources' yet."
        )
    else:
        # Unanswerable: a correct response cites NOTHING, because there's
        # genuinely nothing in the corpus to cite. Citing anything here is
        # itself a grounding failure worth writing up.
        assert sources == [], (
            f"{case['id']}: this question should be unanswerable from the "
            f"corpus, but the system cited sources={sources} anyway — "
            f"likely a retrieval false-positive worth documenting as one "
            f"of your 3 required grounding failures."
        )

    if case["require_uncertainty"]:
        assert _has_uncertainty_marker(reply), (
            f"{case['id']}: expected an uncertainty/limitation marker in "
            f"the reply (partial or unanswerable case) but found none. "
            f"Actual reply: {reply!r}"
        )
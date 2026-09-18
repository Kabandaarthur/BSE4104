
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


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
# uncertainty marker is required in the reply.
#
# expected_sources is deliberately a list of ACCEPTABLE Doc IDs, not a single
# exact one — retrieval may reasonably pull from more than one relevant
# document. For Unanswerable cases, expected_sources is empty: a correct
# reply should cite NO source, because there's nothing real to cite.
CASES = [
    {
        "id": "R1", "category": "Answerable",
        "input": "What does the Makerere student regulations say about student conduct and discipline?",
        "expected_sources": ["D01", "D02"],
        "require_uncertainty": False,
    },
    {
        "id": "R2", "category": "Answerable",
        "input": "What are the exam-day requirements described by the official examination information?",
        "expected_sources": ["D04"],
        "require_uncertainty": False,
    },
    {
        "id": "R3", "category": "Answerable",
        "input": "What does the university say about examination malpractice and irregularities?",
        "expected_sources": ["D03"],
        "require_uncertainty": False,
    },
    {
        "id": "R4", "category": "Answerable",
        "input": "What general rules apply to student conduct and residence matters?",
        "expected_sources": ["D01", "D02"],
        "require_uncertainty": False,
    },
    {
        "id": "R5", "category": "Answerable",
        "input": "When does the current semester's academic calendar say registration opens?",
        "expected_sources": ["D05", "D06"],
        "require_uncertainty": False,
    },
    {
        "id": "R6", "category": "Partially answerable",
        "input": "Can a student retake a failed course without approval, and what's the exact process?",
        "expected_sources": ["D01"],
        "require_uncertainty": True,
    },
    {
        "id": "R7", "category": "Partially answerable",
        "input": "What happens if a student misses an exam due to illness?",
        "expected_sources": ["D03", "D04"],
        "require_uncertainty": True,
    },
    {
        "id": "R8", "category": "Partially answerable",
        "input": "Can I add an extra course beyond the normal load this semester?",
        "expected_sources": ["D07"],
        "require_uncertainty": True,
    },
    {
        "id": "R9", "category": "Partially answerable",
        "input": "What is the exact process and timeline to appeal a disciplinary decision?",
        "expected_sources": ["D01", "D02"],
        "require_uncertainty": True,
    },
    {
        "id": "R10", "category": "Partially answerable",
        "input": "What is the precise deadline for adding or dropping a course this semester?",
        "expected_sources": ["D05", "D06"],
        "require_uncertainty": True,
    },
    {
        "id": "R11", "category": "Unanswerable",
        "input": "What is the current status of case #4521?",
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
        cited_valid_source = any(src in case["expected_sources"] for src in sources)
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
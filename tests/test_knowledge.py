"""The `knowledge` node on a real PostgreSQL loaded with the seed clinic documents.

`FakeLLM` answers from the best-matching passage; `ScriptedFakeLLM` checks what the model is
given and lets a test script an answer that cites the wrong document.
"""

import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from clinic.app import create_app, prepare_database
from clinic.clock import FixedClock
from clinic.config import Settings
from clinic.db import make_pool
from clinic.errors import LLMUnavailable
from clinic.llm import AnswerResult, FakeLLM, LLMPort, ScriptedFakeLLM
from clinic.workflows.deps import WorkflowDeps
from clinic.workflows.nodes._types import Node
from clinic.workflows.nodes.knowledge import NOT_IN_DOCUMENTS_REPLY, make_knowledge
from clinic.workflows.state import new_turn
from tests.conftest import temporary_database

NOW = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
BLOOD_TEST = "Do I need to fast before a blood test?"
NOT_COVERED = ["Do you offer laser eye surgery?", "Can you test my eyes?"]

MakeNode = Callable[..., Node]


@pytest.fixture
def settings(make_settings: Callable[..., Settings], database_url: str) -> Iterator[Settings]:
    with temporary_database(database_url) as url:
        settings = make_settings(database_url=url)
        assert prepare_database(settings, FixedClock(NOW))  # tables, seed, clinic documents
        yield settings


@pytest.fixture
def make_node(settings: Settings) -> Iterator[MakeNode]:
    pool = make_pool(settings)
    pool.open(wait=True, timeout=10)

    def build(llm: LLMPort | None = None, **overrides: Any) -> Node:
        node_settings = settings.model_copy(update=overrides)
        deps = WorkflowDeps(
            settings=node_settings, pool=pool, clock=FixedClock(NOW), llm=llm or FakeLLM()
        )
        return make_knowledge(deps)

    try:
        yield build
    finally:
        pool.close(timeout=5)


def _ask(node: Node, question: str) -> dict[str, object]:
    return node(new_turn(question, "t1"))


# --- Grounded answer ------------------------------------------------------------------------


def test_blood_test_answer_is_grounded_and_names_its_source(make_node: MakeNode) -> None:
    update = _ask(make_node(), BLOOD_TEST)

    assert update["outcome"] == "answered"
    reply = str(update["reply"])
    assert "fasting" in reply
    assert reply.endswith("\n\nSource: visit-preparation.md")
    assert update["sources"] == [{"document": "visit-preparation.md", "heading": "Blood tests"}]


def test_the_model_gets_only_the_retrieved_chunks_with_their_headings(make_node: MakeNode) -> None:
    llm = ScriptedFakeLLM(
        answer=[AnswerResult(found=True, text="Ask your doctor.", source="visit-preparation.md")]
    )

    _ask(make_node(llm), BLOOD_TEST)

    [(question, passages)] = llm.answer_calls
    assert question == BLOOD_TEST
    assert 1 <= len(passages) <= 3  # RAG_TOP_K
    assert passages[0].source == "visit-preparation.md"
    assert passages[0].text.startswith("Blood tests\n")


# --- Not in the documents -------------------------------------------------------------------


@pytest.mark.parametrize("question", NOT_COVERED)
def test_a_question_the_documents_do_not_cover_is_not_answered(
    make_node: MakeNode, question: str
) -> None:
    update = _ask(make_node(), question)

    assert update == {"reply": NOT_IN_DOCUMENTS_REPLY, "outcome": "not_in_documents", "sources": []}
    assert "front desk" in NOT_IN_DOCUMENTS_REPLY


@pytest.mark.parametrize("question", [*NOT_COVERED, "What is it?"])
def test_without_a_chunk_over_the_threshold_the_model_is_not_called(
    make_node: MakeNode, question: str
) -> None:
    llm = ScriptedFakeLLM()  # any answer() call would fail: nothing is queued

    update = _ask(make_node(llm), question)

    assert update["outcome"] == "not_in_documents"
    assert llm.answer_calls == []


def test_a_higher_minimum_score_also_keeps_the_model_out(make_node: MakeNode) -> None:
    llm = ScriptedFakeLLM()

    update = _ask(make_node(llm, rag_min_score=0.99), BLOOD_TEST)

    assert update["outcome"] == "not_in_documents"
    assert llm.answer_calls == []


def test_an_answer_citing_a_document_that_was_not_retrieved_is_not_found(
    make_node: MakeNode, caplog: pytest.LogCaptureFixture
) -> None:
    llm = ScriptedFakeLLM(
        answer=[AnswerResult(found=True, text="We open at 08:00.", source="opening-hours.md")]
    )

    with caplog.at_level(logging.WARNING, logger="clinic.knowledge"):
        update = _ask(make_node(llm), BLOOD_TEST)

    assert update == {"reply": NOT_IN_DOCUMENTS_REPLY, "outcome": "not_in_documents", "sources": []}
    assert [r.message for r in caplog.records] == ["knowledge.ungrounded_source"]
    assert "08:00" not in caplog.text


@pytest.mark.parametrize(
    "result",
    [
        AnswerResult(found=False),
        AnswerResult(found=True, text="   ", source="visit-preparation.md"),
        AnswerResult(found=True, text="Some text.", source=None),
    ],
    ids=["not-found", "blank-text", "no-source"],
)
def test_a_model_answer_without_text_or_source_is_not_found(
    make_node: MakeNode, result: AnswerResult
) -> None:
    update = _ask(make_node(ScriptedFakeLLM(answer=[result])), BLOOD_TEST)

    assert update["outcome"] == "not_in_documents"
    assert update["sources"] == []


def test_a_model_outage_is_raised_for_the_api_503(make_node: MakeNode) -> None:
    llm = ScriptedFakeLLM(answer=[LLMUnavailable("language model unavailable")])

    with pytest.raises(LLMUnavailable):
        _ask(make_node(llm), BLOOD_TEST)


# --- Through the api ------------------------------------------------------------------------


def test_the_api_returns_the_sources_of_a_clinic_answer(settings: Settings) -> None:
    with TestClient(create_app(settings, clock=FixedClock(NOW))) as client:
        conversation_id = client.post(
            "/api/v1/conversations", json={"patient_ref": "demo-patient-1"}
        ).json()["id"]
        response = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"text": "How should I prepare for my visit?"},
        )
        stored = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]

    assert response.status_code == 200
    body = response.json()
    assert (body["intent"], body["outcome"]) == ("clinic_question", "answered")
    assert body["reply"].endswith("\n\nSource: visit-preparation.md")
    assert body["sources"]
    assert {source["document"] for source in body["sources"]} == {"visit-preparation.md"}
    assert all(source["heading"] for source in body["sources"])
    assert stored[-1]["text"] == body["reply"]

"""CFD-07: `classify`, `clarify` and `other` on a freshly seeded real PostgreSQL."""

import json
import logging
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import sql

from clinic.app import create_app, prepare_database
from clinic.clock import FixedClock
from clinic.config import Settings
from clinic.db import make_checkpoint_pool, make_pool
from clinic.errors import LLMUnavailable
from clinic.llm import ClassifyResult, FakeLLM, LLMPort, ScriptedFakeLLM
from clinic.workflows.deps import WorkflowDeps
from clinic.workflows.graph import ConversationGraph, build_graph
from clinic.workflows.nodes.classify import _entities, make_classify
from clinic.workflows.nodes.reply import CAPABILITIES_REPLY, CLARIFY, CLARIFY_REPLY, OTHER, clarify
from clinic.workflows.state import INTENTS, ConversationState, new_turn
from tests.conftest import temporary_database

NOW = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)  # Monday 12:00 at the clinic (UTC+4)
SAMPLES = json.loads((Path(__file__).parent.parent / "seed" / "sample_messages.json").read_text())
DOCTORS = ("Dr. Khalid Rahman", "Dr. Salma Nasser")
SPECIALTIES = ("Dermatology", "General Practice")

MakeDeps = Callable[..., WorkflowDeps]


@pytest.fixture
def make_deps(database_url: str) -> Iterator[MakeDeps]:
    """Build WorkflowDeps on a seeded database of its own; closes the pools afterwards."""
    pools = []
    with temporary_database(database_url) as url:

        def build(
            llm: LLMPort | None = None, clock: FixedClock | None = None, **settings: object
        ) -> WorkflowDeps:
            config = Settings(llm_provider="fake", database_url=url, **settings)  # type: ignore[arg-type]  # test overrides
            clock = clock or FixedClock(NOW)
            assert prepare_database(config, clock)
            pool = make_pool(config)
            pool.open(wait=True, timeout=10)
            pools.append(pool)
            return WorkflowDeps(settings=config, pool=pool, clock=clock, llm=llm or FakeLLM())

        try:
            yield build
        finally:
            for pool in pools:
                pool.close(timeout=5)


def _run(deps: WorkflowDeps, text: str) -> tuple[dict[str, Any], list[str]]:
    """Run one turn through the real graph: the final state and the nodes that ran, in order."""
    thread: RunnableConfig = {"configurable": {"thread_id": str(uuid4())}}
    checkpoint_pool = make_checkpoint_pool(deps.settings)
    checkpoint_pool.open(wait=True, timeout=10)
    try:
        graph: ConversationGraph = build_graph(deps.settings, PostgresSaver(checkpoint_pool), deps)
        visited = [
            node
            for update in graph.stream(new_turn(text, "t1"), thread, stream_mode="updates")
            for node in update
        ]
        return dict(graph.get_state(thread).values), visited
    finally:
        checkpoint_pool.close(timeout=5)


def _count(deps: WorkflowDeps, table: str) -> int:
    with psycopg.connect(deps.settings.database_url) as conn:
        query = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
        row = conn.execute(query).fetchone()
    assert row is not None
    return int(row[0])


# --- Routing -----------------------------------------------------------------------------------


def test_sample_messages_cover_every_intent() -> None:
    assert len(SAMPLES) >= 10
    assert {sample["intent"] for sample in SAMPLES} == set(INTENTS)


@pytest.mark.parametrize("sample", SAMPLES, ids=[sample["text"] for sample in SAMPLES])
def test_sample_messages_reach_the_expected_node(
    make_deps: MakeDeps, sample: dict[str, str]
) -> None:
    _, visited = _run(make_deps(), sample["text"])

    assert visited[:2] == ["classify", sample["node"]]
    assert visited[-1] == "finalize"


# --- Clarifying -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "result",
    [
        ClassifyResult(intent="unclear", confidence=0.9),
        ClassifyResult(intent="book", doctor="Dr. Salma Nasser", confidence=0.59),
        ClassifyResult(intent="cancel", confidence=0.0),
    ],
    ids=["unclear", "low-confidence-book", "low-confidence-cancel"],
)
def test_unclear_or_unsure_gets_one_question_and_writes_nothing(
    make_deps: MakeDeps, result: ClassifyResult
) -> None:
    deps = make_deps(ScriptedFakeLLM(classify=[result]))

    state, visited = _run(deps, "hmm")

    assert visited == ["classify", "clarify", "finalize"]
    assert state["outcome"] == CLARIFY
    assert state["messages"][-1].text == CLARIFY_REPLY
    assert _count(deps, "bookings") == 0


@pytest.mark.parametrize("intent", ["find_slots", "book"])
def test_an_unknown_doctor_is_asked_which_one_with_the_valid_names(
    make_deps: MakeDeps, intent: str
) -> None:
    llm = ScriptedFakeLLM(classify=[ClassifyResult(intent=intent, doctor="Dr. Zaid")])
    deps = make_deps(llm)

    state, visited = _run(deps, "with Dr. Zaid please")

    assert visited == ["classify", "clarify", "finalize"]
    assert state["outcome"] == CLARIFY
    reply = state["messages"][-1].text
    assert all(name in reply for name in (*DOCTORS, *SPECIALTIES))
    assert "Zaid" not in reply  # what the patient typed is never echoed back
    assert _count(deps, "bookings") == 0


def test_an_ambiguous_name_is_asked_which_one_not_told_nobody_exists(
    make_deps: MakeDeps,
) -> None:
    """A name that matches no single doctor (here only the title) falls to the same question."""
    llm = ScriptedFakeLLM(classify=[ClassifyResult(intent="book", doctor="Dr.")])

    state, visited = _run(make_deps(llm), "book me with the doctor")

    assert visited == ["classify", "clarify", "finalize"]
    reply = state["messages"][-1].text
    assert reply.startswith("I'm not sure which doctor or specialty you mean.")
    assert all(name in reply for name in DOCTORS)


def test_an_unknown_specialty_is_asked_which_one(make_deps: MakeDeps) -> None:
    llm = ScriptedFakeLLM(classify=[ClassifyResult(intent="find_slots", specialty="Cardiology")])

    state, visited = _run(make_deps(llm), "any cardiology slots?")

    assert visited == ["classify", "clarify", "finalize"]
    assert "General Practice" in state["messages"][-1].text


def test_a_known_doctor_by_a_short_name_reaches_find_slots_as_the_exact_name(
    make_deps: MakeDeps,
) -> None:
    llm = ScriptedFakeLLM(
        classify=[ClassifyResult(intent="find_slots", doctor="salma", date="2026-11-03")]
    )

    state, visited = _run(make_deps(llm), "salma tomorrow?")

    assert visited == ["classify", "find_slots", "finalize"]
    assert state["entities"] == {"doctor": "Dr. Salma Nasser", "date": "2026-11-03"}
    assert state["outcome"] == "slots_listed"


def test_an_invalid_date_from_the_model_is_dropped(make_deps: MakeDeps) -> None:
    result = ClassifyResult.model_construct(intent="find_slots", date="not a date")
    deps = make_deps(ScriptedFakeLLM(classify=[result]))

    update = make_classify(deps)(new_turn("when?", "t1"))

    assert update["entities"] == {}


# --- What the classifier receives ---------------------------------------------------------------


def _conversation(count: int) -> list[AnyMessage]:
    """`count` messages alternating patient, assistant, ..., ending with a patient message."""
    messages: list[AnyMessage] = [
        HumanMessage(content=f"patient {n}") if n % 2 == 0 else AIMessage(content=f"reply {n}")
        for n in range(count)
    ]
    assert isinstance(messages[-1], HumanMessage)
    return messages


def test_classify_gets_at_most_history_window_messages_today_and_the_catalogue(
    make_deps: MakeDeps,
) -> None:
    llm = ScriptedFakeLLM(classify=[ClassifyResult(intent="other")])
    # 21:00 UTC on Monday is already 01:00 on Tuesday at the clinic.
    clock = FixedClock(datetime(2026, 11, 2, 21, 0, tzinfo=UTC))
    deps = make_deps(llm, clock, history_window=10)
    state: ConversationState = {"messages": _conversation(31)}

    make_classify(deps)(state)

    [(text, context)] = llm.classify_calls
    assert text == "patient 30"
    assert len(context.history) + 1 <= 10
    assert context.history[-1].text == "reply 29"
    assert context.history[0].role == "patient"  # the window starts with a patient message
    assert context.today == date(2026, 11, 3)
    assert context.doctors == DOCTORS
    assert context.specialties == SPECIALTIES


def test_classify_with_a_single_message_has_no_history(make_deps: MakeDeps) -> None:
    llm = ScriptedFakeLLM(classify=[ClassifyResult(intent="other")])

    make_classify(make_deps(llm))(new_turn("hello", "t1"))

    [(_, context)] = llm.classify_calls
    assert context.history == ()


def test_a_model_outage_stops_the_turn_before_any_reply(make_deps: MakeDeps) -> None:
    deps = make_deps(ScriptedFakeLLM(classify=[LLMUnavailable("down")]))

    with pytest.raises(LLMUnavailable):
        _run(deps, "hello")

    assert _count(deps, "bookings") == 0


# --- Other ---------------------------------------------------------------------------------


def test_other_is_a_fixed_capabilities_message_with_no_model_text(make_deps: MakeDeps) -> None:
    llm = ScriptedFakeLLM(classify=[ClassifyResult(intent="other")])

    state, visited = _run(make_deps(llm), "Hello")

    assert visited == ["classify", "other", "finalize"]
    assert state["outcome"] == OTHER
    assert state["messages"][-1].text == CAPABILITIES_REPLY
    assert llm.answer_calls == []


def test_a_database_outage_while_loading_the_catalogue_is_a_json_503(
    make_settings: Callable[..., Settings], caplog: pytest.LogCaptureFixture
) -> None:
    settings = make_settings(db_pool_timeout_s=1)  # the default test database is unreachable
    pool = make_pool(settings)
    pool.open(wait=False)
    deps = WorkflowDeps(settings=settings, pool=pool, clock=FixedClock(NOW), llm=ScriptedFakeLLM())
    classify = make_classify(deps)
    app = create_app(settings)

    @app.get("/boom")
    def boom() -> None:
        classify(new_turn("hello", "t1"))

    try:
        with TestClient(app) as client, caplog.at_level(logging.ERROR, logger="clinic.errors"):
            response = client.get("/boom")
    finally:
        pool.close(timeout=5)

    assert response.status_code == 503
    assert response.json()["code"] == "database_unavailable"
    assert [r for r in caplog.records if r.name == "clinic.errors" and r.levelno == logging.ERROR]


def test_an_empty_catalogue_asks_the_generic_question_instead_of_an_empty_list() -> None:
    result = ClassifyResult(intent="book", doctor="Dr. Salma Nasser")

    entities = _entities(result, doctors=(), specialties=())

    assert entities == {"unknown_doctor": "Dr. Salma Nasser"}
    update = clarify({"entities": entities})
    assert update["reply"] == CLARIFY_REPLY

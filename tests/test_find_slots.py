"""The `find_slots` node on a freshly seeded real PostgreSQL, with a fixed clock."""

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres import PostgresSaver

from clinic.app import prepare_database
from clinic.clock import FixedClock
from clinic.config import Settings
from clinic.db import make_checkpoint_pool, make_pool
from clinic.workflows.deps import WorkflowDeps
from clinic.workflows.graph import build_graph
from clinic.workflows.nodes import NODES
from clinic.workflows.nodes._types import Node
from clinic.workflows.nodes.appointments import make_find_slots
from clinic.workflows.state import ConversationState, new_turn
from tests.conftest import temporary_database

# Monday 2026-11-02, 12:00 at the clinic (Asia/Muscat, UTC+4).
NOW = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
SALMA = "Dr. Salma Nasser"


@pytest.fixture
def deps(database_url: str) -> Iterator[WorkflowDeps]:
    with temporary_database(database_url) as url:
        settings = Settings(llm_provider="fake", database_url=url)
        clock = FixedClock(NOW)
        assert prepare_database(settings, clock)  # tables, seed, checkpointer
        pool = make_pool(settings)
        pool.open(wait=True, timeout=10)
        try:
            yield WorkflowDeps(settings=settings, pool=pool, clock=clock)
        finally:
            pool.close(timeout=5)


@pytest.fixture
def find_slots(deps: WorkflowDeps) -> Node:
    return make_find_slots(deps)


def _ask(node: Node, **entities: str) -> dict[str, object]:
    state: ConversationState = {**new_turn("free slots?", "t1"), "entities": entities}
    return node(state)


def _book_out(deps: WorkflowDeps, doctor_id: int, local_day: str | None = None) -> None:
    """Mark a doctor's slots booked: one clinic day, or every day when `local_day` is None."""
    with psycopg.connect(deps.settings.database_url, autocommit=True) as conn:
        conn.execute(
            """
            UPDATE slots SET status = 'booked'
            WHERE doctor_id = %(doctor_id)s
              AND (%(day)s::date IS NULL
                   OR (starts_at AT TIME ZONE 'Asia/Muscat')::date = %(day)s::date)
            """,
            {"doctor_id": doctor_id, "day": local_day},
        )


def _offered(update: dict[str, object]) -> list[dict[str, str | int]]:
    offered = update["offered_slots"]
    assert isinstance(offered, list)
    return offered


# --- Slots listed ---------------------------------------------------------------------------


def test_doctor_and_date_list_five_numbered_slots_in_clinic_time(find_slots: Node) -> None:
    update = _ask(find_slots, doctor=SALMA, date="2026-11-03")

    assert update["outcome"] == "slots_listed"
    reply = str(update["reply"])
    assert reply.startswith("Free slots with Dr. Salma Nasser on Tuesday 3 November:")
    for line in ("1. 09:00", "2. 09:30", "3. 10:00", "4. 10:30", "5. 11:00"):
        assert line in reply
    assert "6." not in reply
    offered = _offered(update)
    assert len(offered) == 5
    assert {slot["doctor"] for slot in offered} == {SALMA}
    assert offered[0]["starts_at"] == "2026-11-03T09:00:00+04:00"
    assert all(isinstance(slot["slot_id"], int) for slot in offered)


def test_specialty_works_too(find_slots: Node) -> None:
    update = _ask(find_slots, specialty="general practice", date="2026-11-03")

    assert update["outcome"] == "slots_listed"
    assert "in General Practice" in str(update["reply"])
    assert {slot["doctor"] for slot in _offered(update)} == {"Dr. Khalid Rahman"}


@pytest.mark.parametrize("date", [None, "2026-11-01", "not a date"])
def test_no_past_or_missing_date_means_today_from_now_on(
    find_slots: Node, date: str | None
) -> None:
    entities = {"doctor": SALMA} if date is None else {"doctor": SALMA, "date": date}

    update = _ask(find_slots, **entities)

    assert "Monday 2 November" in str(update["reply"])
    assert _offered(update)[0]["starts_at"] == "2026-11-02T12:30:00+04:00"  # 12:00 has started


def test_offered_slots_are_kept_in_the_checkpoint(
    deps: WorkflowDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    def classify_find_slots(state: ConversationState) -> dict[str, object]:
        return {
            "intent": "find_slots",
            "confidence": 1.0,
            "entities": {"doctor": SALMA, "date": "2026-11-03"},
        }

    monkeypatch.setitem(NODES, "classify", classify_find_slots)
    thread: RunnableConfig = {"configurable": {"thread_id": str(uuid4())}}
    checkpoint_pool = make_checkpoint_pool(deps.settings)
    checkpoint_pool.open(wait=True, timeout=10)
    try:
        graph = build_graph(deps.settings, PostgresSaver(checkpoint_pool), deps)
        graph.invoke(new_turn("When is Dr. Salma free tomorrow?", "t1"), thread)
        state = graph.get_state(thread).values
    finally:
        checkpoint_pool.close(timeout=5)

    assert state["outcome"] == "slots_listed"
    assert len(state["offered_slots"]) == 5
    assert state["messages"][-1].text.startswith("Free slots with Dr. Salma Nasser")


# --- No slots -------------------------------------------------------------------------------


def test_full_day_offers_the_next_day_with_free_slots(deps: WorkflowDeps, find_slots: Node) -> None:
    _book_out(deps, doctor_id=1, local_day="2026-11-03")

    update = _ask(find_slots, doctor=SALMA, date="2026-11-03")

    assert update["outcome"] == "no_slots"
    reply = str(update["reply"])
    assert "no free slots with Dr. Salma Nasser on Tuesday 3 November" in reply
    assert "The next day with free slots is Wednesday 4 November" in reply
    assert "1. 09:00" in reply
    offered = _offered(update)
    assert len(offered) == 5
    assert all(str(slot["starts_at"]).startswith("2026-11-04") for slot in offered)


def test_no_free_slot_at_all_says_so_and_offers_nothing(
    deps: WorkflowDeps, find_slots: Node
) -> None:
    _book_out(deps, doctor_id=1)

    update = _ask(find_slots, doctor=SALMA, date="2026-11-03")

    assert update["outcome"] == "no_slots"
    assert "call the front desk" in str(update["reply"])
    assert _offered(update) == []


# --- Unknown doctor -------------------------------------------------------------------------


@pytest.mark.parametrize("entities", [{}, {"doctor": "Dr. Nobody"}, {"specialty": "Cardiology"}])
def test_unknown_or_missing_doctor_asks_which_one(
    find_slots: Node, entities: dict[str, str]
) -> None:
    update = _ask(find_slots, **entities)

    assert update["outcome"] == "clarify"
    assert "Dr. Salma Nasser (Dermatology)" in str(update["reply"])
    assert "Dr. Khalid Rahman (General Practice)" in str(update["reply"])
    assert "offered_slots" not in update  # earlier offers stay valid

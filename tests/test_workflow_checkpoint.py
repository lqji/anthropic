"""PostgresSaver checkpointing on a real PostgreSQL: restart, setup, and the history window."""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.conninfo import make_conninfo

from clinic.app import create_app
from clinic.config import Settings
from clinic.db import make_checkpoint_pool
from clinic.workflows.checkpointer import setup_checkpointer
from clinic.workflows.graph import ConversationGraph, build_graph
from clinic.workflows.history import history_window
from clinic.workflows.nodes import NODES
from clinic.workflows.nodes.classify import classify
from clinic.workflows.state import ConversationState, new_turn

CHECKPOINT_TABLES = {
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
}


@pytest.fixture
def settings(make_settings: Callable[..., Settings], database_url: str) -> Settings:
    setup_checkpointer(database_url)
    return make_settings(database_url=database_url, history_window=10)


@pytest.fixture
def fresh_database_url(database_url: str) -> Iterator[str]:
    """A brand-new, empty database on the test server, dropped afterwards."""
    name = f"cfd05_{uuid4().hex}"
    with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as admin:
        admin.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name)))
    try:
        yield make_conninfo(database_url, dbname=name)
    finally:
        with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as admin:
            admin.execute(
                psycopg.sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    psycopg.sql.Identifier(name)
                )
            )


@contextmanager
def running_graph(settings: Settings) -> Iterator[ConversationGraph]:
    """One 'process lifetime': its own pool, saver and graph, all closed at the end."""
    pool = make_checkpoint_pool(settings)
    pool.open(wait=True, timeout=10)
    try:
        yield build_graph(settings, PostgresSaver(pool))
    finally:
        pool.close(timeout=5)


def _thread() -> RunnableConfig:
    return {"configurable": {"thread_id": str(uuid4())}}


def _tables(database_url: str) -> set[str]:
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchall()
    return {str(row[0]) for row in rows}


# --- AC1: context survives a restart --------------------------------------------------------


def test_third_turn_after_restart_sees_all_earlier_messages(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread = _thread()
    with running_graph(settings) as graph:
        graph.invoke(new_turn("first"), thread)
        graph.invoke(new_turn("second"), thread)

    seen_by_classify: list[list[str]] = []

    def spying_classify(state: ConversationState) -> dict[str, object]:
        seen_by_classify.append([m.text for m in state["messages"]])
        return classify(state)

    monkeypatch.setitem(NODES, "classify", spying_classify)
    with running_graph(settings) as graph:  # new pool, saver and graph: a simulated restart
        result = graph.invoke(new_turn("third"), thread)

    texts = [m.text for m in result["messages"]]
    assert texts[0::2] == ["first", "second", "third"]
    assert len(texts) == 6
    # The node running in the third turn saw both earlier turns, not just the new message.
    assert seen_by_classify == [texts[:5]]


def test_threads_do_not_share_context(settings: Settings) -> None:
    first, second = _thread(), _thread()
    with running_graph(settings) as graph:
        graph.invoke(new_turn("only in the first thread"), first)
        result = graph.invoke(new_turn("hello"), second)

    assert [m.text for m in result["messages"] if isinstance(m, HumanMessage)] == ["hello"]


# --- AC2: checkpoint tables are created on start, setup is idempotent -----------------------


def test_api_starts_twice_on_a_fresh_database(
    fresh_database_url: str,
    make_settings: Callable[..., Settings],
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = make_settings(database_url=fresh_database_url)
    assert _tables(fresh_database_url) == set()

    with caplog.at_level(logging.ERROR):
        for _ in range(2):
            with TestClient(create_app(settings)) as client:
                assert client.get("/health").status_code == 200

    assert CHECKPOINT_TABLES | {"conversations", "messages"} <= _tables(fresh_database_url)
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == []


def test_setup_twice_raises_nothing_and_runs_each_migration_once(fresh_database_url: str) -> None:
    setup_checkpointer(fresh_database_url)
    setup_checkpointer(fresh_database_url)

    with psycopg.connect(fresh_database_url, connect_timeout=5) as conn:
        row = conn.execute(
            "SELECT count(*), count(DISTINCT v) FROM checkpoint_migrations"
        ).fetchone()
    assert row == (len(PostgresSaver.MIGRATIONS), len(PostgresSaver.MIGRATIONS))


def test_api_starts_with_the_database_down_and_logs_it(
    make_settings: Callable[..., Settings], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="clinic.startup"):
        with TestClient(create_app(make_settings(db_pool_timeout_s=1))) as client:
            response = client.get("/health")

    assert response.status_code == 503
    assert [r.getMessage() for r in caplog.records if r.name == "clinic.startup"] == [
        "startup.database_unavailable"
    ]


# --- AC3: 30 messages, a window of 10, the checkpoint keeps everything ----------------------


def test_window_of_thirty_checkpointed_messages_is_the_last_ten(settings: Settings) -> None:
    thread = _thread()
    with running_graph(settings) as graph:
        for n in range(15):  # 15 turns = 15 patient messages + 15 replies
            graph.invoke(new_turn(f"message {n}"), thread)
        messages = graph.get_state(thread).values["messages"]

    window = history_window(messages, settings.history_window)

    assert len(messages) == 30
    assert len(window) <= 10
    assert isinstance(window[0], HumanMessage)
    assert window == messages[-10:]
    assert window[0].text == "message 10"

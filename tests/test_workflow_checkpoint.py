"""PostgresSaver checkpointing on a real PostgreSQL: restart, setup, and the history window."""

import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import TupleRow

from clinic import app as app_module
from clinic.app import create_app, keep_preparing
from clinic.clock import Clock, SystemClock
from clinic.config import Settings
from clinic.db import make_checkpoint_pool
from clinic.workflows.checkpointer import setup_checkpointer
from clinic.workflows.graph import ConversationGraph, build_graph
from clinic.workflows.history import history_window
from clinic.workflows.nodes import NODES
from clinic.workflows.nodes.classify import classify
from clinic.workflows.state import ConversationState, new_turn, turn_id_for

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


def _admin(database_url: str, statement: str, name: str) -> None:
    """Run CREATE/DROP DATABASE `name` on the test server (cannot run inside a transaction)."""
    with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as admin:
        admin.execute(psycopg.sql.SQL(statement).format(psycopg.sql.Identifier(name)))


@pytest.fixture
def missing_database_url(database_url: str) -> Iterator[str]:
    """URL of a database that does not exist yet (connecting fails); dropped afterwards."""
    name = f"cfd05_{uuid4().hex}"
    try:
        yield make_conninfo(database_url, dbname=name)
    finally:
        _admin(database_url, "DROP DATABASE IF EXISTS {} WITH (FORCE)", name)


@pytest.fixture
def fresh_database_url(database_url: str, missing_database_url: str) -> str:
    """A brand-new, empty database on the test server, dropped afterwards."""
    _admin(database_url, "CREATE DATABASE {}", _dbname(missing_database_url))
    return missing_database_url


def _dbname(url: str) -> str:
    return str(conninfo_to_dict(url)["dbname"])


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
        graph.invoke(new_turn("first", "t1"), thread)
        graph.invoke(new_turn("second", "t2"), thread)

    seen_by_classify: list[list[str]] = []

    def spying_classify(state: ConversationState) -> dict[str, object]:
        seen_by_classify.append([m.text for m in state["messages"]])
        return classify(state)

    monkeypatch.setitem(NODES, "classify", spying_classify)
    with running_graph(settings) as graph:  # new pool, saver and graph: a simulated restart
        result = graph.invoke(new_turn("third", "t3"), thread)

    texts = [m.text for m in result["messages"]]
    assert texts[0::2] == ["first", "second", "third"]
    assert len(texts) == 6
    # The node running in the third turn saw both earlier turns, not just the new message.
    assert seen_by_classify == [texts[:5]]


def test_a_replayed_turn_replaces_itself_instead_of_duplicating(settings: Settings) -> None:
    # The service ran turn 2 but failed to store it, so the patient's retry gets turn number 2
    # again, after a restart in this test.
    conversation_id = str(uuid4())
    thread: RunnableConfig = {"configurable": {"thread_id": conversation_id}}
    with running_graph(settings) as graph:
        graph.invoke(new_turn("first", turn_id_for(conversation_id, 1)), thread)
        graph.invoke(new_turn("second", turn_id_for(conversation_id, 2)), thread)
    with running_graph(settings) as graph:
        graph.invoke(new_turn("second", turn_id_for(conversation_id, 2)), thread)
        graph.invoke(new_turn("third", turn_id_for(conversation_id, 3)), thread)
        messages = graph.get_state(thread).values["messages"]

    assert [m.text for m in messages if isinstance(m, HumanMessage)] == [
        "first",
        "second",
        "third",
    ]
    assert len(messages) == 6
    assert [m.id for m in messages] == [
        f"{conversation_id}:{n}:{role}" for n in (1, 2, 3) for role in ("patient", "reply")
    ]


def test_threads_do_not_share_context(settings: Settings) -> None:
    first, second = _thread(), _thread()
    with running_graph(settings) as graph:
        graph.invoke(new_turn("only in the first thread", "t1"), first)
        result = graph.invoke(new_turn("hello", "t1"), second)

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


def test_tables_are_created_when_the_database_comes_up_after_the_api(
    database_url: str,
    missing_database_url: str,
    make_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(app_module, "PREPARE_RETRY_S", 0.1)
    settings = make_settings(database_url=missing_database_url, db_pool_timeout_s=1)

    with caplog.at_level(logging.INFO, logger="clinic.startup"):
        with TestClient(create_app(settings)) as client:
            assert client.get("/health").status_code == 503  # database not there yet
            _admin(database_url, "CREATE DATABASE {}", _dbname(missing_database_url))
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not _logged(
                caplog, "startup.database_prepared_after_retry"
            ):
                time.sleep(0.1)

    assert _logged(caplog, "startup.database_unavailable")
    assert _logged(caplog, "startup.database_prepared_after_retry")
    assert CHECKPOINT_TABLES | {"conversations", "messages"} <= _tables(missing_database_url)


def test_retry_stops_on_an_error_that_will_not_fix_itself(
    make_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[Settings] = []

    def broken_prepare(settings: Settings, clock: Clock) -> bool:
        calls.append(settings)
        raise ValueError("bad migration")

    monkeypatch.setattr(app_module, "PREPARE_RETRY_S", 0.01)
    monkeypatch.setattr(app_module, "prepare_database", broken_prepare)

    with caplog.at_level(logging.ERROR, logger="clinic.startup"):
        keep_preparing(
            make_settings(), SystemClock(), threading.Event()
        )  # returns instead of looping

    assert len(calls) == 1
    assert _logged(caplog, "startup.database_prepare_failed")


@pytest.mark.parametrize(
    ("error", "unreachable"),
    [
        (psycopg.OperationalError("connection refused"), True),  # no SQLSTATE: never connected
        (psycopg.errors.ConnectionFailure(), True),  # 08006
        (psycopg.errors.CannotConnectNow(), True),  # 57P03: server still starting up
        (psycopg.errors.AdminShutdown(), True),  # 57P01: server restarting
        (psycopg.errors.QueryCanceled(), False),  # 57014: statement timeout
    ],
)
def test_only_an_unreachable_database_counts_as_unreachable(
    error: psycopg.OperationalError, unreachable: bool
) -> None:
    assert app_module.is_unreachable(error) is unreachable


def test_a_timed_out_migration_stops_startup_instead_of_being_retried(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def slow_schema(conn: psycopg.Connection[TupleRow], db_dir: Path) -> None:
        conn.execute("SET statement_timeout = 10")
        conn.execute("SELECT pg_sleep(1)")

    monkeypatch.setattr(app_module, "apply_schema", slow_schema)

    with caplog.at_level(logging.ERROR, logger="clinic.startup"):
        with pytest.raises(psycopg.errors.QueryCanceled):
            app_module.prepare_database(settings, SystemClock())

    assert not _logged(caplog, "startup.database_unavailable")


def test_setup_connections_use_the_long_statement_timeout(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    timeouts: dict[str, object] = {}

    def schema_spy(conn: psycopg.Connection[TupleRow], db_dir: Path) -> None:
        row = conn.execute("SHOW statement_timeout").fetchone()
        timeouts["schema"] = None if row is None else row[0]

    def setup_spy(saver: PostgresSaver) -> None:
        assert isinstance(saver.conn, psycopg.Connection)
        row = saver.conn.execute("SHOW statement_timeout").fetchone()
        timeouts["checkpointer"] = None if row is None else row["statement_timeout"]

    monkeypatch.setattr(app_module, "apply_schema", schema_spy)
    monkeypatch.setattr(PostgresSaver, "setup", setup_spy)

    assert app_module.prepare_database(settings, SystemClock()) is True
    assert timeouts == {"schema": "5min", "checkpointer": "5min"}  # not the 5 s request timeout


def _logged(caplog: pytest.LogCaptureFixture, message: str) -> bool:
    return any(r.getMessage() == message for r in caplog.records)


# --- AC3: 30 messages, a window of 10, the checkpoint keeps everything ----------------------


def test_window_of_thirty_checkpointed_messages_is_the_last_ten(settings: Settings) -> None:
    thread = _thread()
    with running_graph(settings) as graph:
        for n in range(15):  # 15 turns = 15 patient messages + 15 replies
            graph.invoke(new_turn(f"message {n}", f"t{n}"), thread)
        messages = graph.get_state(thread).values["messages"]

    window = history_window(messages, settings.history_window)

    assert len(messages) == 30
    assert len(window) <= 10
    assert isinstance(window[0], HumanMessage)
    assert window == messages[-10:]
    assert window[0].text == "message 10"

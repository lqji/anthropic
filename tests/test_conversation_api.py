import logging
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from clinic import app as app_module
from clinic.app import create_app
from clinic.config import Settings
from clinic.errors import LLMUnavailable
from clinic.repositories import conversations as conversations_repository
from clinic.repositories import messages as messages_repository
from clinic.schema import apply_schema
from clinic.services.conversation import TurnResult


class FakeWorkflow:
    """Stands in for CFD-05's graph.

    `gate` pauses a turn until set; `barrier` makes turns wait for each other; `error` makes every
    turn fail; `status` is the conversation status every turn returns.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.entered = threading.Event()
        self.gate: threading.Event | None = None
        self.barrier: threading.Barrier | None = None
        self.error: Exception | None = None
        self.status = "active"

    def run_turn(self, conversation_id: str, text: str) -> TurnResult:
        self.calls.append((conversation_id, text))
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(10)
        if self.barrier is not None:
            self.barrier.wait(timeout=10)
        if self.error is not None:
            raise self.error
        return TurnResult(
            reply=f"echo: {text}", intent="general_question", outcome="answered", status=self.status
        )


@pytest.fixture
def settings(make_settings: Callable[..., Settings], database_url: str) -> Settings:
    return make_settings(database_url=database_url)


@pytest.fixture
def workflow() -> FakeWorkflow:
    return FakeWorkflow()


@pytest.fixture
def client(settings: Settings, database_url: str, workflow: FakeWorkflow) -> Iterator[TestClient]:
    with TestClient(create_app(settings, workflow=workflow)) as test_client:
        with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as conn:
            conn.execute("TRUNCATE conversations CASCADE")
        yield test_client


def _start(client: TestClient, patient_ref: str = "demo-patient-1") -> str:
    response = client.post("/api/v1/conversations", json={"patient_ref": patient_ref})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


# --- Start a conversation -------------------------------------------------------------------


def test_start_conversation_returns_201_with_uuid_and_active(client: TestClient) -> None:
    response = client.post("/api/v1/conversations", json={"patient_ref": "demo-patient-1"})

    assert response.status_code == 201
    body = response.json()
    assert UUID(body["id"]).version == 4
    assert body["status"] == "active"
    assert body["patient_ref"] == "demo-patient-1"


@pytest.mark.parametrize("payload", [{}, {"patient_ref": ""}, {"patient_ref": "   "}])
def test_blank_or_missing_patient_ref_is_422(client: TestClient, payload: dict[str, str]) -> None:
    response = client.post("/api/v1/conversations", json=payload)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


# --- Send a message -------------------------------------------------------------------------


def test_message_returns_reply_intent_outcome_status(
    client: TestClient, workflow: FakeWorkflow
) -> None:
    conversation_id = _start(client)

    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages", json={"text": "hello"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "reply": "echo: hello",
        "intent": "general_question",
        "outcome": "answered",
        "status": "active",
        "sources": [],
    }
    assert workflow.calls == [(conversation_id, "hello")]


@pytest.mark.parametrize("text", ["", "   ", "x" * 2001])
def test_empty_or_too_long_message_is_422(
    client: TestClient, workflow: FakeWorkflow, text: str
) -> None:
    conversation_id = _start(client)

    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": text})

    assert response.status_code == 422
    assert workflow.calls == []


def test_message_of_exactly_2000_chars_is_accepted(client: TestClient) -> None:
    conversation_id = _start(client)
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages", json={"text": "x" * 2000}
    )
    assert response.status_code == 200


@pytest.mark.parametrize("conversation_id", [str(uuid4()), "not-a-uuid"])
def test_unknown_conversation_is_404_not_found(
    client: TestClient, workflow: FakeWorkflow, conversation_id: str
) -> None:
    send = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "hi"})
    get = client.get(f"/api/v1/conversations/{conversation_id}")

    for response in (send, get):
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"
    assert workflow.calls == []


def test_failed_turn_stores_nothing_and_retry_is_clean(
    client: TestClient, workflow: FakeWorkflow
) -> None:
    conversation_id = _start(client)
    url = f"/api/v1/conversations/{conversation_id}/messages"
    workflow.error = LLMUnavailable("model timed out")

    failed = client.post(url, json={"text": "hello"})
    after_failure = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]
    workflow.error = None
    retried = client.post(url, json={"text": "hello"})
    after_retry = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]

    assert failed.status_code == 503
    assert failed.json()["code"] == "llm_unavailable"
    assert after_failure == []
    assert retried.status_code == 200
    assert [m["text"] for m in after_retry] == ["hello", "echo: hello"]


def test_invalid_workflow_status_is_503_and_stores_nothing(
    client: TestClient, workflow: FakeWorkflow, caplog: pytest.LogCaptureFixture
) -> None:
    conversation_id = _start(client)
    workflow.status = "paused-ish"

    with caplog.at_level(logging.ERROR, logger="clinic.conversation"):
        response = client.post(
            f"/api/v1/conversations/{conversation_id}/messages", json={"text": "hello"}
        )

    assert response.status_code == 503
    assert response.json()["code"] == "workflow_unavailable"
    assert client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"] == []
    assert [r.getMessage() for r in caplog.records if r.name == "clinic.conversation"] == [
        "conversation.invalid_workflow_status"
    ]


# --- Read a conversation back ---------------------------------------------------------------


def test_get_returns_all_messages_in_order(client: TestClient) -> None:
    conversation_id = _start(client)
    for text in ("first", "second", "third"):
        client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": text})

    response = client.get(f"/api/v1/conversations/{conversation_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == conversation_id
    assert [(m["role"], m["text"]) for m in body["messages"]] == [
        ("patient", "first"),
        ("assistant", "echo: first"),
        ("patient", "second"),
        ("assistant", "echo: second"),
        ("patient", "third"),
        ("assistant", "echo: third"),
    ]
    assistant = body["messages"][1]
    assert (assistant["intent"], assistant["outcome"]) == ("general_question", "answered")
    assert body["messages"][0]["intent"] is None


# --- Restart, concurrency -------------------------------------------------------------------


def test_second_app_instance_sees_the_same_messages_and_continues(
    settings: Settings, database_url: str
) -> None:
    with TestClient(create_app(settings, workflow=FakeWorkflow())) as first_app:
        conversation_id = _start(first_app)
        first_app.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "one"})

    second_workflow = FakeWorkflow()
    with TestClient(create_app(settings, workflow=second_workflow)) as second_app:
        before = second_app.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]
        response = second_app.post(
            f"/api/v1/conversations/{conversation_id}/messages", json={"text": "two"}
        )
        after = second_app.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]

    assert [m["text"] for m in before] == ["one", "echo: one"]
    assert response.status_code == 200
    assert second_workflow.calls == [(conversation_id, "two")]
    assert [m["text"] for m in after] == ["one", "echo: one", "two", "echo: two"]


def test_two_simultaneous_messages_one_200_one_409(
    client: TestClient, workflow: FakeWorkflow
) -> None:
    conversation_id = _start(client)
    url = f"/api/v1/conversations/{conversation_id}/messages"
    workflow.gate = threading.Event()

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(client.post, url, json={"text": "first"})
        assert workflow.entered.wait(5)
        second = client.post(url, json={"text": "second"})
        workflow.gate.set()
        first_response = first.result(timeout=10)

    assert first_response.status_code == 200
    assert second.status_code == 409
    assert second.json()["code"] == "conversation_busy"
    messages = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]
    assert [m["text"] for m in messages] == ["first", "echo: first"]


def test_ten_conversations_messaged_at_once_all_200(
    make_settings: Callable[..., Settings], database_url: str
) -> None:
    # The barrier only opens when all ten turns are inside the workflow at the same time. With
    # a pool of 2, holding a connection during the workflow would starve the other eight (503).
    workflow = FakeWorkflow()
    workflow.barrier = threading.Barrier(10)
    settings = make_settings(database_url=database_url, db_pool_max_size=2)

    with TestClient(create_app(settings, workflow=workflow)) as client:
        conversation_ids = [_start(client, f"patient-{n}") for n in range(10)]
        with ThreadPoolExecutor(max_workers=10) as pool:
            responses = list(
                pool.map(
                    lambda cid: client.post(
                        f"/api/v1/conversations/{cid}/messages", json={"text": "hello"}
                    ),
                    conversation_ids,
                )
            )

    assert [r.status_code for r in responses] == [200] * 10
    assert sorted(cid for cid, _ in workflow.calls) == sorted(conversation_ids)
    assert not workflow.barrier.broken


# --- Database failures ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/v1/conversations", {"patient_ref": "demo-patient-1"}),
        ("POST", f"/api/v1/conversations/{uuid4()}/messages", {"text": "hello"}),
        ("GET", f"/api/v1/conversations/{uuid4()}", None),
    ],
)
def test_database_down_is_json_503_database_unavailable_and_logged(
    make_settings: Callable[..., Settings],
    caplog: pytest.LogCaptureFixture,
    method: str,
    path: str,
    body: dict[str, str] | None,
) -> None:
    # make_settings defaults to an unreachable database; a 1 s pool timeout keeps the test short.
    settings = make_settings(db_pool_timeout_s=1)
    with TestClient(create_app(settings, workflow=FakeWorkflow())) as client:
        with caplog.at_level(logging.ERROR, logger="clinic.errors"):
            response = client.request(method, path, json=body)

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["code"] == "database_unavailable"
    logged = [r for r in caplog.records if r.name == "clinic.errors"]
    assert [r.getMessage() for r in logged] == ["database_unavailable"]


def test_database_failure_while_storing_the_turn_is_503_and_stores_nothing(
    client: TestClient,
    workflow: FakeWorkflow,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The workflow has already run when the write fails, yet the client gets a clean 503 and
    nothing is stored. That the retry is then safe is pinned by the real-graph tests below."""
    conversation_id = _start(client)

    def broken_add(*args: object, **kwargs: object) -> None:
        raise psycopg.OperationalError("server closed the connection unexpectedly")

    monkeypatch.setattr(messages_repository, "add", broken_add)
    with caplog.at_level(logging.ERROR, logger="clinic.errors"):
        response = client.post(
            f"/api/v1/conversations/{conversation_id}/messages", json={"text": "hello"}
        )
    monkeypatch.undo()

    assert response.status_code == 503
    assert response.json()["code"] == "database_unavailable"
    assert workflow.calls == [(conversation_id, "hello")]  # the workflow did run
    assert client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"] == []
    assert [r.getMessage() for r in caplog.records if r.name == "clinic.errors"] == [
        "database_unavailable"
    ]


# --- The schema does not exist yet ----------------------------------------------------------


@pytest.fixture
def empty_database_url(database_url: str) -> Iterator[str]:
    """A brand-new database with no tables, created and dropped inside the test."""
    name = f"clinic_no_tables_{uuid4().hex[:8]}"
    with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_conninfo(database_url, dbname=name)
    finally:
        with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/v1/conversations", {"patient_ref": "demo-patient-1"}),
        ("POST", f"/api/v1/conversations/{uuid4()}/messages", {"text": "hello"}),
        ("GET", f"/api/v1/conversations/{uuid4()}", None),
    ],
)
def test_missing_tables_are_json_503_database_unavailable_and_logged(
    make_settings: Callable[..., Settings],
    empty_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    method: str,
    path: str,
    body: dict[str, str] | None,
) -> None:
    # The api started while the database was down, so the tables are still being created by the
    # background retry. Turn that retry off: the tables stay missing.
    monkeypatch.setattr(app_module, "prepare_database", lambda settings, clock: True)
    settings = make_settings(database_url=empty_database_url)

    with TestClient(create_app(settings, workflow=FakeWorkflow())) as client:
        with caplog.at_level(logging.ERROR, logger="clinic.errors"):
            response = client.request(method, path, json=body)

    assert response.status_code == 503  # not a 500: this is "not ready yet", not a bug
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["code"] == "database_unavailable"
    logged = [r for r in caplog.records if r.name == "clinic.errors"]
    assert [r.getMessage() for r in logged] == ["database_not_ready"]
    assert logged[0].__dict__["fields"] == {"error": "UndefinedTable"}


def test_the_same_endpoint_works_once_the_tables_exist(
    make_settings: Callable[..., Settings],
    empty_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module, "prepare_database", lambda settings, clock: True)
    settings = make_settings(database_url=empty_database_url)

    with TestClient(create_app(settings, workflow=FakeWorkflow())) as client:
        before = client.post("/api/v1/conversations", json={"patient_ref": "demo-patient-1"})
        with psycopg.connect(empty_database_url, autocommit=True, connect_timeout=5) as conn:
            apply_schema(conn)  # what the background retry does once the database is reachable
        after = client.post("/api/v1/conversations", json={"patient_ref": "demo-patient-1"})

    assert before.status_code == 503
    assert after.status_code == 201


def test_any_other_sql_error_is_still_a_500(
    make_settings: Callable[..., Settings],
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a missing table means "not ready yet". Any other SQL error is a bug, not a 503."""

    def broken_create(*args: object, **kwargs: object) -> None:
        raise psycopg.errors.UndefinedColumn("column does not exist")

    monkeypatch.setattr(conversations_repository, "create", broken_create)
    settings = make_settings(database_url=database_url)

    with TestClient(
        create_app(settings, workflow=FakeWorkflow()), raise_server_exceptions=False
    ) as client:
        response = client.post("/api/v1/conversations", json={"patient_ref": "demo-patient-1"})

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"


# --- The real CFD-05 graph (the default workflow) -------------------------------------------


def _deps(client: TestClient) -> Any:
    app = client.app
    assert isinstance(app, FastAPI)
    return app.state.deps


def _checkpointed(client: TestClient, conversation_id: str) -> list[tuple[str | None, str]]:
    """(message id, type) of the messages the graph has checkpointed for a conversation."""
    state = _deps(client).graph.get_state({"configurable": {"thread_id": conversation_id}})
    return [(m.id, m.type) for m in state.values.get("messages", [])]


def _turn_ids(conversation_id: str, *numbers: int) -> list[tuple[str, str]]:
    """What the checkpoint holds after turns `numbers`: a patient and a reply message each."""
    return [
        (f"{conversation_id}:{n}:{kind}", kind_type)
        for n in numbers
        for kind, kind_type in (("patient", "human"), ("reply", "ai"))
    ]


def test_default_workflow_runs_the_checkpointed_graph(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        conversation_id = _start(client)

        response = client.post(
            f"/api/v1/conversations/{conversation_id}/messages", json={"text": "hello"}
        )
        stored = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]
        checkpointed = _checkpointed(client, conversation_id)

    # What the graph answers depends on the nodes that have landed (stubs, then CFD-07's classify),
    # so check the contract: a full answer, the same reply stored, and one checkpointed turn.
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"reply", "intent", "outcome", "status", "sources"}
    assert all(isinstance(body[key], str) and body[key] for key in body if key != "sources")
    assert isinstance(body["sources"], list)
    assert [(m["role"], m["text"]) for m in stored] == [
        ("patient", "hello"),
        ("assistant", body["reply"]),
    ]
    assert checkpointed == _turn_ids(conversation_id, 1)


def test_second_app_instance_continues_the_checkpointed_conversation(settings: Settings) -> None:
    with TestClient(create_app(settings)) as first_app:
        conversation_id = _start(first_app)
        first_app.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "one"})

    with TestClient(create_app(settings)) as second_app:  # a restart: new pools, new graph
        response = second_app.post(
            f"/api/v1/conversations/{conversation_id}/messages", json={"text": "two"}
        )
        checkpointed = _checkpointed(second_app, conversation_id)

    assert response.status_code == 200
    assert checkpointed == _turn_ids(conversation_id, 1, 2)  # turn 2 continued after turn 1


def test_replayed_turn_replaces_itself_in_the_checkpoint(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The graph ran, but storing the turn failed. The retry must not duplicate the turn."""

    def broken_add(*args: object, **kwargs: object) -> None:
        raise psycopg.OperationalError("server closed the connection unexpectedly")

    with TestClient(create_app(settings)) as client:
        conversation_id = _start(client)
        url = f"/api/v1/conversations/{conversation_id}/messages"

        monkeypatch.setattr(messages_repository, "add", broken_add)
        failed = client.post(url, json={"text": "hello"})
        monkeypatch.undo()
        assert _checkpointed(client, conversation_id) == _turn_ids(conversation_id, 1)  # it ran

        retried = client.post(url, json={"text": "hello"})
        checkpointed = _checkpointed(client, conversation_id)
        stored = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]

    assert failed.status_code == 503
    assert retried.status_code == 200
    assert checkpointed == _turn_ids(conversation_id, 1)  # replaced, not appended
    assert [m["text"] for m in stored] == ["hello", retried.json()["reply"]]


def test_replay_after_an_interleaved_emergency_still_replaces(
    settings: Settings, database_url: str
) -> None:
    """An emergency is stored but never reaches the graph, so it must not renumber the turns."""
    with TestClient(create_app(settings)) as client:
        conversation_id = _start(client)
        workflow = _deps(client).workflow
        workflow.run_turn(conversation_id, "hello")  # the graph ran; storing the turn failed

        with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as conn:
            now = datetime.now(UTC)
            cid = UUID(conversation_id)
            messages_repository.add(conn, cid, "patient", "I have chest pain", now)
            messages_repository.add(
                conn, cid, "assistant", "Call 9999", now, intent="emergency", outcome="emergency"
            )
        workflow.run_turn(conversation_id, "hello")  # the retry
        checkpointed = _checkpointed(client, conversation_id)

    assert checkpointed == _turn_ids(conversation_id, 1)  # still turn 1, so it replaced itself


def test_count_graph_turns_skips_replies_that_did_not_come_from_the_graph(
    client: TestClient, database_url: str
) -> None:
    conversation_id = UUID(_start(client))
    now = datetime.now(UTC)
    with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as conn:
        add = messages_repository.add
        assert messages_repository.count_graph_turns(conn, conversation_id) == 0
        add(conn, conversation_id, "patient", "hello", now)
        add(conn, conversation_id, "assistant", "hi", now, intent="other", outcome="answered")
        add(conn, conversation_id, "patient", "chest pain", now)
        add(
            conn, conversation_id, "assistant", "call", now, intent="emergency", outcome="emergency"
        )
        add(conn, conversation_id, "patient", "any news?", now)
        add(conn, conversation_id, "assistant", "wait", now, intent="x", outcome="still_waiting")
        add(conn, conversation_id, "assistant", "cancelled", now, intent="x", outcome="cancelled")
        add(conn, conversation_id, "patient", "thanks", now)
        add(conn, conversation_id, "assistant", "bye", now, intent="other", outcome="answered")

        # Two graph turns. The emergency, the "still waiting" answer and an assistant-only message
        # (a resumed approval) are not graph turns, and another conversation is not counted.
        assert messages_repository.count_graph_turns(conn, conversation_id) == 2
        assert messages_repository.count_graph_turns(conn, uuid4()) == 0


def test_missing_checkpoint_tables_are_a_json_503_and_store_nothing(
    make_settings: Callable[..., Settings],
    empty_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The api started while the database was down: the app tables exist, but the checkpoint
    tables are still being created by the background retry."""
    monkeypatch.setattr(app_module, "prepare_database", lambda settings, clock: True)
    with psycopg.connect(empty_database_url, autocommit=True, connect_timeout=5) as conn:
        apply_schema(conn)
    settings = make_settings(database_url=empty_database_url)

    with TestClient(create_app(settings)) as client:
        conversation_id = _start(client)
        with caplog.at_level(logging.ERROR, logger="clinic.errors"):
            response = client.post(
                f"/api/v1/conversations/{conversation_id}/messages", json={"text": "hello"}
            )
        stored = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]

    assert response.status_code == 503  # not a 500
    assert response.json()["code"] == "database_unavailable"
    assert [r.getMessage() for r in caplog.records if r.name == "clinic.errors"] == [
        "database_not_ready"
    ]
    assert stored == []

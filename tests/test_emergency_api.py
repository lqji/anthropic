import json
import logging
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clinic import app as app_module
from clinic.app import create_app
from clinic.config import Settings
from clinic.errors import LLMUnavailable
from clinic.llm import ClassifyResult, ScriptedFakeLLM
from clinic.repositories import emergencies as emergencies_repository
from clinic.repositories import messages as messages_repository
from tests.test_conversation_api import FakeWorkflow

EMERGENCY_NUMBER = "1234"  # not the default, so the reply must come from settings
CHEST_PAIN = "I have crushing chest pain"
SAMPLES = Path(__file__).resolve().parents[1] / "seed" / "sample_messages.json"
EMERGENCY_SAMPLES = [
    sample for sample in json.loads(SAMPLES.read_text()) if sample["intent"] == "emergency"
]


@pytest.fixture
def workflow() -> FakeWorkflow:
    return FakeWorkflow()


@pytest.fixture
def client(
    make_settings: Callable[..., Settings], database_url: str, workflow: FakeWorkflow
) -> Iterator[TestClient]:
    settings = make_settings(database_url=database_url, emergency_number=EMERGENCY_NUMBER)
    with TestClient(create_app(settings, workflow=workflow)) as test_client:
        with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as conn:
            conn.execute("TRUNCATE conversations CASCADE")  # cascades to messages, emergencies
        yield test_client


def _start(client: TestClient) -> str:
    response = client.post("/api/v1/conversations", json={"patient_ref": "demo-patient-1"})
    assert response.status_code == 201
    return str(response.json()["id"])


def _url(conversation_id: str) -> str:
    return f"/api/v1/conversations/{conversation_id}/messages"


def test_chest_pain_gets_emergency_instruction_without_the_workflow(
    client: TestClient, workflow: FakeWorkflow
) -> None:
    conversation_id = _start(client)

    response = client.post(_url(conversation_id), json={"text": CHEST_PAIN})

    assert response.status_code == 200
    body = response.json()
    assert EMERGENCY_NUMBER in body["reply"]
    assert body["outcome"] == "emergency"
    assert body["intent"] == "emergency"
    assert body["status"] == "active"
    # The workflow is the only path to the LLM: zero workflow calls means zero LLM calls.
    assert workflow.calls == []
    messages = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]
    assert [(m["role"], m["outcome"]) for m in messages] == [
        ("patient", None),
        ("assistant", "emergency"),
    ]


def test_emergency_is_listed_for_staff_with_rule_and_excerpt(client: TestClient) -> None:
    conversation_id = _start(client)
    client.post(_url(conversation_id), json={"text": CHEST_PAIN})

    response = client.get("/api/v1/emergencies", params={"status": "open"})

    assert response.status_code == 200
    [flag] = response.json()
    assert flag["conversation_id"] == conversation_id
    assert flag["matched_rule"] == "chest_pain"
    assert flag["excerpt"] == CHEST_PAIN
    assert flag["status"] == "open"


@pytest.mark.parametrize(
    "sample", EMERGENCY_SAMPLES, ids=[sample["text"] for sample in EMERGENCY_SAMPLES]
)
def test_emergency_sample_messages_get_the_emergency_outcome(
    client: TestClient, workflow: FakeWorkflow, sample: dict[str, str]
) -> None:
    conversation_id = _start(client)

    response = client.post(_url(conversation_id), json={"text": sample["text"]})

    assert response.status_code == 200
    body = response.json()
    assert (body["intent"], body["outcome"]) == (sample["intent"], sample["outcome"])
    assert workflow.calls == []  # no workflow, so no model call
    [flag] = client.get("/api/v1/emergencies", params={"status": "open"}).json()
    assert flag["matched_rule"] == sample["rule"]


def test_long_emergency_message_excerpt_is_200_chars(client: TestClient) -> None:
    conversation_id = _start(client)
    text = "I can't breathe. " + "x" * 300

    client.post(_url(conversation_id), json={"text": text})

    [flag] = client.get("/api/v1/emergencies", params={"status": "open"}).json()
    assert flag["matched_rule"] == "breathing"
    assert flag["excerpt"] == text[:200]


def test_status_filter(client: TestClient) -> None:
    client.post(_url(_start(client)), json={"text": CHEST_PAIN})

    acknowledged = client.get("/api/v1/emergencies", params={"status": "acknowledged"})
    default = client.get("/api/v1/emergencies")
    bad = client.get("/api/v1/emergencies", params={"status": "closed"})

    assert acknowledged.json() == []
    assert len(default.json()) == 1  # status defaults to open
    assert bad.status_code == 422


def test_emergency_still_answered_when_the_llm_is_down(
    client: TestClient, workflow: FakeWorkflow
) -> None:
    conversation_id = _start(client)
    workflow.error = LLMUnavailable("model timed out")

    routine = client.post(_url(conversation_id), json={"text": "What are your opening hours?"})
    emergency = client.post(_url(conversation_id), json={"text": CHEST_PAIN})

    assert routine.status_code == 503
    assert emergency.status_code == 200
    assert EMERGENCY_NUMBER in emergency.json()["reply"]


def test_emergency_answered_at_once_while_another_turn_holds_the_lock(
    client: TestClient, workflow: FakeWorkflow
) -> None:
    conversation_id = _start(client)
    workflow.gate = threading.Event()

    with ThreadPoolExecutor(max_workers=1) as pool:
        routine = pool.submit(client.post, _url(conversation_id), json={"text": "hello"})
        assert workflow.entered.wait(5)  # the routine turn now holds the lock
        started = time.perf_counter()
        emergency = client.post(_url(conversation_id), json={"text": CHEST_PAIN})
        elapsed = time.perf_counter() - started
        workflow.gate.set()
        routine_response = routine.result(timeout=10)

    assert emergency.status_code == 200
    assert emergency.json()["outcome"] == "emergency"
    assert elapsed < 1
    assert routine_response.status_code == 200
    assert workflow.calls == [(conversation_id, "hello")]
    messages = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]
    # The emergency was answered first, so it is stored first.
    assert [m["text"] for m in messages][0] == CHEST_PAIN
    assert [m["text"] for m in messages][2:] == ["hello", "echo: hello"]


def test_routine_message_still_goes_to_the_workflow(
    client: TestClient, workflow: FakeWorkflow
) -> None:
    conversation_id = _start(client)

    response = client.post(_url(conversation_id), json={"text": "I need a blood test"})

    assert response.json()["outcome"] == "answered"
    assert workflow.calls == [(conversation_id, "I need a blood test")]
    assert client.get("/api/v1/emergencies").json() == []


def test_emergency_on_unknown_conversation_is_404(client: TestClient) -> None:
    response = client.post(_url(str(uuid4())), json={"text": CHEST_PAIN})

    assert response.status_code == 404
    assert client.get("/api/v1/emergencies").json() == []


def test_a_saved_emergency_says_staff_were_alerted(client: TestClient) -> None:
    conversation_id = _start(client)

    reply = client.post(_url(conversation_id), json={"text": CHEST_PAIN}).json()["reply"]

    assert "front-desk staff have been alerted" in reply


def test_an_emergency_cannot_land_between_a_routine_message_and_its_reply(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    """The routine turn stores its two messages in one transaction; the emergency waits for it
    (both lock the conversation row), so the stored order pairs each patient message with its
    own reply. `count_graph_turns` relies on that pairing."""
    conversation_id = _start(client)
    original_add = messages_repository.add
    first_stored = threading.Event()
    go_on = threading.Event()

    def slow_add(conn: Any, cid: Any, role: str, text: str, *args: Any, **kwargs: Any) -> Any:
        result = original_add(conn, cid, role, text, *args, **kwargs)
        if text == "hello":  # the routine patient message is written, its reply is not yet
            first_stored.set()
            go_on.wait(10)
        return result

    monkeypatch.setattr(messages_repository, "add", slow_add)
    with ThreadPoolExecutor(max_workers=2) as pool:
        routine = pool.submit(client.post, _url(conversation_id), json={"text": "hello"})
        assert first_stored.wait(5)
        emergency = pool.submit(client.post, _url(conversation_id), json={"text": CHEST_PAIN})
        time.sleep(0.5)  # long enough for the emergency to run, if nothing made it wait
        assert not emergency.done()  # it waits for the routine transaction
        go_on.set()
        assert routine.result(timeout=10).status_code == 200
        assert emergency.result(timeout=10).status_code == 200
    monkeypatch.undo()

    messages = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]
    assert [m["text"] for m in messages][:2] == ["hello", "echo: hello"]
    assert messages[2]["text"] == CHEST_PAIN
    assert messages[3]["outcome"] == "emergency"
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        assert messages_repository.count_graph_turns(conn, UUID(conversation_id)) == 1


def test_concurrent_emergency_and_routine_saves_keep_turn_numbers_right(
    client: TestClient, database_url: str
) -> None:
    """Routine messages and emergencies race. Two routine messages at once may get 409 (one turn
    at a time), emergencies always get 200. Each stored message sits next to its own reply, so
    every accepted turn is counted once and emergencies take no turn number."""
    conversation_id = _start(client)
    routine_count, emergency_count = 4, 4
    barrier = threading.Barrier(routine_count + emergency_count)

    def send(text: str) -> int:
        barrier.wait(10)
        return client.post(_url(conversation_id), json={"text": text}).status_code

    with ThreadPoolExecutor(max_workers=routine_count + emergency_count) as pool:
        futures = [pool.submit(send, f"hello {n}") for n in range(routine_count)]
        futures += [pool.submit(send, CHEST_PAIN) for _ in range(emergency_count)]
        statuses = [f.result(timeout=30) for f in futures]

    routine_statuses, emergency_statuses = statuses[:routine_count], statuses[routine_count:]
    assert emergency_statuses == [200] * emergency_count
    assert set(routine_statuses) <= {200, 409} and 200 in routine_statuses
    accepted = routine_statuses.count(200)
    messages = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]
    assert len(messages) == 2 * (accepted + emergency_count)
    for index in range(0, len(messages), 2):  # each patient message is followed by its own reply
        patient, reply = messages[index], messages[index + 1]
        assert patient["role"] == "patient" and reply["role"] == "assistant"
        if patient["text"] == CHEST_PAIN:
            assert reply["outcome"] == "emergency"
        else:
            assert reply["text"] == f"echo: {patient['text']}"
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        count = messages_repository.count_graph_turns(conn, UUID(conversation_id))
    assert count == accepted


# --- Persistence failures: the patient still gets the instruction ---------------------------


def _persist_failures(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == "emergency.persist_failed"]


def test_emergency_still_answered_when_the_database_is_down(
    make_settings: Callable[..., Settings], caplog: pytest.LogCaptureFixture
) -> None:
    workflow = FakeWorkflow()
    # make_settings defaults to an unreachable database; a 1 s pool timeout keeps the test short.
    settings = make_settings(db_pool_timeout_s=1, emergency_number=EMERGENCY_NUMBER)
    url = f"/api/v1/conversations/{uuid4()}/messages"

    with TestClient(create_app(settings, workflow=workflow)) as client:
        with caplog.at_level(logging.WARNING):
            emergency = client.post(url, json={"text": CHEST_PAIN})
            routine = client.post(url, json={"text": "What are your opening hours?"})

    assert emergency.status_code == 200
    body = emergency.json()
    assert EMERGENCY_NUMBER in body["reply"]
    # Nothing was saved, so the reply must not claim staff were alerted.
    assert "alerted" not in body["reply"]
    assert "phone the clinic directly" in body["reply"]
    assert body["outcome"] == "emergency"
    assert workflow.calls == []
    assert routine.status_code == 503  # nothing else works without the database
    [failure] = _persist_failures(caplog)
    assert failure.levelno == logging.ERROR
    assert failure.__dict__["fields"]["matched_rule"] == "chest_pain"
    # The rule name only: patient text never goes to the logs.
    assert all(CHEST_PAIN not in str(r.__dict__) for r in caplog.records)


def test_emergency_still_answered_when_saving_fails_and_nothing_is_half_saved(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    conversation_id = _start(client)

    def broken_create(*args: object, **kwargs: object) -> None:
        raise psycopg.OperationalError("server closed the connection unexpectedly")

    monkeypatch.setattr(emergencies_repository, "create", broken_create)
    with caplog.at_level(logging.WARNING):
        response = client.post(_url(conversation_id), json={"text": CHEST_PAIN})
    monkeypatch.undo()

    assert response.status_code == 200
    reply = response.json()["reply"]
    assert EMERGENCY_NUMBER in reply
    assert "alerted" not in reply and "phone the clinic directly" in reply
    assert len(_persist_failures(caplog)) == 1
    # One transaction: the patient message written before the failure was rolled back.
    assert client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"] == []
    assert client.get("/api/v1/emergencies").json() == []


def test_an_emergency_never_reaches_the_graph_or_the_llm(
    make_settings: Callable[..., Settings],
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On the real graph (the default workflow), with an LLM that records every call."""
    llm = ScriptedFakeLLM(classify=[ClassifyResult(intent="other")])
    monkeypatch.setattr(app_module, "make_llm", lambda settings: llm)
    settings = make_settings(database_url=database_url, emergency_number=EMERGENCY_NUMBER)

    with TestClient(create_app(settings)) as client:
        app = client.app
        assert isinstance(app, FastAPI)
        graph: Any = app.state.deps.graph
        conversation_id = _start(client)
        thread = {"configurable": {"thread_id": conversation_id}}

        emergency = client.post(_url(conversation_id), json={"text": CHEST_PAIN})

        assert emergency.status_code == 200
        assert emergency.json()["outcome"] == "emergency"
        assert EMERGENCY_NUMBER in emergency.json()["reply"]
        assert llm.classify_calls == [] and llm.answer_calls == []  # zero LLM calls
        assert not graph.get_state(thread).values  # the graph never ran

        routine = client.post(_url(conversation_id), json={"text": "hello"})

        assert routine.status_code == 200
        assert len(llm.classify_calls) == 1  # a routine message does reach the LLM
        messages = graph.get_state(thread).values["messages"]
        # The emergency did not take a turn number: the routine message is graph turn 1.
        assert [m.id for m in messages] == [
            f"{conversation_id}:1:patient",
            f"{conversation_id}:1:reply",
        ]

"""CFD-14 on the real conversation graph: "Cancel my appointment tomorrow" in a chat message,
the approval, the restart and the staff decision. A fresh seeded database, the fake LLM (no API
key), a fixed clock; the graph, PostgresSaver and bookings are the real ones."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clinic.app import create_app, prepare_database
from clinic.clock import FixedClock
from clinic.config import Settings
from tests.conftest import temporary_database

# Monday 2026-11-02, 12:00 at the clinic (Asia/Muscat, UTC+4): "tomorrow" is 2026-11-03.
NOW = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
TOMORROW = date(2026, 11, 3)
CANCEL = "Cancel my appointment tomorrow"
PATIENT = "demo-patient-1"
DECISION = {"decided_by": "desk-1", "reason": "Patient called to confirm"}


class Chat:
    def __init__(self, client: TestClient, url: str) -> None:
        self.client = client
        self.url = url

    def start(self, patient_ref: str = PATIENT) -> str:
        response = self.client.post("/api/v1/conversations", json={"patient_ref": patient_ref})
        assert response.status_code == 201
        return str(response.json()["id"])

    def say(self, conversation_id: str, text: str) -> dict[str, Any]:
        response = self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages", json={"text": text}
        )
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    def messages(self, conversation_id: str) -> list[dict[str, Any]]:
        detail = self.client.get(f"/api/v1/conversations/{conversation_id}").json()
        messages: list[dict[str, Any]] = detail["messages"]
        return messages

    def pending(self) -> list[dict[str, Any]]:
        pending: list[dict[str, Any]] = self.client.get("/api/v1/approvals").json()
        return pending

    def decide(self, approval_id: str, verb: str = "approve") -> Any:
        return self.client.post(f"/api/v1/approvals/{approval_id}/{verb}", json=DECISION)

    def book(self, conversation_id: str, day: date = TOMORROW, nth: int = 0) -> tuple[int, int]:
        """A confirmed booking of Dr. Salma's `nth` slot that day for this conversation's
        patient. Returns (booking id, slot id)."""
        with psycopg.connect(self.url, autocommit=True) as conn:
            slot = conn.execute(
                """
                SELECT s.id FROM slots s JOIN doctors d ON d.id = s.doctor_id
                WHERE d.name = 'Dr. Salma Nasser'
                  AND (s.starts_at AT TIME ZONE 'Asia/Muscat')::date = %s
                ORDER BY s.starts_at OFFSET %s LIMIT 1
                """,
                (day, nth),
            ).fetchone()
            assert slot is not None
            conn.execute("UPDATE slots SET status = 'booked' WHERE id = %s", (slot[0],))
            booking = conn.execute(
                """
                INSERT INTO bookings (slot_id, conversation_id, status, created_at, updated_at)
                VALUES (%s, %s, 'confirmed', %s, %s) RETURNING id
                """,
                (slot[0], UUID(conversation_id), NOW, NOW),
            ).fetchone()
            assert booking is not None
            return booking[0], slot[0]

    def status_of(self, table: str, row_id: int) -> str:
        with psycopg.connect(self.url, autocommit=True) as conn:
            row = conn.execute(
                f"SELECT status FROM {table} WHERE id = %s",  # noqa: S608 - fixed table names
                (row_id,),
            ).fetchone()
        assert row is not None
        return str(row[0])

    def paused_at(self, conversation_id: str) -> tuple[str, ...]:
        app = self.client.app
        assert isinstance(app, FastAPI)
        graph: Any = app.state.deps.graph
        config = {"configurable": {"thread_id": conversation_id}}
        return tuple(graph.get_state(config).next)


@pytest.fixture
def url(database_url: str) -> Iterator[str]:
    with temporary_database(database_url) as temp_url:
        settings = Settings(llm_provider="fake", database_url=temp_url)
        assert prepare_database(settings, FixedClock(NOW))  # tables, catalogue, slots
        yield temp_url


@contextmanager
def running(url: str) -> Iterator[Chat]:
    """One api process on this database. Leaving the block stops it (a restart)."""
    settings = Settings(llm_provider="fake", database_url=url)
    with TestClient(create_app(settings, clock=FixedClock(NOW))) as client:
        yield Chat(client, url)


@pytest.fixture
def chat(url: str) -> Iterator[Chat]:
    with running(url) as instance:
        yield instance


def _request_cancellation(chat: Chat) -> tuple[str, int, int, str]:
    """A patient with a booking tomorrow asks to cancel: (conversation, booking, slot, approval)."""
    conversation_id = chat.start()
    booking_id, slot_id = chat.book(conversation_id)
    body = chat.say(conversation_id, CANCEL)
    return conversation_id, booking_id, slot_id, body["approval_id"]


# --- The chat message pauses the graph for staff ----------------------------------------------


def test_a_cancel_message_waits_for_staff_on_the_real_graph(chat: Chat) -> None:
    conversation_id = chat.start()
    booking_id, _ = chat.book(conversation_id)

    body = chat.say(conversation_id, CANCEL)

    assert body["status"] == "awaiting_approval"
    assert body["outcome"] == "awaiting_approval"
    assert UUID(body["approval_id"])
    assert "Tue 3 Nov at 09:00 with Dr. Salma Nasser" in body["reply"]
    [approval] = chat.pending()
    assert approval["id"] == body["approval_id"]
    assert (approval["kind"], approval["booking_id"]) == ("cancellation", booking_id)
    assert chat.paused_at(conversation_id) == ("await_approval",)  # a real interrupt()
    assert chat.status_of("bookings", booking_id) == "confirmed"  # nothing cancelled yet
    detail = chat.client.get(f"/api/v1/conversations/{conversation_id}").json()
    assert detail["status"] == "awaiting_approval"


def test_approving_cancels_the_booking_frees_the_slot_and_tells_the_patient(chat: Chat) -> None:
    conversation_id, booking_id, slot_id, approval_id = _request_cancellation(chat)

    response = chat.decide(approval_id)

    assert response.status_code == 200
    assert chat.status_of("bookings", booking_id) == "cancelled"
    assert chat.status_of("slots", slot_id) == "free"
    assert chat.paused_at(conversation_id) == ()
    assert "has been cancelled" in response.json()["patient_message"]
    last = chat.messages(conversation_id)[-1]
    assert last["role"] == "assistant" and last["text"] == response.json()["patient_message"]
    assert chat.client.get(f"/api/v1/conversations/{conversation_id}").json()["status"] == "active"


def test_rejecting_keeps_the_booking_and_tells_the_patient_the_reason(chat: Chat) -> None:
    conversation_id, booking_id, slot_id, approval_id = _request_cancellation(chat)

    response = chat.decide(approval_id, "reject")

    assert response.status_code == 200
    assert chat.status_of("bookings", booking_id) == "confirmed"
    assert chat.status_of("slots", slot_id) == "booked"
    assert DECISION["reason"] in response.json()["patient_message"]
    assert "still booked" in response.json()["patient_message"]


def test_the_patient_can_chat_again_after_the_decision(chat: Chat) -> None:
    conversation_id, _, _, approval_id = _request_cancellation(chat)
    chat.decide(approval_id)

    body = chat.say(conversation_id, "What are your opening hours?")

    assert body["outcome"] != "still_waiting"
    assert body["status"] == "active"


# --- The pause survives a restart ---------------------------------------------------------------


def test_the_approval_survives_a_restart_and_approving_resumes_the_same_workflow(url: str) -> None:
    with running(url) as first:
        conversation_id, booking_id, _, approval_id = _request_cancellation(first)

    with running(url) as second:  # a new process: new pools, new graph, new checkpointer
        assert [a["id"] for a in second.pending()] == [approval_id]
        assert second.paused_at(conversation_id) == ("await_approval",)

        response = second.decide(approval_id)

        assert response.status_code == 200
        assert second.status_of("bookings", booking_id) == "cancelled"
        assert second.paused_at(conversation_id) == ()


# --- While paused -------------------------------------------------------------------------------


def test_while_paused_a_routine_message_gets_still_waiting(chat: Chat) -> None:
    conversation_id, booking_id, _, _ = _request_cancellation(chat)

    body = chat.say(conversation_id, "What are your opening hours?")

    assert body["outcome"] == "still_waiting"
    assert body["status"] == "awaiting_approval"
    assert chat.paused_at(conversation_id) == ("await_approval",)  # the graph did not run
    assert len(chat.pending()) == 1


def test_while_paused_an_emergency_is_still_answered(chat: Chat) -> None:
    conversation_id, *_ = _request_cancellation(chat)

    body = chat.say(conversation_id, "I have crushing chest pain")

    assert body["outcome"] == "emergency"
    assert chat.paused_at(conversation_id) == ("await_approval",)
    assert len(chat.client.get("/api/v1/emergencies").json()) == 1


# --- No booking, two bookings, already waiting ----------------------------------------------------


def test_no_matching_booking_is_answered_without_asking_staff(chat: Chat) -> None:
    conversation_id = chat.start()

    body = chat.say(conversation_id, CANCEL)

    assert body["outcome"] == "cancel_not_found"
    assert body["status"] == "active"
    assert body.get("approval_id") is None
    assert chat.pending() == []
    assert chat.paused_at(conversation_id) == ()


def test_two_matching_bookings_ask_which_one(chat: Chat) -> None:
    conversation_id = chat.start()
    chat.book(conversation_id, nth=0)
    chat.book(conversation_id, nth=1)

    body = chat.say(conversation_id, CANCEL)

    assert body["outcome"] == "cancel_ambiguous"
    assert "Which one" in body["reply"]
    assert chat.pending() == []


def test_a_booking_already_waiting_for_approval_says_so(chat: Chat) -> None:
    conversation_id, *_ = _request_cancellation(chat)
    other = chat.start()  # the same patient, in another conversation

    body = chat.say(other, CANCEL)

    assert body["outcome"] == "already_pending"
    assert len(chat.pending()) == 1
    assert chat.paused_at(other) == ()
    assert conversation_id != other


def test_a_cancel_message_from_another_patient_never_finds_my_booking(chat: Chat) -> None:
    mine = chat.start()
    chat.book(mine)
    stranger = chat.start(patient_ref=f"stranger-{uuid4().hex[:6]}")

    body = chat.say(stranger, CANCEL)

    assert body["outcome"] == "cancel_not_found"


def test_other_messages_are_unaffected(chat: Chat) -> None:
    conversation_id = chat.start()

    body = chat.say(conversation_id, "What are your opening hours?")

    assert body["status"] == "active"
    assert body.get("approval_id") is None

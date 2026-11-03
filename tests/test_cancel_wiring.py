"""CFD-14 wired into the real graph: cancel, wait for staff, decide, on real PostgreSQL.

`FakeLLM` classifies the messages. The bookings are real rows made with `bookings.book_slot`.
"""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from clinic.app import create_app, prepare_database
from clinic.clock import FixedClock
from clinic.config import Settings
from clinic.repositories import bookings
from clinic.repositories.bookings import SlotUnavailable
from tests.conftest import temporary_database

NOW = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
PATIENT = "demo-patient-1"
DECISION = {"decided_by": "desk-1", "reason": "Patient called to confirm"}


@pytest.fixture
def settings(make_settings: Callable[..., Settings], database_url: str) -> Iterator[Settings]:
    with temporary_database(database_url) as url:
        settings = make_settings(database_url=url)
        assert prepare_database(settings, FixedClock(NOW))
        yield settings


def _free_slots(settings: Settings, count: int) -> list[int]:
    with psycopg.connect(settings.database_url) as conn:
        rows = conn.execute(
            "SELECT id FROM slots WHERE status = 'free' AND starts_at > %s"
            " ORDER BY starts_at LIMIT %s",
            (datetime(2026, 11, 4, tzinfo=UTC), count),
        ).fetchall()
    return [row[0] for row in rows]


def _book(settings: Settings, slot_id: int, conversation_id: str) -> int:
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        return bookings.book_slot(
            conn, slot_id=slot_id, conversation_id=UUID(conversation_id), now=NOW
        ).id


def _status(settings: Settings, table: str, row_id: int) -> str:
    with psycopg.connect(settings.database_url) as conn:
        row = conn.execute(f"SELECT status FROM {table} WHERE id = %s", (row_id,)).fetchone()  # noqa: S608
    assert row is not None
    return str(row[0])


def _start(client: TestClient) -> str:
    return str(client.post("/api/v1/conversations", json={"patient_ref": PATIENT}).json()["id"])


def _say(client: TestClient, conversation_id: str, text: str) -> dict[str, object]:
    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": text})
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def test_cancel_waits_for_staff_then_approval_cancels_and_frees_the_slot(
    settings: Settings,
) -> None:
    [slot_id] = _free_slots(settings, 1)
    with TestClient(create_app(settings, clock=FixedClock(NOW))) as client:
        conversation_id = _start(client)
        booking_id = _book(settings, slot_id, conversation_id)

        body = _say(client, conversation_id, "I need to cancel my appointment")

        assert body["status"] == "awaiting_approval"
        approval_id = str(body["approval_id"])
        assert UUID(approval_id)
        assert _status(settings, "bookings", booking_id) == "confirmed"
        assert _status(settings, "slots", slot_id) == "booked"
        [pending] = client.get("/api/v1/approvals?status=pending").json()
        assert (pending["id"], pending["kind"]) == (approval_id, "cancellation")

        decided = client.post(f"/api/v1/approvals/{approval_id}/approve", json=DECISION)

        assert decided.status_code == 200, decided.text
        assert "has been cancelled" in decided.json()["patient_message"]
        assert _status(settings, "bookings", booking_id) == "cancelled"
        assert _status(settings, "slots", slot_id) == "free"
        detail = client.get(f"/api/v1/conversations/{conversation_id}").json()
        assert detail["status"] == "active"
        assert detail["messages"][-1]["text"] == decided.json()["patient_message"]


def test_rejection_keeps_the_booking_and_gives_the_reason(settings: Settings) -> None:
    [slot_id] = _free_slots(settings, 1)
    with TestClient(create_app(settings, clock=FixedClock(NOW))) as client:
        conversation_id = _start(client)
        booking_id = _book(settings, slot_id, conversation_id)
        approval_id = str(_say(client, conversation_id, "Cancel my appointment")["approval_id"])

        decided = client.post(f"/api/v1/approvals/{approval_id}/reject", json=DECISION)

        assert decided.status_code == 200, decided.text
        assert DECISION["reason"] in decided.json()["patient_message"]
        assert _status(settings, "bookings", booking_id) == "confirmed"
        assert _status(settings, "slots", slot_id) == "booked"


def test_a_patient_without_a_booking_gets_cancel_not_found_and_no_approval(
    settings: Settings,
) -> None:
    with TestClient(create_app(settings, clock=FixedClock(NOW))) as client:
        conversation_id = _start(client)

        body = _say(client, conversation_id, "Cancel my appointment")

        assert body["outcome"] == "cancel_not_found"
        assert body["status"] == "active"
        assert body.get("approval_id") is None
        assert client.get("/api/v1/approvals?status=pending").json() == []


def test_two_bookings_ask_which_one(settings: Settings) -> None:
    first, second = _free_slots(settings, 2)
    with TestClient(create_app(settings, clock=FixedClock(NOW))) as client:
        conversation_id = _start(client)
        _book(settings, first, conversation_id)
        _book(settings, second, conversation_id)

        body = _say(client, conversation_id, "Cancel my appointment")

        assert body["outcome"] == "cancel_ambiguous"
        assert "Which one" in str(body["reply"])


def test_cancelling_twice_never_frees_a_slot_someone_else_booked(settings: Settings) -> None:
    [slot_id] = _free_slots(settings, 1)
    with TestClient(create_app(settings, clock=FixedClock(NOW))):  # creates the conversations
        pass
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        ids = [uuid4(), uuid4()]
        for conversation_id in ids:
            conn.execute(
                "INSERT INTO conversations (id, patient_ref, created_at) VALUES (%s, %s, %s)",
                (conversation_id, PATIENT, NOW),
            )
        first = bookings.book_slot(conn, slot_id=slot_id, conversation_id=ids[0], now=NOW)
        assert bookings.cancel(conn, first.id, NOW) is True
        second = bookings.book_slot(conn, slot_id=slot_id, conversation_id=ids[1], now=NOW)

        assert bookings.cancel(conn, first.id, NOW) is False  # a replayed decision

        assert _status(settings, "slots", slot_id) == "booked"
        assert _status(settings, "bookings", second.id) == "confirmed"
        with pytest.raises(SlotUnavailable):
            bookings.book_slot(conn, slot_id=slot_id, conversation_id=uuid4(), now=NOW)

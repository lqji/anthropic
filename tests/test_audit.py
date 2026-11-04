"""CFD-30: the audit list. Every booking and approval action writes exactly one append-only row, in
the same transaction as the change it describes; `GET /api/v1/audit` reads them newest first.

Repository tests run on a freshly seeded real PostgreSQL with a fixed clock (Monday 2026-11-02,
12:00 clinic time). The API tests run the whole conversation graph."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import Connection
from psycopg.rows import TupleRow

from clinic.repositories import approvals, audit, bookings
from tests.chat_harness import DECISION, NOW, Chat

LATER = NOW + timedelta(hours=1)
Row = tuple[Any, ...]


@pytest.fixture
def conn(url: str) -> Iterator[Connection[TupleRow]]:
    with psycopg.connect(url, autocommit=True) as connection:
        yield connection


def _conversation(conn: Connection[TupleRow]) -> UUID:
    conversation_id = uuid4()
    conn.execute(
        "INSERT INTO conversations (id, patient_ref, created_at) VALUES (%s, 'p-1', %s)",
        (conversation_id, NOW),
    )
    return conversation_id


def _free_slot(conn: Connection[TupleRow], nth: int = 0) -> int:
    row = conn.execute(
        "SELECT id FROM slots WHERE status = 'free' AND starts_at > %s ORDER BY starts_at, id"
        " OFFSET %s LIMIT 1",
        (NOW + timedelta(days=1), nth),
    ).fetchone()
    assert row is not None
    return int(row[0])


def _events(conn: Connection[TupleRow]) -> list[Row]:
    """Rows as (action, actor_type, actor, outcome, booking, conversation, reason, at)."""
    return conn.execute(
        "SELECT action, actor_type, actor, outcome, booking_id, conversation_id, reason, at"
        " FROM audit_events ORDER BY id"
    ).fetchall()


def _confirmed_booking(conn: Connection[TupleRow], conversation_id: UUID) -> int:
    return bookings.book_slot(
        conn, slot_id=_free_slot(conn), conversation_id=conversation_id, now=NOW
    ).id


def _held_booking(conn: Connection[TupleRow], conversation_id: UUID) -> bookings.HeldBooking:
    return bookings.hold_same_day(
        conn,
        slot_id=_free_slot(conn),
        conversation_id=conversation_id,
        approval_id=uuid4(),
        now=NOW,
    )


# --- Each action writes exactly one row --------------------------------------------------------


def test_booking_a_slot_writes_one_row_for_the_patient(conn: Connection[TupleRow]) -> None:
    conversation_id = _conversation(conn)

    booking = bookings.book_slot(
        conn, slot_id=_free_slot(conn), conversation_id=conversation_id, now=NOW
    )

    assert _events(conn) == [
        (
            "booking_created",
            "patient",
            str(conversation_id),
            "confirmed",
            booking.id,
            conversation_id,
            None,
            NOW,
        )
    ]


def test_a_replayed_booking_writes_no_second_row(conn: Connection[TupleRow]) -> None:
    conversation_id = _conversation(conn)
    slot_id = _free_slot(conn)
    bookings.book_slot(conn, slot_id=slot_id, conversation_id=conversation_id, now=NOW)

    bookings.book_slot(conn, slot_id=slot_id, conversation_id=conversation_id, now=LATER)

    assert [event[0] for event in _events(conn)] == ["booking_created"]


def test_a_taken_slot_writes_no_row(conn: Connection[TupleRow]) -> None:
    slot_id = _free_slot(conn)
    bookings.book_slot(conn, slot_id=slot_id, conversation_id=_conversation(conn), now=NOW)

    with pytest.raises(bookings.SlotUnavailable):
        bookings.book_slot(conn, slot_id=slot_id, conversation_id=_conversation(conn), now=NOW)

    assert len(_events(conn)) == 1


def test_holding_a_same_day_slot_writes_one_row(conn: Connection[TupleRow]) -> None:
    conversation_id = _conversation(conn)

    held = _held_booking(conn, conversation_id)

    assert _events(conn) == [
        (
            "same_day_requested",
            "patient",
            str(conversation_id),
            "pending_approval",
            held.booking_id,
            conversation_id,
            None,
            NOW,
        )
    ]


def test_a_replayed_hold_writes_no_second_row(conn: Connection[TupleRow]) -> None:
    conversation_id = _conversation(conn)
    slot_id = _free_slot(conn)
    kwargs: dict[str, Any] = {"slot_id": slot_id, "conversation_id": conversation_id}
    bookings.hold_same_day(conn, approval_id=uuid4(), now=NOW, **kwargs)

    bookings.hold_same_day(conn, approval_id=uuid4(), now=LATER, **kwargs)

    assert [event[0] for event in _events(conn)] == ["same_day_requested"]


def test_requesting_a_cancellation_writes_one_row(conn: Connection[TupleRow]) -> None:
    conversation_id = _conversation(conn)
    booking_id = _confirmed_booking(conn, conversation_id)

    approvals.create(conn, uuid4(), conversation_id, "cancellation", booking_id, LATER)

    assert _events(conn)[1:] == [
        (
            "cancellation_requested",
            "patient",
            str(conversation_id),
            "pending_approval",
            booking_id,
            conversation_id,
            None,
            LATER,
        )
    ]


@pytest.mark.parametrize(
    ("status", "action"), [("approved", "approval_approved"), ("rejected", "approval_rejected")]
)
def test_a_staff_decision_writes_one_row_with_who_and_why(
    conn: Connection[TupleRow], status: str, action: str
) -> None:
    conversation_id = _conversation(conn)
    booking_id = _confirmed_booking(conn, conversation_id)
    approval_id = uuid4()
    approvals.create(conn, approval_id, conversation_id, "cancellation", booking_id, NOW)

    decided = approvals.decide(conn, approval_id, status, "desk-1", "Called the patient", LATER)

    assert decided is not None
    assert _events(conn)[2:] == [
        (
            action,
            "staff",
            "desk-1",
            status,
            booking_id,
            conversation_id,
            "Called the patient",
            LATER,
        )
    ]


def test_a_second_decision_writes_no_row(conn: Connection[TupleRow]) -> None:
    conversation_id = _conversation(conn)
    booking_id = _confirmed_booking(conn, conversation_id)
    approval_id = uuid4()
    approvals.create(conn, approval_id, conversation_id, "cancellation", booking_id, NOW)
    approvals.decide(conn, approval_id, "approved", "desk-1", "first", NOW)

    assert approvals.decide(conn, approval_id, "rejected", "desk-2", "second", LATER) is None

    assert [event[0] for event in _events(conn)[1:]] == [
        "cancellation_requested",
        "approval_approved",
    ]


def test_confirming_a_held_booking_writes_one_row(conn: Connection[TupleRow]) -> None:
    conversation_id = _conversation(conn)
    held = _held_booking(conn, conversation_id)

    assert bookings.confirm_held(conn, held.booking_id, LATER)
    assert not bookings.confirm_held(conn, held.booking_id, LATER)  # a repeat changes nothing

    assert _events(conn)[1:] == [
        (
            "booking_confirmed",
            "system",
            "workflow",
            "confirmed",
            held.booking_id,
            conversation_id,
            None,
            LATER,
        )
    ]


def test_rejecting_a_held_booking_writes_one_row(conn: Connection[TupleRow]) -> None:
    conversation_id = _conversation(conn)
    held = _held_booking(conn, conversation_id)

    assert bookings.reject_held(conn, held.booking_id, LATER)
    assert not bookings.reject_held(conn, held.booking_id, LATER)

    assert [event[:5] for event in _events(conn)[1:]] == [
        ("booking_rejected", "system", "workflow", "rejected", held.booking_id)
    ]


def test_cancelling_a_booking_writes_one_row(conn: Connection[TupleRow]) -> None:
    conversation_id = _conversation(conn)
    booking_id = _confirmed_booking(conn, conversation_id)

    assert bookings.cancel(conn, booking_id, LATER)
    assert not bookings.cancel(conn, booking_id, LATER)

    assert [event[:5] for event in _events(conn)[1:]] == [
        ("booking_cancelled", "system", "workflow", "cancelled", booking_id)
    ]


# --- Same transaction --------------------------------------------------------------------------


def test_a_rolled_back_booking_leaves_no_audit_row(url: str) -> None:
    with psycopg.connect(url) as conn:
        conversation_id = _conversation(conn)
        conn.commit()
        slot_id = _free_slot(conn)
        with pytest.raises(RuntimeError, match="boom"), conn.transaction():
            bookings.book_slot(conn, slot_id=slot_id, conversation_id=conversation_id, now=NOW)
            raise RuntimeError("boom")  # the caller's transaction fails after the booking

        assert conn.execute("SELECT count(*) FROM bookings").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM audit_events").fetchone() == (0,)


def test_a_rolled_back_decision_leaves_no_audit_row(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as setup:
        conversation_id = _conversation(setup)
        booking_id = _confirmed_booking(setup, conversation_id)
        approval_id = uuid4()
        approvals.create(setup, approval_id, conversation_id, "cancellation", booking_id, NOW)
    with psycopg.connect(url) as conn:
        with pytest.raises(RuntimeError, match="boom"), conn.transaction():
            approvals.decide(conn, approval_id, "approved", "desk-1", "ok", LATER)
            raise RuntimeError("boom")

        assert approvals.get(conn, approval_id) is not None
        assert [row[0] for row in _events(conn)] == ["booking_created", "cancellation_requested"]


def test_audit_rows_cannot_be_changed_or_deleted(conn: Connection[TupleRow]) -> None:
    bookings.book_slot(conn, slot_id=_free_slot(conn), conversation_id=_conversation(conn), now=NOW)

    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        conn.execute("UPDATE audit_events SET outcome = 'tampered'")
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        conn.execute("DELETE FROM audit_events")

    assert len(_events(conn)) == 1


# --- Listing -----------------------------------------------------------------------------------


def test_listing_an_empty_audit_is_an_empty_list(conn: Connection[TupleRow]) -> None:
    assert audit.list_events(conn, limit=10, offset=0) == []


def test_listing_is_newest_first_with_paging_and_filters(conn: Connection[TupleRow]) -> None:
    first, second = _conversation(conn), _conversation(conn)
    booking_a = _confirmed_booking(conn, first)
    held_b = _held_booking(conn, second)
    bookings.cancel(conn, booking_a, LATER)

    everything = audit.list_events(conn, limit=10, offset=0)
    assert [event.action for event in everything] == [
        "booking_cancelled",
        "same_day_requested",
        "booking_created",
    ]
    assert [event.action for event in audit.list_events(conn, limit=1, offset=1)] == [
        "same_day_requested"
    ]
    assert audit.list_events(conn, limit=10, offset=3) == []
    assert [
        e.action for e in audit.list_events(conn, limit=10, offset=0, booking_id=booking_a)
    ] == [
        "booking_cancelled",
        "booking_created",
    ]
    assert [
        e.booking_id for e in audit.list_events(conn, limit=10, offset=0, conversation_id=second)
    ] == [held_b.booking_id]
    assert (
        audit.list_events(conn, limit=10, offset=0, booking_id=booking_a, conversation_id=second)
        == []
    )


def test_events_at_the_same_time_keep_a_stable_newest_first_order(
    conn: Connection[TupleRow],
) -> None:
    conversation_id = _conversation(conn)
    for nth in range(3):
        bookings.book_slot(
            conn, slot_id=_free_slot(conn, nth), conversation_id=_conversation(conn), now=NOW
        )
    bookings.book_slot(conn, slot_id=_free_slot(conn, 3), conversation_id=conversation_id, now=NOW)

    ids = [event.id for event in audit.list_events(conn, limit=10, offset=0)]

    assert ids == sorted(ids, reverse=True)


# --- The API, through the real conversation graph ----------------------------------------------

CANCEL = "Cancel my appointment tomorrow"


def _audit(chat: Chat, **params: Any) -> list[dict[str, Any]]:
    response = chat.client.get("/api/v1/audit", params=params)
    assert response.status_code == 200, response.text
    body: list[dict[str, Any]] = response.json()
    return body


def test_the_api_lists_nothing_on_a_fresh_database(chat: Chat) -> None:
    assert _audit(chat) == []


def test_a_cancellation_through_the_chat_is_audited_end_to_end(chat: Chat) -> None:
    conversation_id = chat.start()
    booking_id, _ = chat.book(conversation_id)
    approval_id = chat.say(conversation_id, CANCEL)["approval_id"]
    assert chat.decide(approval_id).status_code == 200

    events = _audit(chat)

    assert [event["action"] for event in events] == [
        "booking_cancelled",
        "approval_approved",
        "cancellation_requested",
    ]
    assert {event["booking_id"] for event in events} == {booking_id}
    assert {event["conversation_id"] for event in events} == {conversation_id}
    by_action = {event["action"]: event for event in events}
    assert by_action["cancellation_requested"]["actor"] == conversation_id
    assert by_action["approval_approved"]["actor"] == DECISION["decided_by"]
    assert by_action["approval_approved"]["reason"] == DECISION["reason"]
    assert all(datetime.fromisoformat(event["at"]) == NOW for event in events)
    assert set(events[0]) == {
        "id",
        "at",
        "actor_type",
        "actor",
        "action",
        "outcome",
        "booking_id",
        "conversation_id",
        "reason",
    }


def test_a_rejected_same_day_booking_is_audited(chat: Chat) -> None:
    conversation_id = chat.start()
    chat.say(conversation_id, "When is Dr. Salma free today?")
    approval_id = chat.say(conversation_id, "Book the first one")["approval_id"]
    assert chat.decide(approval_id, "reject").status_code == 200

    events = _audit(chat, conversation_id=conversation_id)

    assert [(event["action"], event["outcome"]) for event in events] == [
        ("booking_rejected", "rejected"),
        ("approval_rejected", "rejected"),
        ("same_day_requested", "pending_approval"),
    ]


def test_the_audit_holds_no_patient_message_text(chat: Chat) -> None:
    conversation_id = chat.start("patient-with-a-name")
    chat.book(conversation_id)
    chat.say(conversation_id, CANCEL)

    text = chat.client.get("/api/v1/audit").text

    assert CANCEL not in text and "patient-with-a-name" not in text


def test_the_api_pages_and_filters(chat: Chat) -> None:
    first, second = chat.start(), chat.start("demo-patient-2")
    booking_one, _ = chat.book(first, nth=0)
    chat.book(second, nth=1)
    chat.say(first, CANCEL)
    chat.say(second, CANCEL)

    assert len(_audit(chat)) == 2
    assert len(_audit(chat, limit=1)) == 1
    assert _audit(chat, limit=1, offset=1)[0]["conversation_id"] == first
    assert [event["booking_id"] for event in _audit(chat, booking_id=booking_one)] == [booking_one]
    assert [event["conversation_id"] for event in _audit(chat, conversation_id=second)] == [second]
    assert _audit(chat, offset=2) == []


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 501},
        {"offset": -1},
        {"conversation_id": "nope"},
        {"booking_id": "x"},
    ],
)
def test_the_api_rejects_bad_parameters(chat: Chat, params: dict[str, Any]) -> None:
    response = chat.client.get("/api/v1/audit", params=params)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_the_time_of_an_event_is_the_injected_clocks_time(chat: Chat) -> None:
    conversation_id = chat.start()
    chat.book(conversation_id)
    chat.say(conversation_id, CANCEL)

    [event] = _audit(chat)

    assert datetime.fromisoformat(event["at"]).astimezone(UTC) == NOW

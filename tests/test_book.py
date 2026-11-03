"""Booking through the api and the real checkpointed graph, on a freshly seeded PostgreSQL.

FakeLLM classifies; the clock is fixed at Monday 2026-11-02, 12:00 at the clinic, so "tomorrow"
is a later day and "today" is a same-day booking (held for staff approval, CFD-15).
"""

from collections import Counter
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from clinic.app import create_app
from clinic.clock import FixedClock
from clinic.config import Settings
from clinic.repositories import messages as messages_repository
from tests.conftest import temporary_database

NOW = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
ASK_TOMORROW = "When is Dr. Salma free tomorrow?"
BOOK_FIRST = "Book the first one"


@pytest.fixture
def settings(make_settings: Callable[..., Settings], database_url: str) -> Iterator[Settings]:
    with temporary_database(database_url) as url:
        yield make_settings(database_url=url)


def _app(settings: Settings) -> TestClient:
    return TestClient(create_app(settings, clock=FixedClock(NOW)))


def _start(client: TestClient) -> str:
    response = client.post("/api/v1/conversations", json={"patient_ref": "demo-patient-1"})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _say(client: TestClient, conversation_id: str, text: str) -> dict[str, Any]:
    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": text})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _bookings(settings: Settings) -> list[tuple[str, str, str, str]]:
    """(conversation id, booking status, slot status, slot start in clinic time) per booking."""
    with psycopg.connect(settings.database_url) as conn:
        rows = conn.execute(
            """
            SELECT b.conversation_id::text, b.status, s.status,
                   to_char(s.starts_at AT TIME ZONE 'Asia/Muscat', 'YYYY-MM-DD HH24:MI')
            FROM bookings b JOIN slots s ON s.id = b.slot_id ORDER BY b.id
            """
        ).fetchall()
    return [(cid, booking, slot, start) for cid, booking, slot, start in rows]


def _slot_status(settings: Settings, local_start: str, doctor_id: int = 1) -> str:
    with psycopg.connect(settings.database_url) as conn:
        row = conn.execute(
            """
            SELECT status FROM slots
            WHERE doctor_id = %s AND starts_at = (%s::timestamp AT TIME ZONE 'Asia/Muscat')
            """,
            (doctor_id, local_start),
        ).fetchone()
    assert row is not None
    return str(row[0])


# --- Book the first one ---------------------------------------------------------------------


def test_after_a_restart_book_the_first_one_books_the_first_offered_slot(
    settings: Settings,
) -> None:
    with _app(settings) as first_app:
        conversation_id = _start(first_app)
        listed = _say(first_app, conversation_id, ASK_TOMORROW)

    with _app(settings) as second_app:  # a restart: new pools, new graph, same database
        booked = _say(second_app, conversation_id, BOOK_FIRST)

    assert listed["outcome"] == "slots_listed"
    assert "1. 09:00" in listed["reply"]
    assert booked["intent"] == "book"
    assert booked["outcome"] == "booked"
    assert booked["reply"].startswith(
        "You're booked with Dr. Salma Nasser on Tuesday 3 November at 09:00."
    )
    assert _bookings(settings) == [(conversation_id, "confirmed", "booked", "2026-11-03 09:00")]


def test_a_later_pick_books_that_slot(settings: Settings) -> None:
    with _app(settings) as client:
        conversation_id = _start(client)
        _say(client, conversation_id, ASK_TOMORROW)
        booked = _say(client, conversation_id, "Book number 3")

    assert booked["outcome"] == "booked"
    assert "Tuesday 3 November at 10:00" in booked["reply"]
    assert _bookings(settings) == [(conversation_id, "confirmed", "booked", "2026-11-03 10:00")]


def test_a_same_day_slot_is_held_for_staff_and_not_confirmed(settings: Settings) -> None:
    with _app(settings) as client:
        conversation_id = _start(client)
        listed = _say(client, conversation_id, "When is Dr. Salma free today?")
        answer = _say(client, conversation_id, BOOK_FIRST)

    assert "1. 12:30" in listed["reply"]
    assert answer["outcome"] == "awaiting_approval"  # CFD-15: staff decide, nothing is confirmed
    assert answer["status"] == "awaiting_approval"
    assert _bookings(settings) == [
        (conversation_id, "pending_approval", "held", "2026-11-02 12:30")
    ]
    assert _slot_status(settings, "2026-11-02 12:30") == "held"


def test_a_slot_taken_meanwhile_says_so_and_lists_other_free_slots(settings: Settings) -> None:
    with _app(settings) as client:
        conversation_id = _start(client)
        _say(client, conversation_id, ASK_TOMORROW)
        with psycopg.connect(settings.database_url) as conn:  # someone else takes it
            conn.execute(
                """
                UPDATE slots SET status = 'booked' WHERE doctor_id = 1
                  AND starts_at = ('2026-11-03 09:00'::timestamp AT TIME ZONE 'Asia/Muscat')
                """
            )
        taken = _say(client, conversation_id, BOOK_FIRST)
        retry = _say(client, conversation_id, BOOK_FIRST)  # the new list's first slot

    assert taken["outcome"] == "slot_taken"
    assert taken["reply"].startswith(
        "Sorry, Tuesday 3 November at 09:00 with Dr. Salma Nasser is no longer available."
    )
    assert "1. 09:30" in taken["reply"]
    assert "09:00" not in taken["reply"].split("available.", 1)[1]
    assert retry["outcome"] == "booked"
    assert _bookings(settings) == [(conversation_id, "confirmed", "booked", "2026-11-03 09:30")]


def test_ten_conversations_booking_the_same_first_slot_get_exactly_one(settings: Settings) -> None:
    with _app(settings) as client:
        conversation_ids = [_start(client) for _ in range(10)]
        for conversation_id in conversation_ids:
            assert "1. 09:00" in _say(client, conversation_id, ASK_TOMORROW)["reply"]
        with ThreadPoolExecutor(max_workers=10) as executor:
            answers = list(
                executor.map(lambda cid: _say(client, cid, BOOK_FIRST), conversation_ids)
            )

    outcomes = Counter(answer["outcome"] for answer in answers)
    assert outcomes == {"booked": 1, "slot_taken": 9}
    winner = conversation_ids[[a["outcome"] for a in answers].index("booked")]
    assert _bookings(settings) == [(winner, "confirmed", "booked", "2026-11-03 09:00")]


def test_a_replayed_booking_turn_keeps_the_booking(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The graph booked, but storing the turn failed. The retry confirms, it does not say taken."""

    def broken_add(*args: object, **kwargs: object) -> None:
        raise psycopg.OperationalError("server closed the connection unexpectedly")

    with _app(settings) as client:
        conversation_id = _start(client)
        _say(client, conversation_id, ASK_TOMORROW)
        monkeypatch.setattr(messages_repository, "add", broken_add)
        failed = client.post(
            f"/api/v1/conversations/{conversation_id}/messages", json={"text": BOOK_FIRST}
        )
        monkeypatch.undo()
        retried = _say(client, conversation_id, BOOK_FIRST)

    assert failed.status_code == 503
    assert retried["outcome"] == "booked"
    assert "Tuesday 3 November at 09:00" in retried["reply"]
    assert _bookings(settings) == [(conversation_id, "confirmed", "booked", "2026-11-03 09:00")]


# --- No valid pick: show or ask, never guess ------------------------------------------------


def test_book_naming_a_doctor_without_a_pick_lists_their_slots(settings: Settings) -> None:
    with _app(settings) as client:
        conversation_id = _start(client)
        listed = _say(
            client, conversation_id, "I want to book an appointment with Dr. Khalid tomorrow"
        )
        booked = _say(client, conversation_id, BOOK_FIRST)

    assert listed["outcome"] == "slots_listed"
    assert listed["reply"].startswith("Free slots with Dr. Khalid Rahman on Tuesday 3 November:")
    assert 'Reply "book number 1"' in listed["reply"]
    assert booked["outcome"] == "booked"
    assert "Dr. Khalid Rahman" in booked["reply"]


@pytest.mark.parametrize("text", ["I want to book", "Book number 6"])
def test_after_an_offer_a_book_without_a_valid_pick_asks_which_slot(
    settings: Settings, text: str
) -> None:
    with _app(settings) as client:
        conversation_id = _start(client)
        _say(client, conversation_id, ASK_TOMORROW)
        answer = _say(client, conversation_id, text)
        booked = _say(client, conversation_id, "Book number 2")  # the offer is still there

    assert answer["outcome"] == "clarify"
    assert answer["reply"] == 'Which slot would you like? Reply "book number 1" to "book number 5".'
    assert _bookings(settings)[0][3] == "2026-11-03 09:30"
    assert booked["outcome"] == "booked"


def test_book_with_nothing_offered_and_no_doctor_asks_which_doctor(settings: Settings) -> None:
    with _app(settings) as client:
        conversation_id = _start(client)
        answer = _say(client, conversation_id, BOOK_FIRST)

    assert answer["outcome"] == "clarify"
    assert "Which doctor or specialty would you like?" in answer["reply"]
    assert _bookings(settings) == []


def test_booking_the_same_pick_again_confirms_the_same_booking(settings: Settings) -> None:
    with _app(settings) as client:
        conversation_id = _start(client)
        _say(client, conversation_id, ASK_TOMORROW)
        first = _say(client, conversation_id, BOOK_FIRST)
        again = _say(client, conversation_id, BOOK_FIRST)

    assert first["outcome"] == again["outcome"] == "booked"
    assert again["reply"] == first["reply"]
    assert _bookings(settings) == [(conversation_id, "confirmed", "booked", "2026-11-03 09:00")]

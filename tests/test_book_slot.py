"""`book_slot` on a freshly seeded real PostgreSQL: double booking is impossible in SQL."""

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest

from clinic.app import prepare_database
from clinic.clock import FixedClock
from clinic.config import Settings
from clinic.repositories import conversations
from clinic.repositories.bookings import BookingRow, SlotUnavailable, book_slot
from tests.conftest import temporary_database

# Monday 2026-11-02, 12:00 at the clinic (Asia/Muscat, UTC+4).
NOW = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
THREADS = 20


@pytest.fixture
def url(database_url: str) -> Iterator[str]:
    with temporary_database(database_url) as url:
        settings = Settings(llm_provider="fake", database_url=url)
        assert prepare_database(settings, FixedClock(NOW))  # tables, seed, checkpointer
        yield url


def _conversation(url: str) -> UUID:
    with psycopg.connect(url) as conn:
        return conversations.create(conn, uuid4(), "demo-patient", NOW).id


def _slot(url: str, *, local_start: str, doctor_id: int = 1) -> int:
    """Id of a seeded slot by its start in clinic time, e.g. '2026-11-03 09:00'."""
    with psycopg.connect(url) as conn:
        row = conn.execute(
            """
            SELECT id FROM slots
            WHERE doctor_id = %s AND starts_at = (%s::timestamp AT TIME ZONE 'Asia/Muscat')
            """,
            (doctor_id, local_start),
        ).fetchone()
    assert row is not None
    return int(row[0])


def _state(url: str, slot_id: int) -> tuple[str, list[tuple[str, UUID]]]:
    """(slot status, [(booking status, conversation id)])."""
    with psycopg.connect(url) as conn:
        slot = conn.execute("SELECT status FROM slots WHERE id = %s", (slot_id,)).fetchone()
        bookings = conn.execute(
            "SELECT status, conversation_id FROM bookings WHERE slot_id = %s ORDER BY id",
            (slot_id,),
        ).fetchall()
    assert slot is not None
    return slot[0], [(status, cid) for status, cid in bookings]


def _book(url: str, slot_id: int, conversation_id: UUID, now: datetime = NOW) -> BookingRow:
    with psycopg.connect(url) as conn:
        return book_slot(conn, slot_id=slot_id, conversation_id=conversation_id, now=now)


# --- One booking ----------------------------------------------------------------------------


def test_a_free_slot_is_booked_and_confirmed(url: str) -> None:
    slot_id = _slot(url, local_start="2026-11-03 09:00")
    conversation_id = _conversation(url)

    booking = _book(url, slot_id, conversation_id)

    assert (booking.slot_id, booking.status) == (slot_id, "confirmed")
    assert _state(url, slot_id) == ("booked", [("confirmed", conversation_id)])


def test_booking_the_same_slot_again_from_the_same_conversation_returns_that_booking(
    url: str,
) -> None:
    """A replayed turn must not turn the patient's own booking into "slot taken"."""
    slot_id = _slot(url, local_start="2026-11-03 09:00")
    conversation_id = _conversation(url)

    first = _book(url, slot_id, conversation_id)
    again = _book(url, slot_id, conversation_id)

    assert again == first
    assert _state(url, slot_id) == ("booked", [("confirmed", conversation_id)])


def test_a_slot_booked_by_another_conversation_is_unavailable(url: str) -> None:
    slot_id = _slot(url, local_start="2026-11-03 09:00")
    winner = _conversation(url)
    _book(url, slot_id, winner)

    with pytest.raises(SlotUnavailable) as raised:
        _book(url, slot_id, _conversation(url))

    assert raised.value.slot_id == slot_id
    assert _state(url, slot_id) == ("booked", [("confirmed", winner)])


@pytest.mark.parametrize("status", ["held", "booked"])
def test_a_slot_that_is_not_free_is_unavailable(url: str, status: str) -> None:
    slot_id = _slot(url, local_start="2026-11-03 09:00")
    with psycopg.connect(url) as conn:
        conn.execute("UPDATE slots SET status = %s WHERE id = %s", (status, slot_id))

    with pytest.raises(SlotUnavailable):
        _book(url, slot_id, _conversation(url))

    assert _state(url, slot_id) == (status, [])


@pytest.mark.parametrize("minutes_after_start", [0, 10])
def test_a_slot_that_has_started_is_unavailable(url: str, minutes_after_start: int) -> None:
    slot_id = _slot(url, local_start="2026-11-02 12:00")  # starts at NOW
    later = NOW + timedelta(minutes=minutes_after_start)

    with pytest.raises(SlotUnavailable):
        _book(url, slot_id, _conversation(url), now=later)

    assert _state(url, slot_id) == ("free", [])


def test_the_unique_index_is_mapped_to_slot_unavailable_and_nothing_changes(url: str) -> None:
    """Second guard: a free slot that somehow already has an active booking."""
    slot_id = _slot(url, local_start="2026-11-03 09:00")
    other = _conversation(url)
    with psycopg.connect(url) as conn:
        conn.execute(
            """
            INSERT INTO bookings (slot_id, conversation_id, status, created_at, updated_at)
            VALUES (%s, %s, 'pending_approval', %s, %s)
            """,
            (slot_id, other, NOW, NOW),
        )

    with psycopg.connect(url) as conn:
        with pytest.raises(SlotUnavailable):
            book_slot(conn, slot_id=slot_id, conversation_id=_conversation(url), now=NOW)
        # The failed attempt is rolled back on its own and the connection is still usable.
        assert conn.execute("SELECT 1").fetchone() == (1,)

    assert _state(url, slot_id) == ("free", [("pending_approval", other)])


def test_another_unique_violation_is_not_hidden(url: str) -> None:
    slot_id = _slot(url, local_start="2026-11-03 09:00")
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "CREATE UNIQUE INDEX one_booking_per_conversation ON bookings (conversation_id)"
        )
    conversation_id = _conversation(url)
    _book(url, _slot(url, local_start="2026-11-03 09:30"), conversation_id)

    with pytest.raises(psycopg.errors.UniqueViolation):
        _book(url, slot_id, conversation_id)


# --- Concurrency ----------------------------------------------------------------------------


def test_twenty_concurrent_bookings_of_one_slot_give_exactly_one(url: str) -> None:
    slot_id = _slot(url, local_start="2026-11-03 09:00")
    conversation_ids = [_conversation(url) for _ in range(THREADS)]
    start = threading.Barrier(THREADS)

    def attempt(conversation_id: UUID) -> BookingRow | SlotUnavailable:
        with psycopg.connect(url) as conn:  # its own connection, like a separate request
            start.wait(timeout=10)
            try:
                return book_slot(conn, slot_id=slot_id, conversation_id=conversation_id, now=NOW)
            except SlotUnavailable as exc:
                return exc

    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        results = list(executor.map(attempt, conversation_ids))

    booked = [r for r in results if isinstance(r, BookingRow)]
    refused = [r for r in results if isinstance(r, SlotUnavailable)]
    assert (len(booked), len(refused)) == (1, THREADS - 1)
    winner = conversation_ids[results.index(booked[0])]
    assert _state(url, slot_id) == ("booked", [("confirmed", winner)])

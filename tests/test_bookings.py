"""Real bookings access for the approval nodes (CFD-14 wiring): find a patient's confirmed
bookings and cancel one safely, on a freshly seeded real PostgreSQL with a fixed clock."""

import logging
from collections.abc import Iterator
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import psycopg
import pytest

from clinic.app import prepare_database
from clinic.clock import FixedClock
from clinic.config import Settings
from clinic.db import make_pool
from clinic.schema import apply_schema
from clinic.workflows.bookings import PostgresBookings
from tests.conftest import temporary_database

# Monday 2026-11-02, 12:00 at the clinic (Asia/Muscat, UTC+4).
NOW = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
SALMA = "Dr. Salma Nasser"
PATIENT = "demo-patient-1"
TOMORROW = date(2026, 11, 3)


class Env:
    def __init__(self, url: str, bookings: PostgresBookings) -> None:
        self.url = url
        self.bookings = bookings

    def conversation(self, patient_ref: str = PATIENT) -> UUID:
        conversation_id = uuid4()
        with psycopg.connect(self.url, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO conversations (id, patient_ref, created_at) VALUES (%s, %s, %s)",
                (conversation_id, patient_ref, NOW),
            )
        return conversation_id

    def book(
        self,
        day: date,
        *,
        patient_ref: str = PATIENT,
        status: str = "confirmed",
        nth: int = 0,
        starts_before_now: bool = False,
    ) -> tuple[int, int]:
        """A booking of Dr. Salma's `nth` slot on `day` (clinic time). Returns (booking, slot)."""
        conversation_id = self.conversation(patient_ref)
        with psycopg.connect(self.url, autocommit=True) as conn:
            row = conn.execute(
                """
                SELECT s.id FROM slots s JOIN doctors d ON d.id = s.doctor_id
                WHERE d.name = %s AND (s.starts_at AT TIME ZONE 'Asia/Muscat')::date = %s
                ORDER BY s.starts_at OFFSET %s LIMIT 1
                """,
                (SALMA, day, nth),
            ).fetchone()
            assert row is not None, "the seed has no such slot"
            slot_id = row[0]
            slot_status = "booked" if status == "confirmed" else "held"
            conn.execute("UPDATE slots SET status = %s WHERE id = %s", (slot_status, slot_id))
            booking = conn.execute(
                """
                INSERT INTO bookings (slot_id, conversation_id, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s) RETURNING id
                """,
                (slot_id, conversation_id, status, NOW, NOW),
            ).fetchone()
            assert booking is not None
            return booking[0], slot_id

    def booking_status(self, booking_id: int) -> str:
        with psycopg.connect(self.url, autocommit=True) as conn:
            row = conn.execute(
                "SELECT status FROM bookings WHERE id = %s", (booking_id,)
            ).fetchone()
        assert row is not None
        return str(row[0])

    def slot_status(self, slot_id: int) -> str:
        with psycopg.connect(self.url, autocommit=True) as conn:
            row = conn.execute("SELECT status FROM slots WHERE id = %s", (slot_id,)).fetchone()
        assert row is not None
        return str(row[0])


@pytest.fixture
def env(database_url: str) -> Iterator[Env]:
    with temporary_database(database_url) as url:
        settings = Settings(llm_provider="fake", database_url=url)
        clock = FixedClock(NOW)
        assert prepare_database(settings, clock)
        pool = make_pool(settings)
        pool.open(wait=True, timeout=10)
        try:
            yield Env(url, PostgresBookings(pool, clock, settings.clinic_timezone))
        finally:
            pool.close(timeout=5)


# --- find_confirmed -------------------------------------------------------------------------


def test_finds_the_patients_confirmed_booking_with_a_readable_label(env: Env) -> None:
    booking_id, _ = env.book(TOMORROW)

    [found] = env.bookings.find_confirmed(PATIENT, None)

    assert found.id == booking_id
    assert found.label == "on Tue 3 Nov at 09:00 with Dr. Salma Nasser"


def test_filters_by_the_day_in_clinic_time(env: Env) -> None:
    env.book(TOMORROW)

    assert len(env.bookings.find_confirmed(PATIENT, TOMORROW)) == 1
    assert env.bookings.find_confirmed(PATIENT, date(2026, 11, 4)) == []


def test_only_this_patients_confirmed_upcoming_bookings_are_found(env: Env) -> None:
    mine, _ = env.book(TOMORROW, nth=0)
    env.book(TOMORROW, nth=1, patient_ref="someone-else")
    env.book(TOMORROW, nth=2, status="pending_approval")
    cancelled, _ = env.book(TOMORROW, nth=3)
    with psycopg.connect(env.url, autocommit=True) as conn:
        conn.execute("UPDATE bookings SET status = 'cancelled' WHERE id = %s", (cancelled,))

    found = env.bookings.find_confirmed(PATIENT, None)

    assert [booking.id for booking in found] == [mine]


def test_a_booking_that_already_started_is_not_upcoming(env: Env) -> None:
    # 2026-11-02 09:00 clinic time is before NOW (12:00 clinic time).
    env.book(date(2026, 11, 2), nth=0)

    assert env.bookings.find_confirmed(PATIENT, None) == []


# --- cancel ---------------------------------------------------------------------------------


def test_cancel_cancels_the_booking_and_frees_the_slot(env: Env) -> None:
    booking_id, slot_id = env.book(TOMORROW)

    env.bookings.cancel(booking_id)

    assert env.booking_status(booking_id) == "cancelled"
    assert env.slot_status(slot_id) == "free"


def test_cancel_twice_is_harmless(env: Env) -> None:
    booking_id, slot_id = env.book(TOMORROW)

    env.bookings.cancel(booking_id)
    env.bookings.cancel(booking_id)

    assert env.booking_status(booking_id) == "cancelled"
    assert env.slot_status(slot_id) == "free"


def test_a_replayed_cancel_never_frees_a_slot_someone_else_has_since_booked(env: Env) -> None:
    booking_id, slot_id = env.book(TOMORROW)
    env.bookings.cancel(booking_id)
    with psycopg.connect(env.url, autocommit=True) as conn:  # another patient takes the slot
        other = uuid4()
        conn.execute(
            "INSERT INTO conversations (id, patient_ref, created_at) VALUES (%s, 'p2', %s)",
            (other, NOW),
        )
        conn.execute(
            "INSERT INTO bookings (slot_id, conversation_id, status, created_at, updated_at)"
            " VALUES (%s, %s, 'confirmed', %s, %s)",
            (slot_id, other, NOW, NOW),
        )
        conn.execute("UPDATE slots SET status = 'booked' WHERE id = %s", (slot_id,))

    env.bookings.cancel(booking_id)  # the resume applies the same decision again

    assert env.slot_status(slot_id) == "booked"


def test_cancel_only_touches_a_confirmed_booking(env: Env) -> None:
    booking_id, slot_id = env.book(TOMORROW, status="pending_approval")

    env.bookings.cancel(booking_id)

    assert env.booking_status(booking_id) == "pending_approval"
    assert env.slot_status(slot_id) == "held"


# --- approvals reference real bookings ------------------------------------------------------


def _insert_approval(url: str, conversation_id: UUID, booking_id: int) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            """
            INSERT INTO approvals (id, conversation_id, kind, booking_id, created_at)
            VALUES (%s, %s, 'cancellation', %s, %s)
            """,
            (uuid4(), conversation_id, booking_id, NOW),
        )


def test_an_approval_needs_a_real_booking(env: Env) -> None:
    conversation_id = env.conversation()

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _insert_approval(env.url, conversation_id, 999_999)


def test_an_approval_for_a_real_booking_is_stored(env: Env) -> None:
    booking_id, _ = env.book(TOMORROW)
    with psycopg.connect(env.url, autocommit=True) as conn:
        row = conn.execute(
            "SELECT conversation_id FROM bookings WHERE id = %s", (booking_id,)
        ).fetchone()
    assert row is not None

    _insert_approval(env.url, row[0], booking_id)


def test_the_constraint_is_added_to_a_database_created_before_it_existed(env: Env) -> None:
    with psycopg.connect(env.url, autocommit=True) as conn:
        conn.execute("ALTER TABLE approvals DROP CONSTRAINT approvals_booking_id_fkey")
        conn.execute(
            "INSERT INTO conversations (id, patient_ref, created_at) VALUES (%s, 'old', %s)",
            (conversation_id := uuid4(), NOW),
        )
        # An approval written before the constraint existed, pointing at no booking.
        conn.execute(
            "INSERT INTO approvals (id, conversation_id, kind, booking_id, created_at)"
            " VALUES (%s, %s, 'cancellation', 424242, %s)",
            (uuid4(), conversation_id, NOW),
        )

        apply_schema(conn)  # runs on every start; must not fail on the old row
        apply_schema(conn)  # and must be repeatable

        exists = conn.execute(
            "SELECT 1 FROM pg_constraint WHERE conname = 'approvals_booking_id_fkey'"
        ).fetchone()
    assert exists is not None
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _insert_approval(env.url, env.conversation(), 999_999)


def test_cancel_logs_when_nothing_was_cancelled(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    booking_id, _ = env.book(TOMORROW, status="pending_approval")

    with caplog.at_level(logging.WARNING):
        env.bookings.cancel(booking_id)

    [record] = [r for r in caplog.records if r.getMessage() == "booking.cancel_not_applied"]
    assert record.__dict__["fields"] == {"booking_id": booking_id}


def test_cancel_logs_nothing_when_it_cancelled(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    booking_id, _ = env.book(TOMORROW)

    with caplog.at_level(logging.WARNING):
        env.bookings.cancel(booking_id)

    assert [r for r in caplog.records if r.getMessage() == "booking.cancel_not_applied"] == []


def test_the_constraint_guard_looks_at_the_approvals_table_only(env: Env) -> None:
    """A same-named constraint on another table must not make the upgrade skip silently."""
    with psycopg.connect(env.url, autocommit=True) as conn:
        conn.execute("ALTER TABLE approvals DROP CONSTRAINT approvals_booking_id_fkey")
        conn.execute("CREATE TABLE decoy (booking_id bigint)")
        conn.execute("ALTER TABLE decoy ADD CONSTRAINT approvals_booking_id_fkey CHECK (true)")

        apply_schema(conn)

        exists = conn.execute(
            "SELECT 1 FROM pg_constraint WHERE conname = 'approvals_booking_id_fkey'"
            " AND conrelid = 'approvals'::regclass"
        ).fetchone()
    assert exists is not None


# --- Staff decisions on a held same-day booking (CFD-15) ---------------------------------------


def _hold(env: Env) -> tuple[int, int]:
    """A same-day style hold: booking `pending_approval`, slot `held`. (booking id, slot id)."""
    return env.book(TOMORROW, status="pending_approval")


def test_confirming_a_held_booking_books_the_slot(env: Env) -> None:
    booking_id, slot_id = _hold(env)

    env.bookings.confirm_held(booking_id)

    assert env.booking_status(booking_id) == "confirmed"
    assert env.slot_status(slot_id) == "booked"


def test_rejecting_a_held_booking_frees_the_slot(env: Env) -> None:
    booking_id, slot_id = _hold(env)

    env.bookings.reject_held(booking_id)

    assert env.booking_status(booking_id) == "rejected"
    assert env.slot_status(slot_id) == "free"


@pytest.mark.parametrize("decide", ["confirm_held", "reject_held"])
def test_a_decision_applied_twice_changes_nothing_the_second_time(env: Env, decide: str) -> None:
    booking_id, slot_id = _hold(env)
    getattr(env.bookings, decide)(booking_id)
    first = (env.booking_status(booking_id), env.slot_status(slot_id))

    getattr(env.bookings, decide)(booking_id)

    assert (env.booking_status(booking_id), env.slot_status(slot_id)) == first


def test_a_replayed_reject_never_frees_a_slot_someone_else_has_since_booked(env: Env) -> None:
    booking_id, slot_id = _hold(env)
    env.bookings.reject_held(booking_id)
    with psycopg.connect(env.url, autocommit=True) as conn:  # another patient takes the slot
        other = uuid4()
        conn.execute(
            "INSERT INTO conversations (id, patient_ref, created_at) VALUES (%s, 'p2', %s)",
            (other, NOW),
        )
        conn.execute(
            "INSERT INTO bookings (slot_id, conversation_id, status, created_at, updated_at)"
            " VALUES (%s, %s, 'confirmed', %s, %s)",
            (slot_id, other, NOW, NOW),
        )
        conn.execute("UPDATE slots SET status = 'booked' WHERE id = %s", (slot_id,))

    env.bookings.reject_held(booking_id)

    assert env.slot_status(slot_id) == "booked"


def test_a_confirmed_booking_is_not_changed_by_a_decision_meant_for_a_held_one(env: Env) -> None:
    booking_id, slot_id = env.book(TOMORROW)  # already confirmed

    env.bookings.reject_held(booking_id)

    assert env.booking_status(booking_id) == "confirmed"
    assert env.slot_status(slot_id) == "booked"

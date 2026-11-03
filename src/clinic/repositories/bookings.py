"""SQL for bookings (schema in sql/02_appointments.sql). Double booking is impossible in SQL.

A slot is taken by one statement: a conditional UPDATE that only changes a `free` slot, and the
booking INSERT on the row it returned. Two transactions racing for one slot both try the UPDATE;
PostgreSQL locks the row, the second re-checks `status = 'free'` after the first commits and
changes nothing. The partial unique index `bookings_one_active_per_slot` is the second guard.

Time-dependent statements take `now` from the injected Clock, never SQL `now()`.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

import psycopg
from psycopg import Connection
from psycopg.rows import TupleRow

# Name of the partial unique index in sql/02_appointments.sql.
ONE_ACTIVE_BOOKING_PER_SLOT: Final = "bookings_one_active_per_slot"


class SlotUnavailable(Exception):
    """The slot is not free any more (booked, held or started), or already has an active booking."""

    def __init__(self, slot_id: int) -> None:
        self.slot_id = slot_id
        super().__init__(f"slot {slot_id} is not available")


@dataclass(frozen=True)
class BookingRow:
    id: int
    slot_id: int
    status: str


def book_slot(
    conn: Connection[TupleRow], *, slot_id: int, conversation_id: UUID, now: datetime
) -> BookingRow:
    """Book a free, not yet started slot for a conversation: a `confirmed` booking, slot `booked`.

    Raises `SlotUnavailable` if the slot is taken. Idempotent per conversation: if this
    conversation already has the slot confirmed (a replayed turn), that booking is returned.
    Runs in its own transaction block, so the connection stays usable after `SlotUnavailable`.
    """
    try:
        with conn.transaction():
            row = conn.execute(
                """
                WITH taken AS (
                    UPDATE slots SET status = 'booked'
                    WHERE id = %(slot_id)s AND status = 'free' AND starts_at > %(now)s
                    RETURNING id
                )
                INSERT INTO bookings (slot_id, conversation_id, status, created_at, updated_at)
                SELECT id, %(conversation_id)s, 'confirmed', %(now)s, %(now)s FROM taken
                RETURNING id, slot_id, status
                """,
                {"slot_id": slot_id, "conversation_id": conversation_id, "now": now},
            ).fetchone()
    except psycopg.errors.UniqueViolation as exc:
        if exc.diag.constraint_name != ONE_ACTIVE_BOOKING_PER_SLOT:
            raise
        raise SlotUnavailable(slot_id) from exc
    if row is not None:
        return BookingRow(*row)
    existing = conn.execute(
        """
        SELECT id, slot_id, status FROM bookings
        WHERE slot_id = %(slot_id)s AND conversation_id = %(conversation_id)s
          AND status = 'confirmed'
        """,
        {"slot_id": slot_id, "conversation_id": conversation_id},
    ).fetchone()
    if existing is None:
        raise SlotUnavailable(slot_id)
    return BookingRow(*existing)

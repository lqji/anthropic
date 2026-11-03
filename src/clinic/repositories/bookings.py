"""SQL for bookings (schema in sql/02_appointments.sql). Double booking is impossible in SQL.

A slot is taken by one statement: a conditional UPDATE that only changes a `free` slot, and the
booking INSERT on the row it returned (`book_slot` books it, `hold_same_day` holds it for
staff). Two transactions racing for one slot both try the UPDATE; PostgreSQL locks the row, the
second re-checks `status = 'free'` after the first commits and changes nothing. The partial
unique index `bookings_one_active_per_slot` is the second guard.

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
# `approvals.kind` of a same-day booking (sql/03_approvals.sql).
SAME_DAY_BOOKING: Final = "same_day_booking"


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


@dataclass(frozen=True)
class HeldBooking:
    booking_id: int
    slot_id: int
    approval_id: UUID


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


def hold_same_day(
    conn: Connection[TupleRow],
    *,
    slot_id: int,
    conversation_id: UUID,
    approval_id: UUID,
    now: datetime,
) -> HeldBooking:
    """Hold a free, not yet started slot for staff approval: slot `held`, a `pending_approval`
    booking and a `pending` same-day approval for it, in one statement (all or nothing).

    The caller decides that the slot is same-day; staff confirm or reject it later (CFD-15).
    Raises `SlotUnavailable` if the slot is taken. Idempotent per conversation: if this
    conversation already holds the slot with a pending approval (a replayed turn), that hold is
    returned and `approval_id` is not used. Any other unique violation, such as the conversation
    already waiting for another approval, is raised as it is.
    """
    try:
        with conn.transaction():
            row = conn.execute(
                """
                WITH held AS (
                    UPDATE slots SET status = 'held'
                    WHERE id = %(slot_id)s AND status = 'free' AND starts_at > %(now)s
                    RETURNING id
                ),
                booking AS (
                    INSERT INTO bookings (slot_id, conversation_id, status, created_at, updated_at)
                    SELECT id, %(conversation_id)s, 'pending_approval', %(now)s, %(now)s FROM held
                    RETURNING id, slot_id
                ),
                approval AS (
                    INSERT INTO approvals (id, conversation_id, kind, booking_id, created_at)
                    SELECT %(approval_id)s, %(conversation_id)s, %(kind)s, id, %(now)s FROM booking
                    RETURNING id, booking_id
                )
                SELECT booking.id, booking.slot_id, approval.id
                FROM booking JOIN approval ON approval.booking_id = booking.id
                """,
                {
                    "slot_id": slot_id,
                    "conversation_id": conversation_id,
                    "approval_id": approval_id,
                    "kind": SAME_DAY_BOOKING,
                    "now": now,
                },
            ).fetchone()
    except psycopg.errors.UniqueViolation as exc:
        if exc.diag.constraint_name != ONE_ACTIVE_BOOKING_PER_SLOT:
            raise
        raise SlotUnavailable(slot_id) from exc
    if row is not None:
        return HeldBooking(*row)
    existing = conn.execute(
        """
        SELECT b.id, b.slot_id, a.id FROM bookings b
        JOIN approvals a ON a.booking_id = b.id AND a.kind = %(kind)s AND a.status = 'pending'
        WHERE b.slot_id = %(slot_id)s AND b.conversation_id = %(conversation_id)s
          AND b.status = 'pending_approval'
        """,
        {"slot_id": slot_id, "conversation_id": conversation_id, "kind": SAME_DAY_BOOKING},
    ).fetchone()
    if existing is None:
        raise SlotUnavailable(slot_id)
    return HeldBooking(*existing)

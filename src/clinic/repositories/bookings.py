"""SQL for bookings (schema in sql/02_appointments.sql).

Time-dependent statements take `now` from the injected Clock, never SQL `now()`.
"""

from dataclasses import dataclass
from datetime import datetime

from psycopg import Connection
from psycopg.rows import TupleRow


@dataclass(frozen=True)
class ConfirmedBooking:
    id: int
    starts_at: datetime
    doctor: str


def confirmed_for_patient(
    conn: Connection[TupleRow],
    patient_ref: str,
    *,
    now: datetime,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[ConfirmedBooking]:
    """The patient's confirmed bookings that have not started yet at `now`, soonest first.

    `start` and `end` limit them to [start, end) when given (one clinic day).
    """
    rows = conn.execute(
        """
        SELECT b.id, s.starts_at, d.name
        FROM bookings b
        JOIN conversations c ON c.id = b.conversation_id
        JOIN slots s ON s.id = b.slot_id
        JOIN doctors d ON d.id = s.doctor_id
        WHERE c.patient_ref = %(patient_ref)s
          AND b.status = 'confirmed'
          AND s.starts_at > %(now)s
          AND (%(start)s::timestamptz IS NULL OR s.starts_at >= %(start)s)
          AND (%(end)s::timestamptz IS NULL OR s.starts_at < %(end)s)
        ORDER BY s.starts_at, b.id
        """,
        {"patient_ref": patient_ref, "now": now, "start": start, "end": end},
    ).fetchall()
    return [ConfirmedBooking(*row) for row in rows]


def cancel(conn: Connection[TupleRow], booking_id: int, now: datetime) -> bool:
    """Cancel a confirmed booking and free its slot, in one transaction. True if it cancelled.

    Safe to repeat: only a `confirmed` booking changes, and the slot is freed only when this call
    changed it. A replayed decision therefore never frees a slot that someone else has booked
    since.
    """
    with conn.transaction():
        row = conn.execute(
            """
            UPDATE bookings SET status = 'cancelled', updated_at = %(now)s
            WHERE id = %(id)s AND status = 'confirmed'
            RETURNING slot_id
            """,
            {"id": booking_id, "now": now},
        ).fetchone()
        if row is None:
            return False
        conn.execute("UPDATE slots SET status = 'free' WHERE id = %s", (row[0],))
        return True

"""The real `BookingPort` for the approval nodes: bookings in PostgreSQL."""

from datetime import date
from zoneinfo import ZoneInfo

from psycopg_pool import ConnectionPool

from clinic.clock import Clock
from clinic.domain.schedule import day_bounds
from clinic.repositories import bookings
from clinic.repositories.bookings import UpcomingBooking
from clinic.workflows.approval import BookingRef


class PostgresBookings:
    def __init__(self, pool: ConnectionPool, clock: Clock, timezone: str) -> None:
        self._pool = pool
        self._clock = clock
        self._timezone = timezone

    def find_confirmed(self, patient_ref: str, on_date: date | None) -> list[BookingRef]:
        start, end = (None, None) if on_date is None else day_bounds(on_date, self._timezone)
        with self._pool.connection() as conn:
            rows = bookings.find_confirmed(
                conn, patient_ref, now=self._clock.now(), start=start, end=end
            )
        return [BookingRef(id=row.id, label=self._label(row)) for row in rows]

    def cancel(self, booking_id: int) -> None:
        with self._pool.connection() as conn:
            bookings.cancel(conn, booking_id, self._clock.now())

    def _label(self, row: UpcomingBooking) -> str:
        """How the patient sees the booking, e.g. "on Wed 4 Nov at 09:30 with Dr. Salma Nasser"."""
        local = row.starts_at.astimezone(ZoneInfo(self._timezone))
        return f"on {local:%a} {local.day} {local:%b} at {local:%H:%M} with {row.doctor}"

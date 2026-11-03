"""The real `BookingPort` for the approval nodes: bookings in PostgreSQL."""

from datetime import date
from zoneinfo import ZoneInfo

from psycopg_pool import ConnectionPool

from clinic.clock import Clock
from clinic.domain.schedule import day_bounds
from clinic.repositories import bookings
from clinic.repositories.bookings import ConfirmedBooking
from clinic.workflows.approval import BookingRef


class PostgresBookings:
    """Implements `BookingPort`. Days and labels are in the clinic's time zone."""

    def __init__(self, pool: ConnectionPool, clock: Clock, timezone: str) -> None:
        self._pool = pool
        self._clock = clock
        self._timezone = timezone

    def find_confirmed(self, patient_ref: str, on_date: date | None) -> list[BookingRef]:
        start, end = day_bounds(on_date, self._timezone) if on_date else (None, None)
        with self._pool.connection() as conn:
            found = bookings.confirmed_for_patient(
                conn, patient_ref, now=self._clock.now(), start=start, end=end
            )
        return [BookingRef(id=b.id, label=self._label(b)) for b in found]

    def cancel(self, booking_id: int) -> None:
        with self._pool.connection() as conn:
            bookings.cancel(conn, booking_id, self._clock.now())

    def _label(self, booking: ConfirmedBooking) -> str:
        local = booking.starts_at.astimezone(ZoneInfo(self._timezone))
        return f"on {local:%a} {local.day} {local:%b} at {local:%H:%M} with {booking.doctor}"

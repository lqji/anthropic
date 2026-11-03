"""The real `BookingPort` for the approval nodes: bookings in PostgreSQL."""

import logging
from datetime import date
from zoneinfo import ZoneInfo

from psycopg_pool import ConnectionPool

from clinic.clock import Clock
from clinic.domain.schedule import day_bounds
from clinic.repositories import bookings
from clinic.repositories.bookings import ConfirmedBooking
from clinic.workflows.approval import BookingRef

logger = logging.getLogger("clinic.bookings")


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
            cancelled = bookings.cancel(conn, booking_id, self._clock.now())
        if not cancelled:
            # Expected after a crash (the decision is applied twice), so the patient is still told
            # "cancelled"; but a booking that was never `confirmed` should be visible in the logs.
            logger.warning(
                "booking.cancel_not_applied", extra={"fields": {"booking_id": booking_id}}
            )

    def confirm_held(self, booking_id: int) -> None:
        with self._pool.connection() as conn:
            changed = bookings.confirm_held(conn, booking_id, self._clock.now())
        if not changed:
            logger.warning(
                "booking.confirm_not_applied", extra={"fields": {"booking_id": booking_id}}
            )

    def reject_held(self, booking_id: int) -> None:
        with self._pool.connection() as conn:
            changed = bookings.reject_held(conn, booking_id, self._clock.now())
        if not changed:
            logger.warning(
                "booking.reject_not_applied", extra={"fields": {"booking_id": booking_id}}
            )

    def _label(self, booking: ConfirmedBooking) -> str:
        local = booking.starts_at.astimezone(ZoneInfo(self._timezone))
        return f"on {local:%a} {local.day} {local:%b} at {local:%H:%M} with {booking.doctor}"

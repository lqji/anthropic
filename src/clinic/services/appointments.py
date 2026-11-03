"""Doctors, specialties and free slots for the API. Validation lives here, SQL in repositories."""

from datetime import date

from psycopg import Connection
from psycopg.rows import TupleRow
from psycopg_pool import ConnectionPool

from clinic.clock import Clock
from clinic.domain.dates import today_in
from clinic.domain.schedule import day_bounds
from clinic.errors import InvalidRequest, NotFound
from clinic.repositories import appointments
from clinic.repositories.appointments import DoctorRow, SlotRow, SpecialtyRow


class AppointmentService:
    def __init__(self, pool: ConnectionPool, clock: Clock, timezone: str) -> None:
        self._pool = pool
        self._clock = clock
        self._timezone = timezone

    def specialties(self) -> list[SpecialtyRow]:
        with self._pool.connection() as conn:
            return appointments.list_specialties(conn)

    def doctors(self, specialty: str | None) -> list[DoctorRow]:
        """All doctors, or those of one specialty (404 if the specialty does not exist)."""
        with self._pool.connection() as conn:
            specialty_id = None
            if specialty is not None:
                specialty_id = _specialty_id(conn, specialty)
            return appointments.list_doctors(conn, specialty_id)

    def free_slots(
        self, *, day: date, doctor_id: int | None, specialty: str | None
    ) -> list[SlotRow]:
        """Free slots of a doctor and/or specialty on `day` that have not started yet."""
        if doctor_id is None and specialty is None:
            raise InvalidRequest("give doctor_id or specialty")
        if day < today_in(self._clock, self._timezone):
            raise InvalidRequest("date is in the past")
        start, end = day_bounds(day, self._timezone)
        with self._pool.connection() as conn:
            if doctor_id is not None and appointments.get_doctor(conn, doctor_id) is None:
                raise NotFound("doctor not found")
            specialty_id = None if specialty is None else _specialty_id(conn, specialty)
            return appointments.free_slots(
                conn,
                now=self._clock.now(),
                start=start,
                end=end,
                doctor_id=doctor_id,
                specialty_id=specialty_id,
            )


def _specialty_id(conn: Connection[TupleRow], name: str) -> int:
    row = appointments.get_specialty_by_name(conn, name)
    if row is None:
        raise NotFound("specialty not found")
    return row.id

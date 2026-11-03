"""Demo catalogue and slots, inserted on the first start (when there are no doctors yet)."""

from datetime import date, datetime, timedelta
from typing import Final

from psycopg import Connection
from psycopg.rows import TupleRow

from clinic.domain.schedule import SLOT_LENGTH, local_date, slot_starts
from clinic.repositories import appointments

SEED_DAYS: Final = 5
SPECIALTIES: Final = ((1, "Dermatology"), (2, "General Practice"))
DOCTORS: Final = ((1, "Dr. Salma Nasser", 1), (2, "Dr. Khalid Rahman", 2))


def seed_slots(first_day: date, timezone: str) -> list[tuple[int, datetime, datetime]]:
    """(doctor_id, starts_at, ends_at) for every doctor, every slot, SEED_DAYS days."""
    return [
        (doctor_id, start, start + SLOT_LENGTH)
        for offset in range(SEED_DAYS)
        for doctor_id, _, _ in DOCTORS
        for start in slot_starts(first_day + timedelta(days=offset), timezone)
    ]


def seed_appointments(conn: Connection[TupleRow], timezone: str, now: datetime) -> bool:
    """Seed from today (clinic time) on, so same-day slots exist too. True if it seeded."""
    return appointments.seed_if_empty(
        conn, SPECIALTIES, DOCTORS, seed_slots(local_date(now, timezone), timezone)
    )

"""Demo catalogue (db/seed/*.sql) and slots, inserted on the first start (no doctors yet)."""

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Final

from psycopg import Connection
from psycopg.rows import TupleRow

from clinic.domain.schedule import SLOT_LENGTH, local_date, slot_starts
from clinic.repositories import appointments
from clinic.schema import sql_files

SEED_DAYS: Final = 5


def seed_slots(
    doctor_ids: list[int], first_day: date, timezone: str
) -> list[tuple[int, datetime, datetime]]:
    """(doctor_id, starts_at, ends_at) for every doctor, every slot, SEED_DAYS days."""
    return [
        (doctor_id, start, start + SLOT_LENGTH)
        for offset in range(SEED_DAYS)
        for doctor_id in doctor_ids
        for start in slot_starts(first_day + timedelta(days=offset), timezone)
    ]


def seed_appointments(
    conn: Connection[TupleRow], db_dir: Path, timezone: str, now: datetime
) -> bool:
    """Seed from today (clinic time) on, so same-day slots exist too. True if it seeded.

    The catalogue is SQL; the slots are built here because they need the injected Clock and the
    clinic time zone. One transaction: a failed seed leaves nothing and is retried next start.
    A seed folder that adds no doctors is an error, not a silent empty clinic.
    """
    with conn.transaction():
        if appointments.has_doctors(conn):
            return False
        for path in sql_files(db_dir, "seed"):
            conn.execute(path.read_text(encoding="utf-8"))
        doctor_ids = appointments.doctor_ids(conn)
        if not doctor_ids:
            raise ValueError(f"{db_dir / 'seed'} added no doctors: put the catalogue SQL there")
        slots = seed_slots(doctor_ids, local_date(now, timezone), timezone)
        appointments.insert_slots(conn, slots)
    return True

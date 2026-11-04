"""SQL for specialties, doctors and slots (schema in db/schema/02_appointments.sql).

Time-dependent statements take `now` from the injected Clock, never SQL `now()`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from psycopg import Connection
from psycopg.rows import TupleRow


@dataclass(frozen=True)
class SpecialtyRow:
    id: int
    name: str


@dataclass(frozen=True)
class DoctorRow:
    id: int
    name: str
    specialty_id: int
    specialty: str


@dataclass(frozen=True)
class SlotRow:
    id: int
    doctor_id: int
    doctor: str
    specialty: str
    starts_at: datetime
    ends_at: datetime


def list_specialties(conn: Connection[TupleRow]) -> list[SpecialtyRow]:
    rows = conn.execute("SELECT id, name FROM specialties ORDER BY name").fetchall()
    return [SpecialtyRow(*row) for row in rows]


def get_specialty_by_name(conn: Connection[TupleRow], name: str) -> SpecialtyRow | None:
    """Case-insensitive exact match on the specialty name."""
    row = conn.execute(
        "SELECT id, name FROM specialties WHERE lower(name) = lower(%s)", (name.strip(),)
    ).fetchone()
    return None if row is None else SpecialtyRow(*row)


def list_doctors(conn: Connection[TupleRow], specialty_id: int | None = None) -> list[DoctorRow]:
    rows = conn.execute(
        """
        SELECT d.id, d.name, sp.id, sp.name
        FROM doctors d JOIN specialties sp ON sp.id = d.specialty_id
        WHERE %(specialty_id)s::integer IS NULL OR sp.id = %(specialty_id)s
        ORDER BY d.name
        """,
        {"specialty_id": specialty_id},
    ).fetchall()
    return [DoctorRow(*row) for row in rows]


def get_doctor(conn: Connection[TupleRow], doctor_id: int) -> DoctorRow | None:
    row = conn.execute(
        """
        SELECT d.id, d.name, sp.id, sp.name
        FROM doctors d JOIN specialties sp ON sp.id = d.specialty_id
        WHERE d.id = %s
        """,
        (doctor_id,),
    ).fetchone()
    return None if row is None else DoctorRow(*row)


def get_doctor_by_name(conn: Connection[TupleRow], name: str) -> DoctorRow | None:
    """Case-insensitive exact match on the doctor's full name."""
    row = conn.execute(
        """
        SELECT d.id, d.name, sp.id, sp.name
        FROM doctors d JOIN specialties sp ON sp.id = d.specialty_id
        WHERE lower(d.name) = lower(%s)
        """,
        (name.strip(),),
    ).fetchone()
    return None if row is None else DoctorRow(*row)


def free_slots(
    conn: Connection[TupleRow],
    *,
    now: datetime,
    start: datetime,
    end: datetime,
    doctor_id: int | None = None,
    specialty_id: int | None = None,
    limit: int | None = None,
) -> list[SlotRow]:
    """Free slots starting in [start, end) that have not started yet at `now`, by time.

    `doctor_id` and `specialty_id` filter when given; `limit=None` returns them all.
    """
    rows = conn.execute(
        """
        SELECT s.id, d.id, d.name, sp.name, s.starts_at, s.ends_at
        FROM slots s
        JOIN doctors d ON d.id = s.doctor_id
        JOIN specialties sp ON sp.id = d.specialty_id
        WHERE s.status = 'free'
          AND s.starts_at > %(now)s
          AND (%(doctor_id)s::integer IS NULL OR d.id = %(doctor_id)s)
          AND (%(specialty_id)s::integer IS NULL OR d.specialty_id = %(specialty_id)s)
          AND s.starts_at >= %(start)s AND s.starts_at < %(end)s
        ORDER BY s.starts_at, d.name
        LIMIT %(limit)s::bigint
        """,
        {
            "now": now,
            "start": start,
            "end": end,
            "doctor_id": doctor_id,
            "specialty_id": specialty_id,
            "limit": limit,
        },
    ).fetchall()
    return [SlotRow(*row) for row in rows]


def first_free_slot_start(
    conn: Connection[TupleRow],
    *,
    now: datetime,
    after: datetime,
    doctor_id: int | None = None,
    specialty_id: int | None = None,
) -> datetime | None:
    """Start of the earliest free, not yet started slot at or after `after`, or None."""
    row = conn.execute(
        """
        SELECT min(s.starts_at)
        FROM slots s JOIN doctors d ON d.id = s.doctor_id
        WHERE s.status = 'free'
          AND s.starts_at > %(now)s
          AND (%(doctor_id)s::integer IS NULL OR d.id = %(doctor_id)s)
          AND (%(specialty_id)s::integer IS NULL OR d.specialty_id = %(specialty_id)s)
          AND s.starts_at >= %(after)s
        """,
        {"now": now, "after": after, "doctor_id": doctor_id, "specialty_id": specialty_id},
    ).fetchone()
    return None if row is None else row[0]


def has_doctors(conn: Connection[TupleRow]) -> bool:
    """True once the catalogue has been seeded."""
    row = conn.execute("SELECT EXISTS (SELECT 1 FROM doctors)").fetchone()
    return row is not None and bool(row[0])


def doctor_ids(conn: Connection[TupleRow]) -> list[int]:
    """Every doctor id, lowest first."""
    return [row[0] for row in conn.execute("SELECT id FROM doctors ORDER BY id").fetchall()]


def insert_slots(
    conn: Connection[TupleRow], slots: Sequence[tuple[int, datetime, datetime]]
) -> None:
    """Insert (doctor_id, starts_at, ends_at) rows; a slot that already exists is left alone."""
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO slots (doctor_id, starts_at, ends_at) VALUES (%s, %s, %s)
            ON CONFLICT (doctor_id, starts_at) DO NOTHING
            """,
            slots,
        )

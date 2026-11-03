from datetime import date
from typing import Final
from zoneinfo import ZoneInfo

from psycopg import Connection
from psycopg.rows import TupleRow

from clinic.domain.dates import today_in
from clinic.domain.schedule import day_bounds, local_date
from clinic.repositories import appointments
from clinic.repositories.appointments import SlotRow
from clinic.workflows.deps import WorkflowDeps
from clinic.workflows.nodes._stub import not_implemented
from clinic.workflows.nodes._types import Node
from clinic.workflows.state import ConversationState

MAX_OFFERED_SLOTS: Final = 5
SLOTS_LISTED: Final = "slots_listed"
NO_SLOTS: Final = "no_slots"
CLARIFY: Final = "clarify"


def find_slots(state: ConversationState) -> dict[str, object]:
    """Stub used when the graph is built without dependencies (routing-only tests)."""
    return not_implemented()


def make_find_slots(deps: WorkflowDeps) -> Node:
    """Build the real `find_slots` node.

    Reads `doctor` or `specialty` and `date` (YYYY-MM-DD, default today) from `entities`. Lists up
    to 5 numbered free slots in clinic time and stores them in `offered_slots` for "book the
    first one". If that day has none, offers the next day that has free slots.
    """
    timezone = deps.settings.clinic_timezone

    def find_slots(state: ConversationState) -> dict[str, object]:
        entities = state.get("entities", {})
        now = deps.clock.now()
        today = today_in(deps.clock, timezone)
        day = max(_parse_date(entities.get("date")) or today, today)

        with deps.pool.connection() as conn:
            target = _target(conn, entities)
            if target is None:
                return _ask_which_doctor(conn)
            doctor_id, specialty_id, label = target
            start, end = day_bounds(day, timezone)
            slots = appointments.free_slots(
                conn,
                now=now,
                start=start,
                end=end,
                doctor_id=doctor_id,
                specialty_id=specialty_id,
                limit=MAX_OFFERED_SLOTS,
            )
            if slots:
                reply = f"Free slots {label} on {_day(day)}:\n{_numbered(slots, timezone)}"
                return _offer(
                    reply + "\nReply with a number to book.", SLOTS_LISTED, slots, timezone
                )

            next_start = appointments.first_free_slot_start(
                conn, now=now, after=end, doctor_id=doctor_id, specialty_id=specialty_id
            )
            if next_start is None:
                reply = (
                    f"Sorry, there are no free slots {label} on {_day(day)} or in the next days. "
                    "Please call the front desk."
                )
                return _offer(reply, NO_SLOTS, [], timezone)
            next_day = local_date(next_start, timezone)
            next_start_bound, next_end = day_bounds(next_day, timezone)
            slots = appointments.free_slots(
                conn,
                now=now,
                start=next_start_bound,
                end=next_end,
                doctor_id=doctor_id,
                specialty_id=specialty_id,
                limit=MAX_OFFERED_SLOTS,
            )
        reply = (
            f"Sorry, there are no free slots {label} on {_day(day)}. "
            f"The next day with free slots is {_day(next_day)}:\n{_numbered(slots, timezone)}"
            "\nReply with a number to book."
        )
        return _offer(reply, NO_SLOTS, slots, timezone)

    return find_slots


def _target(
    conn: Connection[TupleRow], entities: dict[str, str]
) -> tuple[int | None, int | None, str] | None:
    """(doctor_id, specialty_id, label for the reply) or None if neither is known."""
    if name := entities.get("doctor"):
        doctor = appointments.get_doctor_by_name(conn, name)
        return None if doctor is None else (doctor.id, None, f"with {doctor.name}")
    if name := entities.get("specialty"):
        specialty = appointments.get_specialty_by_name(conn, name)
        return None if specialty is None else (None, specialty.id, f"in {specialty.name}")
    return None


def _ask_which_doctor(conn: Connection[TupleRow]) -> dict[str, object]:
    doctors = ", ".join(f"{d.name} ({d.specialty})" for d in appointments.list_doctors(conn))
    return {
        "reply": f"Which doctor or specialty would you like? Our doctors are: {doctors}.",
        "outcome": CLARIFY,
    }


def _offer(reply: str, outcome: str, slots: list[SlotRow], timezone: str) -> dict[str, object]:
    zone = ZoneInfo(timezone)
    offered: list[dict[str, str | int]] = [
        {
            "slot_id": slot.id,
            "doctor_id": slot.doctor_id,
            "doctor": slot.doctor,
            "starts_at": slot.starts_at.astimezone(zone).isoformat(),
        }
        for slot in slots
    ]
    return {"reply": reply, "outcome": outcome, "offered_slots": offered}


def _numbered(slots: list[SlotRow], timezone: str) -> str:
    """One numbered line per slot in clinic time, with the doctor when it varies."""
    zone = ZoneInfo(timezone)
    several_doctors = len({slot.doctor_id for slot in slots}) > 1
    lines = []
    for number, slot in enumerate(slots, start=1):
        line = f"{number}. {slot.starts_at.astimezone(zone):%H:%M}"
        if several_doctors:
            line += f" with {slot.doctor}"
        lines.append(line)
    return "\n".join(lines)


def _day(day: date) -> str:
    return f"{day:%A} {day.day} {day:%B}"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def book(state: ConversationState) -> dict[str, object]:
    """Stub (CFD-13 books an offered slot atomically; CFD-15 holds same-day slots)."""
    return not_implemented()


def confirm_booking(state: ConversationState) -> dict[str, object]:
    """Stub (CFD-13 confirms doctor, date and time to the patient)."""
    return {}

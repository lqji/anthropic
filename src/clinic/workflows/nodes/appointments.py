from datetime import date, datetime
from typing import Final
from uuid import UUID
from zoneinfo import ZoneInfo

from langgraph.config import get_config
from psycopg import Connection
from psycopg.rows import TupleRow

from clinic.domain.choice import slot_choice
from clinic.domain.dates import today_in
from clinic.domain.schedule import day_bounds, local_date
from clinic.repositories import appointments, bookings
from clinic.repositories.appointments import SlotRow
from clinic.repositories.bookings import SlotUnavailable
from clinic.workflows.deps import WorkflowDeps
from clinic.workflows.nodes._stub import not_implemented
from clinic.workflows.nodes._types import Node
from clinic.workflows.routing import latest_patient_text
from clinic.workflows.state import ConversationState

MAX_OFFERED_SLOTS: Final = 5
SLOTS_LISTED: Final = "slots_listed"
NO_SLOTS: Final = "no_slots"
CLARIFY: Final = "clarify"
BOOKED: Final = "booked"
SLOT_TAKEN: Final = "slot_taken"
SAME_DAY_NEEDS_APPROVAL: Final = "same_day_needs_approval"
BOOK_HINT: Final = '\nReply "book number 1" (or another number) to book.'

# A target to list slots for: (doctor_id, specialty_id, label for the reply such as "with Dr. X").
_Target = tuple[int | None, int | None, str]


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
        today = today_in(deps.clock, timezone)
        day = max(_parse_date(entities.get("date")) or today, today)

        with deps.pool.connection() as conn:
            target = _target(conn, entities)
            if target is None:
                return _ask_which_doctor(conn)
            return _list_free(conn, deps.clock.now(), day, timezone, target)

    return find_slots


def _list_free(
    conn: Connection[TupleRow], now: datetime, day: date, timezone: str, target: _Target
) -> dict[str, object]:
    """Free slots of `target` on `day`, or on the next day that has some, as an offer."""
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
        return _offer(reply + BOOK_HINT, SLOTS_LISTED, slots, timezone)

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
    )
    return _offer(reply + BOOK_HINT, NO_SLOTS, slots, timezone)


def _target(conn: Connection[TupleRow], entities: dict[str, str]) -> _Target | None:
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


def _when(starts_at: datetime) -> str:
    """'Tuesday 3 November at 09:00' for a clinic-time instant (`offered_slots` keep the offset)."""
    return f"{_day(starts_at.date())} at {starts_at:%H:%M}"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def book(state: ConversationState) -> dict[str, object]:
    """Stub used when the graph is built without dependencies (routing-only tests)."""
    return not_implemented()


def make_book(deps: WorkflowDeps) -> Node:
    """Build the real `book` node: book the offered slot the patient picked.

    "Book the first one" picks from `offered_slots`, which the checkpoint keeps across turns and
    restarts. A later-day slot is booked atomically (`bookings.book_slot`) and handed to
    `confirm_booking`. A same-day slot is never confirmed here: it needs front-desk approval
    (CFD-15). A slot taken meanwhile gets an apology and a fresh list. Without a valid pick, the
    patient is shown slots (or asked which one) instead of the node guessing.
    """
    timezone = deps.settings.clinic_timezone
    list_slots = make_find_slots(deps)

    def book(state: ConversationState) -> dict[str, object]:
        offered = state.get("offered_slots", [])
        entities = state.get("entities", {})
        choice = slot_choice(latest_patient_text(state))
        if choice is None or not 1 <= choice <= len(offered):
            if offered and not ("doctor" in entities or "specialty" in entities):
                return {
                    "reply": f'Which slot would you like? Reply "book number 1" to '
                    f'"book number {len(offered)}".',
                    "outcome": CLARIFY,
                }
            return list_slots(state)

        slot = offered[choice - 1]
        starts_at = datetime.fromisoformat(str(slot["starts_at"]))
        if local_date(starts_at, timezone) == today_in(deps.clock, timezone):
            return {
                "reply": f"Same-day appointments need approval from our front desk, so I have not "
                f"booked {starts_at:%H:%M} today. Please call the front desk, or pick a slot "
                "on another day.",
                "outcome": SAME_DAY_NEEDS_APPROVAL,
            }

        now = deps.clock.now()
        with deps.pool.connection() as conn:
            try:
                booking = bookings.book_slot(
                    conn,
                    slot_id=int(slot["slot_id"]),
                    conversation_id=_conversation_id(),
                    now=now,
                )
            except SlotUnavailable:
                target = (int(slot["doctor_id"]), None, f"with {slot['doctor']}")
                fresh = _list_free(conn, now, local_date(starts_at, timezone), timezone, target)
                return {
                    **fresh,
                    "reply": f"Sorry, {_when(starts_at)} with {slot['doctor']} is no longer "
                    f"available. {fresh['reply']}",
                    "outcome": SLOT_TAKEN,
                }
        # `offered_slots` stays as it is: a replayed turn (its storing failed) must pick the same
        # slot again, and `book_slot` then returns this conversation's booking instead of
        # refusing it.
        return {
            "booking": {
                "booking_id": booking.id,
                "doctor": str(slot["doctor"]),
                "starts_at": str(slot["starts_at"]),
            }
        }

    return book


def _conversation_id() -> UUID:
    """The conversation booking this turn: the graph's thread is the conversation id."""
    return UUID(str(get_config()["configurable"]["thread_id"]))


def confirm_booking(state: ConversationState) -> dict[str, object]:
    """Confirm the booking `book` made this turn: doctor, date and time in clinic time.

    If `book` booked nothing (it already replied), there is nothing to confirm.
    """
    booking = state.get("booking", {})
    if not booking:
        return {}
    starts_at = datetime.fromisoformat(str(booking["starts_at"]))
    return {
        "reply": f"You're booked with {booking['doctor']} on {_when(starts_at)}. See you then!",
        "outcome": BOOKED,
    }

"""CFD-15 on the real conversation graph: a same-day booking waits for front-desk approval while
the slot is held. A fresh seeded database, the fake LLM, a fixed clock (Monday 2026-11-02,
12:00 clinic time, so Dr. Salma's first free slot today is 12:30)."""

from typing import Any

import psycopg

from tests.chat_harness import DECISION, Chat, running

ASK_TODAY = "When is Dr. Salma free today?"
ASK_TOMORROW = "When is Dr. Salma free tomorrow?"
BOOK_FIRST = "Book the first one"
FIRST_SLOT_TODAY = "2026-11-02 12:30"


def _slot(chat: Chat, local_start: str) -> tuple[int, str]:
    """(slot id, status) of Dr. Salma's slot starting at this clinic time."""
    with psycopg.connect(chat.url, autocommit=True) as conn:
        row = conn.execute(
            """
            SELECT id, status FROM slots
            WHERE doctor_id = 1 AND starts_at = (%s::timestamp AT TIME ZONE 'Asia/Muscat')
            """,
            (local_start,),
        ).fetchone()
    assert row is not None
    return int(row[0]), str(row[1])


def _bookings(chat: Chat) -> list[tuple[int, str]]:
    with psycopg.connect(chat.url, autocommit=True) as conn:
        rows = conn.execute("SELECT id, status FROM bookings ORDER BY id").fetchall()
    return [(int(booking_id), str(status)) for booking_id, status in rows]


def _book_same_day(chat: Chat, patient_ref: str = "demo-patient-1") -> tuple[str, dict[str, Any]]:
    conversation_id = chat.start(patient_ref)
    chat.say(conversation_id, ASK_TODAY)
    return conversation_id, chat.say(conversation_id, BOOK_FIRST)


# --- Booking later today waits for staff ----------------------------------------------------------


def test_a_same_day_booking_holds_the_slot_and_waits_for_staff(chat: Chat) -> None:
    conversation_id, body = _book_same_day(chat)

    assert body["status"] == "awaiting_approval"
    assert body["outcome"] == "awaiting_approval"
    assert body["approval_id"]
    assert "12:30" in body["reply"] and "Dr. Salma Nasser" in body["reply"]
    assert _slot(chat, FIRST_SLOT_TODAY)[1] == "held"
    [(booking_id, status)] = _bookings(chat)
    assert status == "pending_approval"
    [approval] = chat.pending()
    assert approval["id"] == body["approval_id"]
    assert (approval["kind"], approval["booking_id"]) == ("same_day_booking", booking_id)
    assert chat.paused_at(conversation_id) == ("await_approval",)  # a real interrupt()


def test_another_patient_trying_the_held_slot_gets_slot_taken(chat: Chat) -> None:
    other = chat.start("demo-patient-2")
    chat.say(other, ASK_TODAY)  # the other patient was shown the slot first
    _book_same_day(chat)

    body = chat.say(other, BOOK_FIRST)

    assert body["outcome"] == "slot_taken"
    assert body["status"] == "active"
    assert len(_bookings(chat)) == 1


# --- Staff decide -----------------------------------------------------------------------------


def test_approving_confirms_the_booking_books_the_slot_and_tells_the_patient(chat: Chat) -> None:
    conversation_id, body = _book_same_day(chat)

    response = chat.decide(body["approval_id"])

    assert response.status_code == 200
    assert _bookings(chat)[0][1] == "confirmed"
    assert _slot(chat, FIRST_SLOT_TODAY)[1] == "booked"
    message = response.json()["patient_message"]
    assert "12:30" in message and "confirmed" in message
    assert chat.messages(conversation_id)[-1]["text"] == message
    assert chat.client.get(f"/api/v1/conversations/{conversation_id}").json()["status"] == "active"


def test_rejecting_frees_the_slot_for_others_and_tells_the_patient_the_reason(chat: Chat) -> None:
    conversation_id, body = _book_same_day(chat)

    response = chat.decide(body["approval_id"], "reject")

    assert response.status_code == 200
    assert _bookings(chat)[0][1] == "rejected"
    assert _slot(chat, FIRST_SLOT_TODAY)[1] == "free"
    message = response.json()["patient_message"]
    assert DECISION["reason"] in message and "did not approve" in message
    # Bookable again: another patient can now hold the same slot.
    again_conversation, again = _book_same_day(chat, "demo-patient-2")
    assert again["status"] == "awaiting_approval"
    assert _slot(chat, FIRST_SLOT_TODAY)[1] == "held"
    assert again_conversation != conversation_id


def test_the_pause_survives_a_restart_and_approving_resumes_the_booking(url: str) -> None:
    with running(url) as first:
        _, body = _book_same_day(first)

    with running(url) as second:
        assert [a["id"] for a in second.pending()] == [body["approval_id"]]

        response = second.decide(body["approval_id"])

        assert response.status_code == 200
        assert _bookings(second)[0][1] == "confirmed"
        assert _slot(second, FIRST_SLOT_TODAY)[1] == "booked"


def test_while_the_booking_waits_a_routine_message_gets_still_waiting(chat: Chat) -> None:
    conversation_id, _ = _book_same_day(chat)

    body = chat.say(conversation_id, "What are your opening hours?")

    assert body["outcome"] == "still_waiting"


# --- Later days are unaffected ------------------------------------------------------------------


def test_a_booking_tomorrow_is_confirmed_at_once_without_staff(chat: Chat) -> None:
    conversation_id = chat.start()
    chat.say(conversation_id, ASK_TOMORROW)

    body = chat.say(conversation_id, BOOK_FIRST)

    assert body["outcome"] == "booked"
    assert body["status"] == "active"
    assert body.get("approval_id") is None
    assert _bookings(chat) == [(_bookings(chat)[0][0], "confirmed")]
    assert chat.pending() == []

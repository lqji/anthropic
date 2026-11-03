"""CFD-14 on the real conversation graph: "Cancel my appointment tomorrow" in a chat message,
the approval, the restart and the staff decision. A fresh seeded database, the fake LLM (no API
key), a fixed clock; the graph, PostgresSaver and bookings are the real ones."""

from uuid import UUID, uuid4

from tests.chat_harness import DECISION, Chat, running

CANCEL = "Cancel my appointment tomorrow"


def _request_cancellation(chat: Chat) -> tuple[str, int, int, str]:
    """A patient with a booking tomorrow asks to cancel: (conversation, booking, slot, approval)."""
    conversation_id = chat.start()
    booking_id, slot_id = chat.book(conversation_id)
    body = chat.say(conversation_id, CANCEL)
    return conversation_id, booking_id, slot_id, body["approval_id"]


# --- The chat message pauses the graph for staff ----------------------------------------------


def test_a_cancel_message_waits_for_staff_on_the_real_graph(chat: Chat) -> None:
    conversation_id = chat.start()
    booking_id, _ = chat.book(conversation_id)

    body = chat.say(conversation_id, CANCEL)

    assert body["status"] == "awaiting_approval"
    assert body["outcome"] == "awaiting_approval"
    assert UUID(body["approval_id"])
    assert "Tue 3 Nov at 09:00 with Dr. Salma Nasser" in body["reply"]
    [approval] = chat.pending()
    assert approval["id"] == body["approval_id"]
    assert (approval["kind"], approval["booking_id"]) == ("cancellation", booking_id)
    assert chat.paused_at(conversation_id) == ("await_approval",)  # a real interrupt()
    assert chat.status_of("bookings", booking_id) == "confirmed"  # nothing cancelled yet
    detail = chat.client.get(f"/api/v1/conversations/{conversation_id}").json()
    assert detail["status"] == "awaiting_approval"


def test_approving_cancels_the_booking_frees_the_slot_and_tells_the_patient(chat: Chat) -> None:
    conversation_id, booking_id, slot_id, approval_id = _request_cancellation(chat)

    response = chat.decide(approval_id)

    assert response.status_code == 200
    assert chat.status_of("bookings", booking_id) == "cancelled"
    assert chat.status_of("slots", slot_id) == "free"
    assert chat.paused_at(conversation_id) == ()
    assert "has been cancelled" in response.json()["patient_message"]
    last = chat.messages(conversation_id)[-1]
    assert last["role"] == "assistant" and last["text"] == response.json()["patient_message"]
    assert chat.client.get(f"/api/v1/conversations/{conversation_id}").json()["status"] == "active"


def test_rejecting_keeps_the_booking_and_tells_the_patient_the_reason(chat: Chat) -> None:
    conversation_id, booking_id, slot_id, approval_id = _request_cancellation(chat)

    response = chat.decide(approval_id, "reject")

    assert response.status_code == 200
    assert chat.status_of("bookings", booking_id) == "confirmed"
    assert chat.status_of("slots", slot_id) == "booked"
    assert DECISION["reason"] in response.json()["patient_message"]
    assert "still booked" in response.json()["patient_message"]


def test_the_patient_can_chat_again_after_the_decision(chat: Chat) -> None:
    conversation_id, _, _, approval_id = _request_cancellation(chat)
    chat.decide(approval_id)

    body = chat.say(conversation_id, "What are your opening hours?")

    assert body["outcome"] != "still_waiting"
    assert body["status"] == "active"


# --- The pause survives a restart ---------------------------------------------------------------


def test_the_approval_survives_a_restart_and_approving_resumes_the_same_workflow(url: str) -> None:
    with running(url) as first:
        conversation_id, booking_id, _, approval_id = _request_cancellation(first)

    with running(url) as second:  # a new process: new pools, new graph, new checkpointer
        assert [a["id"] for a in second.pending()] == [approval_id]
        assert second.paused_at(conversation_id) == ("await_approval",)

        response = second.decide(approval_id)

        assert response.status_code == 200
        assert second.status_of("bookings", booking_id) == "cancelled"
        assert second.paused_at(conversation_id) == ()


# --- While paused -------------------------------------------------------------------------------


def test_while_paused_a_routine_message_gets_still_waiting(chat: Chat) -> None:
    conversation_id, booking_id, _, _ = _request_cancellation(chat)

    body = chat.say(conversation_id, "What are your opening hours?")

    assert body["outcome"] == "still_waiting"
    assert body["status"] == "awaiting_approval"
    assert chat.paused_at(conversation_id) == ("await_approval",)  # the graph did not run
    assert len(chat.pending()) == 1


def test_while_paused_an_emergency_is_still_answered(chat: Chat) -> None:
    conversation_id, *_ = _request_cancellation(chat)

    body = chat.say(conversation_id, "I have crushing chest pain")

    assert body["outcome"] == "emergency"
    assert chat.paused_at(conversation_id) == ("await_approval",)
    assert len(chat.client.get("/api/v1/emergencies").json()) == 1


# --- No booking, two bookings, already waiting ----------------------------------------------------


def test_no_matching_booking_is_answered_without_asking_staff(chat: Chat) -> None:
    conversation_id = chat.start()

    body = chat.say(conversation_id, CANCEL)

    assert body["outcome"] == "cancel_not_found"
    assert body["status"] == "active"
    assert body.get("approval_id") is None
    assert chat.pending() == []
    assert chat.paused_at(conversation_id) == ()


def test_two_matching_bookings_ask_which_one(chat: Chat) -> None:
    conversation_id = chat.start()
    chat.book(conversation_id, nth=0)
    chat.book(conversation_id, nth=1)

    body = chat.say(conversation_id, CANCEL)

    assert body["outcome"] == "cancel_ambiguous"
    assert "Which one" in body["reply"]
    assert chat.pending() == []


def test_a_booking_already_waiting_for_approval_says_so(chat: Chat) -> None:
    conversation_id, *_ = _request_cancellation(chat)
    other = chat.start()  # the same patient, in another conversation

    body = chat.say(other, CANCEL)

    assert body["outcome"] == "already_pending"
    assert len(chat.pending()) == 1
    assert chat.paused_at(other) == ()
    assert conversation_id != other


def test_a_cancel_message_from_another_patient_never_finds_my_booking(chat: Chat) -> None:
    mine = chat.start()
    chat.book(mine)
    stranger = chat.start(patient_ref=f"stranger-{uuid4().hex[:6]}")

    body = chat.say(stranger, CANCEL)

    assert body["outcome"] == "cancel_not_found"


def test_other_messages_are_unaffected(chat: Chat) -> None:
    conversation_id = chat.start()

    body = chat.say(conversation_id, "What are your opening hours?")

    assert body["status"] == "active"
    assert body.get("approval_id") is None

"""The medical-advice guard: patient messages, replies, the decline path and the output guard."""

import logging
from collections.abc import Callable
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from clinic.config import Settings
from clinic.domain.guard import asks_for_medical_advice, guard_reply, violated_rule
from clinic.rag.chunking import chunk_document
from clinic.workflows.graph import build_graph
from clinic.workflows.nodes._stub import NOT_IMPLEMENTED_REPLY
from clinic.workflows.nodes.reply import CAPABILITIES_REPLY, CLARIFY, CLARIFY_REPLY
from clinic.workflows.nodes.safety import (
    DECLINE_REPLY,
    DECLINED_MEDICAL_ADVICE,
    decline,
    finalize,
)
from clinic.workflows.state import ConversationState, new_turn

SEED_DOCS = Path(__file__).resolve().parent.parent / "seed" / "clinic_docs"

ADVICE_REQUESTS = [
    "What cream should I put on my rash?",
    "Do I have diabetes?",
    "What medicine should I take for a headache?",
    "Which antibiotic is best for a sore throat?",
    "should I take ibuprofen?",
    "Can I take paracetamol with my other pills?",
    "How much should I take, and how many times a day?",
    "What's the dosage for aspirin?",
    "How do I treat a fever at home?",
    "How can I get rid of this rash?",
    "Is it safe to take antibiotics while pregnant?",
    "What is wrong with me?",
    "Can you diagnose my cough?",
    "Do I have a cold or the flu?",
    "Is this cancer?",
    "Could it be an infection?",
    "What should I do about my back pain?",
    "Why does my chest hurt?",
    "What can I use for a burn? Any home remedy?",
]
NOT_ADVICE_REQUESTS = [
    "Do I have an appointment tomorrow?",
    "Do I have to bring my insurance card?",
    "Can I eat before my blood test?",
    "Do I need to fast before my blood test?",
    "Which medicines should I bring to the visit?",
    "What should I take to my appointment?",
    "I have a rash. Can I see Dr. Salma on Thursday?",
    "Book me with a dermatologist next week",
    "Cancel my appointment please",
    "What are your opening hours?",
    "Do you accept my insurance?",
    "What should I do about my appointment on Friday?",
    "Is there a slot with a cardiologist tomorrow?",
    "Can I pay by card?",
    "",
]


@pytest.mark.parametrize("text", ADVICE_REQUESTS)
def test_requests_for_medical_advice_are_caught(text: str) -> None:
    assert asks_for_medical_advice(text), text


@pytest.mark.parametrize("text", NOT_ADVICE_REQUESTS)
def test_clinic_and_booking_requests_are_not_flagged(text: str) -> None:
    assert not asks_for_medical_advice(text), text


def test_curly_apostrophes_and_case_do_not_hide_a_request() -> None:
    assert asks_for_medical_advice("WHAT’S WRONG WITH ME?")


BAD_REPLIES = [
    "You probably have eczema, apply hydrocortisone twice a day.",
    "It sounds like an infection.",
    "Your symptoms suggest a viral illness.",
    "You should take two tablets of ibuprofen.",
    "I recommend taking an antihistamine.",
    "Take 500 mg of paracetamol every 6 hours.",
    "This looks like dermatitis.",
    "Try an ointment on the rash.",
    "The diagnosis is asthma.",
    "You may have diabetes.",
    "Rub some cream on it, 0.5 mg is enough.",
]


@pytest.mark.parametrize("reply", BAD_REPLIES)
def test_replies_that_diagnose_or_advise_are_replaced(reply: str) -> None:
    assert violated_rule(reply) is not None, reply
    sent, rule = guard_reply(reply, "fallback")
    assert (sent, rule is not None) == ("fallback", True)


def test_a_safe_reply_is_returned_unchanged() -> None:
    assert guard_reply("The clinic opens at 09:00.", "fallback") == (
        "The clinic opens at 09:00.",
        None,
    )


def test_every_seeded_clinic_chunk_passes_the_guard() -> None:
    chunks = [
        chunk.content
        for path in sorted(SEED_DOCS.glob("*.md"))
        for chunk in chunk_document(path.read_text(encoding="utf-8"))
    ]
    assert len(chunks) >= 10
    assert [text for text in chunks if violated_rule(text)] == []


def test_every_templated_reply_passes_the_guard() -> None:
    for reply in (DECLINE_REPLY, NOT_IMPLEMENTED_REPLY, CLARIFY_REPLY, CAPABILITIES_REPLY):
        assert violated_rule(reply) is None, reply


def test_decline_sets_the_fixed_reply_and_outcome() -> None:
    assert decline(new_turn("What cream?", "t1")) == {
        "reply": DECLINE_REPLY,
        "outcome": DECLINED_MEDICAL_ADVICE,
    }


def test_finalize_replaces_a_bad_reply_and_logs_only_the_rule(
    caplog: pytest.LogCaptureFixture,
) -> None:
    state: ConversationState = {
        **new_turn("my skin itches", "t1"),
        "reply": "You probably have eczema, apply hydrocortisone.",
        "outcome": "answered",
        "sources": [{"document": "insurance", "heading": "x"}],
    }

    with caplog.at_level(logging.WARNING, logger="clinic.safety"):
        update = finalize(state)

    message = update["messages"][0]  # type: ignore[index]  # update is a dict[str, object]
    assert isinstance(message, AIMessage)
    assert message.content == DECLINE_REPLY
    assert message.id == "t1:reply"
    assert (update["outcome"], update["sources"]) == (DECLINED_MEDICAL_ADVICE, [])
    assert [r.message for r in caplog.records] == ["safety.guard_triggered"]
    logged = repr(caplog.records[0].__dict__)
    assert "eczema" not in logged and "hydrocortisone" not in logged


def test_finalize_keeps_a_safe_reply_and_its_outcome() -> None:
    update = finalize({**new_turn("hi", "t1"), "reply": "We open at 09:00.", "outcome": "answered"})

    assert "outcome" not in update
    assert update["messages"][0].content == "We open at 09:00."  # type: ignore[index]


def test_a_request_for_advice_is_declined_through_the_graph(
    make_settings: Callable[..., Settings],
) -> None:
    graph = build_graph(make_settings(), None)

    result = graph.invoke(new_turn("What cream should I put on my rash?", "t1"))

    assert (result["reply"], result["outcome"]) == (DECLINE_REPLY, DECLINED_MEDICAL_ADVICE)
    assert result["intent"] == "unclear"  # classify never ran
    assert result["messages"][-1].content == DECLINE_REPLY


def test_a_booking_message_still_reaches_classify(
    make_settings: Callable[..., Settings],
) -> None:
    result = build_graph(make_settings(), None).invoke(
        new_turn("I have a rash, book Dr. Salma", "t1")
    )

    assert result["outcome"] == CLARIFY  # the stub classifier is unsure, but classify did run

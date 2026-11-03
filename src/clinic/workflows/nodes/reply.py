from typing import Final

from clinic.workflows.state import (
    UNKNOWN_DOCTOR,
    UNKNOWN_SPECIALTY,
    VALID_CHOICES,
    ConversationState,
)

CLARIFY: Final = "clarify"
OTHER: Final = "other"

CLARIFY_REPLY: Final = (
    "I'm not sure what you need. Would you like to ask about the clinic, see free appointment "
    "times, book an appointment or cancel one?"
)
CAPABILITIES_REPLY: Final = (
    "I'm the clinic's front-desk assistant. I can answer questions about the clinic (opening "
    "hours, insurance, how to prepare for a visit, our policies), show free appointment times, "
    "book an appointment or cancel one. I can't give medical advice. How can I help?"
)


def clarify(state: ConversationState) -> dict[str, object]:
    """One clarifying question. A doctor or specialty that is not in the catalogue gets the valid
    names instead. Fixed text, never model-written, and it writes nothing to the database."""
    entities = state.get("entities", {})
    named_unknown = UNKNOWN_DOCTOR in entities or UNKNOWN_SPECIALTY in entities
    choices = entities.get(VALID_CHOICES)
    if named_unknown and choices:
        reply = f"I'm not sure which doctor or specialty you mean. We have {choices}. Which one?"
    else:
        reply = CLARIFY_REPLY
    return {"reply": reply, "outcome": CLARIFY}


def other(state: ConversationState) -> dict[str, object]:
    """Templated capabilities message: greetings and anything outside the clinic's services."""
    return {"reply": CAPABILITIES_REPLY, "outcome": OTHER}

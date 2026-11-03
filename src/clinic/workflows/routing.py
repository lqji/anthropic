"""Pure routing functions for the conditional edges of the conversation graph."""

from collections.abc import Callable
from typing import Final, Literal

from langchain_core.messages import HumanMessage

from clinic.workflows.state import (
    BOOK,
    CANCEL,
    CLINIC_QUESTION,
    FIND_SLOTS,
    OTHER,
    UNKNOWN_DOCTOR,
    UNKNOWN_SPECIALTY,
    ConversationState,
)

StartRoute = Literal["classify", "decline"]
IntentRoute = Literal["knowledge", "find_slots", "book", "cancel", "other", "clarify"]
CancelRoute = Literal["request_approval", "finalize"]
RequestRoute = Literal["await_approval", "finalize"]

CANCEL_ROUTES: Final[tuple[CancelRoute, ...]] = ("request_approval", "finalize")
REQUEST_ROUTES: Final[tuple[RequestRoute, ...]] = ("await_approval", "finalize")
START_ROUTES: Final[tuple[StartRoute, ...]] = ("classify", "decline")
INTENT_ROUTES: Final[tuple[IntentRoute, ...]] = (
    "knowledge",
    "find_slots",
    "book",
    "cancel",
    "other",
    "clarify",
)

_NODE_FOR_INTENT: Final[dict[str, IntentRoute]] = {
    CLINIC_QUESTION: "knowledge",
    FIND_SLOTS: "find_slots",
    BOOK: "book",
    CANCEL: "cancel",
    OTHER: "other",
}


def never(text: str) -> bool:
    """Medical-advice check that never matches: for routing-only unit tests."""
    return False


def latest_patient_text(state: ConversationState) -> str:
    """Text of the newest human message, or '' if there is none."""
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            return message.text
    return ""


def route_start(
    state: ConversationState, asks_for_medical_advice: Callable[[str], bool] = never
) -> StartRoute:
    """A request for medical advice is declined before any LLM call; everything else is classified.

    The check is deterministic code, never the LLM. Emergencies never reach the graph at all.
    """
    if asks_for_medical_advice(latest_patient_text(state)):
        return "decline"
    return "classify"


def route_after_cancel(state: ConversationState) -> CancelRoute:
    """Ask staff when `cancel` found exactly one booking; else the turn already has its answer."""
    return "request_approval" if state.get("booking_id") is not None else "finalize"


def route_after_request(state: ConversationState) -> RequestRoute:
    """Pause for the decision when an approval was created; else (lost a race) the turn is done."""
    return "await_approval" if state.get("approval_id") is not None else "finalize"


def route_intent(state: ConversationState, min_confidence: float) -> IntentRoute:
    """Node for the classified intent.

    Unclear, unknown or low-confidence intents get a question. So do `find_slots` and `book` that
    name a doctor or specialty missing from the catalogue: the patient is asked which one.
    """
    if state.get("confidence", 0.0) < min_confidence:
        return "clarify"
    node = _NODE_FOR_INTENT.get(state.get("intent", ""), "clarify")
    entities = state.get("entities", {})
    if node in ("find_slots", "book") and (
        UNKNOWN_DOCTOR in entities or UNKNOWN_SPECIALTY in entities
    ):
        return "clarify"
    return node

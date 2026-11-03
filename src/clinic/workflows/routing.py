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
    ConversationState,
)

StartRoute = Literal["classify", "decline"]
IntentRoute = Literal["knowledge", "find_slots", "book", "cancel", "other", "clarify"]

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
    """Default medical-advice check until CFD-09 plugs in the real one."""
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


def route_intent(state: ConversationState, min_confidence: float) -> IntentRoute:
    """Node for the classified intent. Unclear, unknown or low-confidence intents get a question."""
    if state.get("confidence", 0.0) < min_confidence:
        return "clarify"
    return _NODE_FOR_INTENT.get(state.get("intent", ""), "clarify")

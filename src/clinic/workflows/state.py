"""State of one conversation, checkpointed by PostgresSaver under thread_id = conversation id.

Primitives only (str, int, float, bool, lists and dicts of them, plus LangChain messages): no
dataclasses or pydantic models, so every checkpoint stays inside the strict msgpack allowlist
(LANGGRAPH_STRICT_MSGPACK=true) and old checkpoints still load after the code changes.
"""

from typing import Annotated, Final, TypedDict

from langchain_core.messages import AnyMessage, HumanMessage
from langgraph.graph.message import add_messages

# Intents the classifier can return (CFD-07). `unclear` always leads to a clarifying question.
CLINIC_QUESTION: Final = "clinic_question"
FIND_SLOTS: Final = "find_slots"
BOOK: Final = "book"
CANCEL: Final = "cancel"
OTHER: Final = "other"
UNCLEAR: Final = "unclear"
INTENTS: Final = (CLINIC_QUESTION, FIND_SLOTS, BOOK, CANCEL, OTHER, UNCLEAR)


class ConversationState(TypedDict, total=False):
    # Whole history; `add_messages` appends what a node returns instead of replacing the list.
    messages: Annotated[list[AnyMessage], add_messages]
    # Set by `classify` (CFD-07) for the current turn.
    intent: str
    confidence: float
    entities: dict[str, str]
    # Reply for the current turn; `finalize` appends it to `messages`.
    reply: str
    outcome: str
    sources: list[dict[str, str]]  # {"document": ..., "heading": ...} (CFD-11)
    # Slots last shown to the patient, kept across turns for "book the first one" (CFD-12/13).
    offered_slots: list[dict[str, str | int]]
    approval_id: int  # pending staff approval (CFD-14)
    status: str  # conversation status after the turn: active | awaiting_approval | closed


def new_turn(text: str) -> ConversationState:
    """Graph input for one patient message. Clears the per-turn fields left by the last turn."""
    return {
        "messages": [HumanMessage(content=text)],
        "intent": UNCLEAR,
        "confidence": 0.0,
        "entities": {},
        "reply": "",
        "outcome": "",
        "sources": [],
    }

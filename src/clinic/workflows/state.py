"""State of one conversation, checkpointed by PostgresSaver under thread_id = conversation id.

Primitives only (str, int, float, bool, lists and dicts of them, plus LangChain messages): no
dataclasses or pydantic models, so every checkpoint stays inside the strict msgpack allowlist
(LANGGRAPH_STRICT_MSGPACK=true) and old checkpoints still load after the code changes.
"""

from typing import Annotated, Final

from langchain_core.messages import AnyMessage, HumanMessage
from langgraph.graph.message import add_messages

from clinic.workflows.approval import ApprovalState

# Intents the classifier can return (CFD-07). `unclear` always leads to a clarifying question.
CLINIC_QUESTION: Final = "clinic_question"
FIND_SLOTS: Final = "find_slots"
BOOK: Final = "book"
CANCEL: Final = "cancel"
OTHER: Final = "other"
UNCLEAR: Final = "unclear"
INTENTS: Final = (CLINIC_QUESTION, FIND_SLOTS, BOOK, CANCEL, OTHER, UNCLEAR)

# Entity keys `classify` sets when the patient names a doctor or specialty that is not in the
# catalogue. The router sends `find_slots` and `book` with one of them to `clarify`, which lists
# `valid_choices` instead of guessing.
UNKNOWN_DOCTOR: Final = "unknown_doctor"
UNKNOWN_SPECIALTY: Final = "unknown_specialty"
VALID_CHOICES: Final = "valid_choices"


class ConversationState(ApprovalState, total=False):
    """Also holds the approval fields (CFD-14): `reply`, `outcome`, `status` (the conversation
    status after the turn: active | awaiting_approval | closed), `approval_id`, `booking_id`, ..."""

    # Whole history. `add_messages` appends what a node returns, but replaces a message with the
    # same id: that is what makes a replayed turn safe (see `new_turn`).
    messages: Annotated[list[AnyMessage], add_messages]
    turn_id: str  # id of the current turn, see `turn_id_for`
    # Set by `classify` (CFD-07) for the current turn.
    intent: str
    confidence: float
    entities: dict[str, str]
    # `reply` for the current turn: `finalize` appends it to `messages`.
    sources: list[dict[str, str]]  # {"document": ..., "heading": ...} (CFD-11)
    # Slots last shown to the patient, kept across turns for "book the first one" (CFD-12/13).
    offered_slots: list[dict[str, str | int]]


def turn_id_for(conversation_id: str, turn_number: int) -> str:
    """Stable id of a turn: the same conversation and turn number always give the same id.

    The caller numbers turns from what it has stored (e.g. patient messages stored so far + 1), so
    a retry of a turn whose storing failed gets the same number, and so the same id.
    """
    return f"{conversation_id}:{turn_number}"


def new_turn(
    text: str,
    turn_id: str,
    *,
    conversation_id: str | None = None,
    patient_ref: str | None = None,
) -> ConversationState:
    """Graph input for one patient message. Clears the per-turn fields left by the last turn.

    The patient message gets the id `<turn_id>:patient` and `finalize` gives the reply
    `<turn_id>:reply`. Replaying a turn with the same id therefore replaces both messages in the
    checkpoint instead of appending duplicates.

    `conversation_id` and `patient_ref` are what the approval nodes read; they are left out when
    not given (routing-only tests).
    """
    turn: ConversationState = {
        "messages": [HumanMessage(content=text, id=f"{turn_id}:patient")],
        "turn_id": turn_id,
        "intent": UNCLEAR,
        "confidence": 0.0,
        "entities": {},
        "reply": "",
        "outcome": "",
        "sources": [],
        # A finished turn leaves these cleared, but a failed one may not: start clean.
        "approval_kind": None,
        "approval_id": None,
        "booking_id": None,
        "booking_label": None,
        "decision": None,
        "decision_reason": None,
    }
    if conversation_id is not None:
        turn["conversation_id"] = conversation_id
    if patient_ref is not None:
        turn["patient_ref"] = patient_ref
    return turn

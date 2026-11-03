"""Graph nodes. Every node is `def node(state: ConversationState) -> dict[str, object]`."""

from typing import Final

from clinic.workflows.deps import WorkflowDeps
from clinic.workflows.nodes._types import Node
from clinic.workflows.nodes.appointments import book, confirm_booking, find_slots, make_find_slots
from clinic.workflows.nodes.approvals import (
    apply_decision,
    await_approval,
    cancel,
    request_approval,
)
from clinic.workflows.nodes.classify import classify
from clinic.workflows.nodes.knowledge import knowledge
from clinic.workflows.nodes.reply import clarify, other
from clinic.workflows.nodes.safety import decline, finalize

NODES: Final[dict[str, Node]] = {
    "classify": classify,
    "decline": decline,
    "knowledge": knowledge,
    "find_slots": find_slots,
    "book": book,
    "confirm_booking": confirm_booking,
    "cancel": cancel,
    "request_approval": request_approval,
    "await_approval": await_approval,
    "apply_decision": apply_decision,
    "clarify": clarify,
    "other": other,
    "finalize": finalize,
}


def wired_nodes(deps: WorkflowDeps) -> dict[str, Node]:
    """Real nodes built with their dependencies; they replace the stubs of the same name."""
    return {"find_slots": make_find_slots(deps)}

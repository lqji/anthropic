"""Builds the conversation graph. Emergencies are handled before it runs (CFD-08)."""

from functools import partial

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from clinic.config import Settings
from clinic.domain.guard import asks_for_medical_advice
from clinic.workflows.deps import WorkflowDeps
from clinic.workflows.nodes import NODES, wired_nodes
from clinic.workflows.routing import INTENT_ROUTES, START_ROUTES, route_intent, route_start
from clinic.workflows.state import ConversationState

ConversationGraph = CompiledStateGraph[
    ConversationState, None, ConversationState, ConversationState
]

# Fixed edges. Later stories replace some with conditional ones (e.g. same-day booking, CFD-15).
_EDGES: tuple[tuple[str, str], ...] = (
    ("decline", "finalize"),
    ("knowledge", "finalize"),
    ("find_slots", "finalize"),
    ("book", "confirm_booking"),
    ("confirm_booking", "finalize"),
    ("cancel", "request_approval"),
    ("request_approval", "await_approval"),
    ("await_approval", "apply_decision"),
    ("apply_decision", "finalize"),
    ("clarify", "finalize"),
    ("other", "finalize"),
)


def build_graph(
    settings: Settings,
    checkpointer: BaseCheckpointSaver[str] | None,
    deps: WorkflowDeps | None = None,
) -> ConversationGraph:
    """Compile the graph. Pass a PostgresSaver; `None` is only for routing-only unit tests.

    With `deps`, nodes that need the database (e.g. `find_slots`) are the real ones; without
    them every node is its stub.
    """
    nodes = dict(NODES)
    if deps is not None:
        nodes.update(wired_nodes(deps))
    builder = StateGraph(ConversationState)
    for name, node in nodes.items():
        builder.add_node(name, node)
    builder.add_conditional_edges(
        START,
        partial(route_start, asks_for_medical_advice=asks_for_medical_advice),
        list(START_ROUTES),
    )
    builder.add_conditional_edges(
        "classify",
        partial(route_intent, min_confidence=settings.intent_min_confidence),
        list(INTENT_ROUTES),
    )
    for source, target in _EDGES:
        builder.add_edge(source, target)
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)

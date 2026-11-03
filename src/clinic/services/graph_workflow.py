"""Runs one conversation turn through the CFD-05 graph (the real `ConversationWorkflow`)."""

from uuid import UUID

from langchain_core.runnables import RunnableConfig
from psycopg_pool import ConnectionPool

from clinic.repositories import messages
from clinic.services.conversation import TurnResult
from clinic.workflows.graph import ConversationGraph
from clinic.workflows.state import UNCLEAR, new_turn, turn_id_for


class GraphWorkflow:
    """Adapts the checkpointed graph (thread_id = conversation id) to `ConversationWorkflow`.

    Each turn is numbered from what is stored, so a retry of a turn whose storing failed gets the
    same turn id and replaces itself in the checkpoint instead of duplicating (see `new_turn`).
    The caller holds the conversation lock, so the count and the invoke cannot interleave with
    another turn of the same conversation.
    """

    def __init__(self, graph: ConversationGraph, pool: ConnectionPool) -> None:
        self._graph = graph
        self._pool = pool

    def run_turn(self, conversation_id: str, text: str) -> TurnResult:
        with self._pool.connection() as conn:  # released before the graph runs
            turn_number = messages.count_graph_turns(conn, UUID(conversation_id)) + 1
        turn_id = turn_id_for(conversation_id, turn_number)
        config: RunnableConfig = {"configurable": {"thread_id": conversation_id}}
        values = self._graph.invoke(new_turn(text, turn_id), config)
        return TurnResult(
            reply=str(values["reply"]),
            intent=str(values.get("intent", UNCLEAR)),
            outcome=str(values["outcome"]),
            status=str(values.get("status", "active")),  # nodes set it once CFD-14 lands
        )

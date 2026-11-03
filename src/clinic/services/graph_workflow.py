"""Runs one conversation turn through the CFD-05 graph (the real `ConversationWorkflow`)."""

from uuid import UUID

from langchain_core.runnables import RunnableConfig
from psycopg_pool import ConnectionPool

from clinic.errors import NotFound
from clinic.repositories import conversations, messages
from clinic.services.turn import Source, TurnResult
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
            conversation = conversations.get(conn, UUID(conversation_id))
            if conversation is None:
                raise NotFound("conversation not found")
            turn_number = messages.count_graph_turns(conn, conversation.id) + 1
        turn_id = turn_id_for(conversation_id, turn_number)
        config: RunnableConfig = {"configurable": {"thread_id": conversation_id}}
        turn = new_turn(
            text, turn_id, conversation_id=conversation_id, patient_ref=conversation.patient_ref
        )
        values = self._graph.invoke(turn, config)
        return TurnResult(
            reply=str(values["reply"]),
            intent=str(values.get("intent", UNCLEAR)),
            outcome=str(values["outcome"]),
            status=str(values.get("status", "active")),
            approval_id=values.get("approval_id"),  # set while the turn waits for staff
            sources=tuple(
                Source(document=source["document"], heading=source["heading"])
                for source in values.get("sources", [])
            ),
        )

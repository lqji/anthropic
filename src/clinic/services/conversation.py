"""Start conversations, handle patient messages, and read a conversation back."""

import logging
from typing import Protocol
from uuid import UUID, uuid4

from psycopg_pool import ConnectionPool

from clinic.clock import Clock
from clinic.errors import NotFound, WorkflowUnavailable
from clinic.locks import ConversationLocks
from clinic.repositories import conversations, messages
from clinic.repositories.conversations import ConversationRow
from clinic.repositories.messages import MessageRow
from clinic.services.emergency import EmergencyService
from clinic.services.turn import TurnResult

logger = logging.getLogger("clinic.conversation")

# Must match the CHECK constraint on conversations.status (db/schema/01_conversations.sql).
CONVERSATION_STATUSES = frozenset({"active", "awaiting_approval", "closed"})

STILL_WAITING = (
    "We're still waiting for our front-desk staff to decide on your request. "
    "I'll message you here as soon as they do."
)


class ConversationWorkflow(Protocol):
    """Runs one turn of a conversation. CFD-05's LangGraph workflow implements this."""

    def run_turn(self, conversation_id: str, text: str) -> TurnResult: ...


class ConversationService:
    def __init__(
        self,
        pool: ConnectionPool,
        clock: Clock,
        locks: ConversationLocks,
        workflow: ConversationWorkflow,
        emergency: EmergencyService,
    ) -> None:
        self._pool = pool
        self._clock = clock
        self._locks = locks
        self._workflow = workflow
        self._emergency = emergency

    def start(self, patient_ref: str) -> ConversationRow:
        with self._pool.connection() as conn:
            return conversations.create(conn, uuid4(), patient_ref, self._clock.now())

    def get(self, conversation_id: str) -> tuple[ConversationRow, list[MessageRow]]:
        cid = _parse_id(conversation_id)
        with self._pool.connection() as conn:
            conversation = conversations.get(conn, cid)
            if conversation is None:
                raise NotFound("conversation not found")
            return conversation, messages.list_for(conn, cid)

    def handle_message(self, conversation_id: str, text: str) -> TurnResult:
        """An emergency is answered at once; else 404 if unknown, 409 if another message is in
        progress, else run the turn and store it.

        The emergency check comes first of all, before the conversation lookup, the lock and the
        workflow, so an emergency is answered even while another turn holds the lock, the LLM is
        down or the database is unreachable (it still gives 404 for a conversation that does
        not exist).

        No database connection is held while the workflow runs (it may call the LLM), so slow
        turns cannot exhaust the pool. The patient message and the reply are stored together,
        only after the workflow succeeded: a failed turn stores nothing, so a retry is clean.

        If storing the turn fails (database error), the client gets a 503 and nothing is stored,
        but the workflow has already run and checkpointed. A retry is safe: `GraphWorkflow`
        numbers each turn from the stored messages, so the retry gets the same turn id and
        replaces the turn in the checkpoint instead of duplicating it.
        """
        cid = _parse_id(conversation_id)
        emergency = self._emergency.check(cid, text)
        if emergency is not None:
            return emergency

        with self._pool.connection() as conn:
            if conversations.get(conn, cid) is None:
                raise NotFound("conversation not found")

        with self._locks.hold(str(cid)):
            result = self._paused_reply(cid) or self._workflow.run_turn(str(cid), text)
            if result.status not in CONVERSATION_STATUSES:
                logger.error(
                    "conversation.invalid_workflow_status",
                    extra={"fields": {"status": result.status}},
                )
                raise WorkflowUnavailable("the workflow returned an invalid status")

            # One connection block is one transaction: both messages and the status, or nothing.
            # The row lock keeps an emergency (which locks the same row) from storing its
            # messages between the patient message and the reply of this turn.
            with self._pool.connection() as conn:
                if conversations.get(conn, cid, for_update=True) is None:
                    raise NotFound("conversation not found")
                conversations.set_status(conn, cid, result.status)
                messages.add(conn, cid, "patient", text, self._clock.now())
                messages.add(
                    conn,
                    cid,
                    "assistant",
                    result.reply,
                    self._clock.now(),
                    intent=result.intent,
                    outcome=result.outcome,
                )

        logger.info(
            "conversation.turn",
            extra={"fields": {"intent": result.intent, "outcome": result.outcome}},
        )
        return result

    def _paused_reply(self, conversation_id: UUID) -> TurnResult | None:
        """While a conversation waits for staff approval, answer "still waiting" without
        invoking the workflow (its graph is paused at `await_approval`).

        Called under the conversation lock, which a staff decision also takes, so the status
        read here cannot be overtaken by a decision.
        """
        with self._pool.connection() as conn:
            conversation = conversations.get(conn, conversation_id)
        if conversation is None or conversation.status != "awaiting_approval":
            return None
        return TurnResult(
            reply=STILL_WAITING,
            intent="still_waiting",
            outcome="still_waiting",
            status="awaiting_approval",
        )


def _parse_id(conversation_id: str) -> UUID:
    """An id that is not a UUID cannot exist, so it is a 404 rather than a database error."""
    try:
        return UUID(conversation_id)
    except ValueError:
        raise NotFound("conversation not found") from None

"""Answer emergencies at once and flag them for staff, without the LLM or the workflow."""

import logging
from uuid import UUID

import psycopg
from psycopg_pool import ConnectionPool

from clinic.clock import Clock
from clinic.domain.safety import match_emergency
from clinic.errors import NotFound
from clinic.repositories import conversations, emergencies, messages
from clinic.repositories.emergencies import EmergencyRow
from clinic.services.turn import TurnResult

logger = logging.getLogger("clinic.emergency")

EXCERPT_CHARS = 200
EMERGENCY_REPLY = (
    "This may be a medical emergency. Call {number} now or go to the nearest emergency "
    "department. Our front-desk staff have been alerted."
)
# Used when the flag could not be saved: no claim that staff know, and a second way to reach them.
EMERGENCY_REPLY_UNSAVED = (
    "This may be a medical emergency. Call {number} now or go to the nearest emergency "
    "department. Please also phone the clinic directly."
)


class EmergencyService:
    def __init__(self, pool: ConnectionPool, clock: Clock, emergency_number: str) -> None:
        self._pool = pool
        self._clock = clock
        self._emergency_number = emergency_number

    def check(self, conversation_id: UUID, text: str) -> TurnResult | None:
        """None for a routine message. For an emergency: store the patient message, the staff
        flag and the instruction in one transaction, and return the instruction.

        Deterministic rules only; it never calls the workflow, so it answers even while another
        turn is running or the LLM is down. It does lock the conversation row for the length of
        its own transaction (see below). The instruction needs no database: if the database is
        down or the write fails, the error is logged and the patient still gets the instruction
        (nothing is half-saved, the transaction rolls back), but a version that does not say
        staff were alerted, because they were not. Only a conversation that does not exist is
        an error (404).
        """
        rule = match_emergency(text)
        if rule is None:
            return None

        reply = EMERGENCY_REPLY.format(number=self._emergency_number)
        unsaved_reply = EMERGENCY_REPLY_UNSAVED.format(number=self._emergency_number)
        status = "active"  # the real status is unknown if the database cannot be read
        try:
            with self._pool.connection() as conn:
                # FOR UPDATE: a routine turn stores its messages and status in a transaction
                # that updates this row, so the two cannot interleave and number turns wrongly.
                conversation = conversations.get(conn, conversation_id, for_update=True)
                if conversation is None:
                    raise NotFound("conversation not found")
                status = conversation.status
                now = self._clock.now()
                messages.add(conn, conversation_id, "patient", text, now)
                emergencies.create(conn, conversation_id, rule, text[:EXCERPT_CHARS], now)
                messages.add(
                    conn,
                    conversation_id,
                    "assistant",
                    reply,
                    now,
                    intent="emergency",
                    outcome="emergency",
                )
        except psycopg.Error as exc:  # includes pool timeouts and a closed pool
            # The rule name and error type only: patient text never goes to the logs.
            logger.error(
                "emergency.persist_failed",
                extra={"fields": {"matched_rule": rule, "error": type(exc).__name__}},
            )
            reply = unsaved_reply
        else:
            logger.warning("emergency.flagged", extra={"fields": {"matched_rule": rule}})
        return TurnResult(reply=reply, intent="emergency", outcome="emergency", status=status)

    def list_by_status(self, status: str) -> list[EmergencyRow]:
        with self._pool.connection() as conn:
            return emergencies.list_by_status(conn, status)

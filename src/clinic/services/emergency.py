"""Answer emergencies at once and flag them for staff, without the LLM or the workflow."""

import logging
from uuid import UUID

from psycopg_pool import ConnectionPool

from clinic.clock import Clock
from clinic.domain.safety import match_emergency
from clinic.repositories import emergencies, messages
from clinic.repositories.emergencies import EmergencyRow
from clinic.services.turn import TurnResult

logger = logging.getLogger("clinic.emergency")

EXCERPT_CHARS = 200
EMERGENCY_REPLY = (
    "This may be a medical emergency. Call {number} now or go to the nearest emergency "
    "department. Our front-desk staff have been alerted."
)


class EmergencyService:
    def __init__(self, pool: ConnectionPool, clock: Clock, emergency_number: str) -> None:
        self._pool = pool
        self._clock = clock
        self._emergency_number = emergency_number

    def check(self, conversation_id: UUID, text: str, status: str) -> TurnResult | None:
        """None for a routine message. For an emergency: store the patient message, the staff
        flag and the instruction in one transaction, and return the instruction.

        Deterministic rules only; it takes no conversation lock and never calls the workflow,
        so it answers even while another turn is running or the LLM is down.
        """
        rule = match_emergency(text)
        if rule is None:
            return None

        reply = EMERGENCY_REPLY.format(number=self._emergency_number)
        now = self._clock.now()
        with self._pool.connection() as conn:
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
        # The rule name only: patient text never goes to the logs.
        logger.warning("emergency.flagged", extra={"fields": {"matched_rule": rule}})
        return TurnResult(reply=reply, intent="emergency", outcome="emergency", status=status)

    def list_by_status(self, status: str) -> list[EmergencyRow]:
        with self._pool.connection() as conn:
            return emergencies.list_by_status(conn, status)

"""SQL for the append-only audit list (schema in db/schema/06_audit.sql).

`record` is called by the other repositories inside the transaction of the change it describes,
so the row and the change commit or roll back together. Time comes from the injected Clock.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from psycopg import Connection
from psycopg.rows import TupleRow

# `actor_type` values.
PATIENT: Final = "patient"
STAFF: Final = "staff"
SYSTEM: Final = "system"
# The actor of a booking change that follows a staff decision (the staff name is on the decision).
WORKFLOW: Final = "workflow"

# `action` values: the CHECK constraint in db/schema/06_audit.sql.
BOOKING_CREATED: Final = "booking_created"
SAME_DAY_REQUESTED: Final = "same_day_requested"
CANCELLATION_REQUESTED: Final = "cancellation_requested"
APPROVAL_APPROVED: Final = "approval_approved"
APPROVAL_REJECTED: Final = "approval_rejected"
BOOKING_CONFIRMED: Final = "booking_confirmed"
BOOKING_CANCELLED: Final = "booking_cancelled"
BOOKING_REJECTED: Final = "booking_rejected"


@dataclass(frozen=True)
class AuditEvent:
    id: int
    at: datetime
    actor_type: str
    actor: str
    action: str
    outcome: str
    booking_id: int
    conversation_id: UUID
    reason: str | None


def record(
    conn: Connection[TupleRow],
    *,
    now: datetime,
    actor_type: str,
    actor: str,
    action: str,
    outcome: str,
    booking_id: int,
    conversation_id: UUID,
    reason: str | None = None,
) -> None:
    """Append one event. Call it inside the transaction of the change it describes."""
    conn.execute(
        """
        INSERT INTO audit_events
            (at, actor_type, actor, action, outcome, booking_id, conversation_id, reason)
        VALUES (%(now)s, %(actor_type)s, %(actor)s, %(action)s, %(outcome)s, %(booking_id)s,
                %(conversation_id)s, %(reason)s)
        """,
        {
            "now": now,
            "actor_type": actor_type,
            "actor": actor,
            "action": action,
            "outcome": outcome,
            "booking_id": booking_id,
            "conversation_id": conversation_id,
            "reason": reason,
        },
    )


def list_events(
    conn: Connection[TupleRow],
    *,
    limit: int,
    offset: int,
    booking_id: int | None = None,
    conversation_id: UUID | None = None,
) -> list[AuditEvent]:
    """Events newest first, one page at a time, optionally for one booking or conversation.

    `id` breaks ties between events with the same time, so paging never skips or repeats a row.
    """
    rows = conn.execute(
        """
        SELECT id, at, actor_type, actor, action, outcome, booking_id, conversation_id, reason
        FROM audit_events
        WHERE (%(booking_id)s::bigint IS NULL OR booking_id = %(booking_id)s)
          AND (%(conversation_id)s::uuid IS NULL OR conversation_id = %(conversation_id)s)
        ORDER BY at DESC, id DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {
            "booking_id": booking_id,
            "conversation_id": conversation_id,
            "limit": limit,
            "offset": offset,
        },
    ).fetchall()
    return [AuditEvent(*row) for row in rows]

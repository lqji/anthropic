"""SQL for the `approvals` table (schema in db/schema/03_approvals.sql)."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import Connection
from psycopg.rows import TupleRow


@dataclass(frozen=True)
class ApprovalRow:
    id: UUID
    conversation_id: UUID
    kind: str
    booking_id: int
    status: str
    decided_by: str | None
    reason: str | None
    decided_at: datetime | None
    resumed_at: datetime | None
    created_at: datetime


def create(
    conn: Connection[TupleRow],
    approval_id: UUID,
    conversation_id: UUID,
    kind: str,
    booking_id: int,
    now: datetime,
) -> None:
    """Raises psycopg.errors.UniqueViolation if the booking or conversation already has a
    pending approval."""
    conn.execute(
        """
        INSERT INTO approvals (id, conversation_id, kind, booking_id, created_at)
        VALUES (%(id)s, %(conversation_id)s, %(kind)s, %(booking_id)s, %(now)s)
        """,
        {
            "id": approval_id,
            "conversation_id": conversation_id,
            "kind": kind,
            "booking_id": booking_id,
            "now": now,
        },
    )


def get(conn: Connection[TupleRow], approval_id: UUID) -> ApprovalRow | None:
    row = conn.execute(
        "SELECT id, conversation_id, kind, booking_id, status, decided_by, reason,"
        " decided_at, resumed_at, created_at"
        " FROM approvals WHERE id = %s",
        (approval_id,),
    ).fetchone()
    return None if row is None else ApprovalRow(*row)


def pending_for_booking(
    conn: Connection[TupleRow], kind: str, booking_id: int
) -> ApprovalRow | None:
    row = conn.execute(
        "SELECT id, conversation_id, kind, booking_id, status, decided_by, reason,"
        " decided_at, resumed_at, created_at"
        " FROM approvals"
        " WHERE kind = %s AND booking_id = %s AND status = 'pending'",
        (kind, booking_id),
    ).fetchone()
    return None if row is None else ApprovalRow(*row)


def open_for(
    conn: Connection[TupleRow], conversation_id: UUID, kind: str, booking_id: int
) -> ApprovalRow | None:
    """The approval of this conversation and booking that has not resumed its workflow yet.

    Pending, or decided but not resumed. `request_approval` uses it to reuse an approval whose
    row was saved by a run that died before its checkpoint, instead of asking staff twice.
    """
    row = conn.execute(
        "SELECT id, conversation_id, kind, booking_id, status, decided_by, reason,"
        " decided_at, resumed_at, created_at"
        " FROM approvals"
        " WHERE conversation_id = %s AND kind = %s AND booking_id = %s AND resumed_at IS NULL"
        " ORDER BY created_at DESC, id LIMIT 1",
        (conversation_id, kind, booking_id),
    ).fetchone()
    return None if row is None else ApprovalRow(*row)


def list_by_status(
    conn: Connection[TupleRow], status: str, limit: int = 100, offset: int = 0
) -> list[ApprovalRow]:
    """Approvals with the given status, oldest first (the order staff should handle them)."""
    rows = conn.execute(
        "SELECT id, conversation_id, kind, booking_id, status, decided_by, reason,"
        " decided_at, resumed_at, created_at"
        " FROM approvals"
        " WHERE status = %s ORDER BY created_at, id LIMIT %s OFFSET %s",
        (status, limit, offset),
    ).fetchall()
    return [ApprovalRow(*row) for row in rows]


def decide(
    conn: Connection[TupleRow],
    approval_id: UUID,
    status: str,
    decided_by: str,
    reason: str,
    now: datetime,
) -> ApprovalRow | None:
    """Record a decision if, and only if, the approval is still pending.

    The conditional UPDATE makes the decision race-free across requests and processes: of two
    concurrent decisions exactly one gets a row back. None means already decided (or unknown).
    """
    row = conn.execute(
        """
        UPDATE approvals
        SET status = %(status)s, decided_by = %(decided_by)s, reason = %(reason)s,
            decided_at = %(now)s
        WHERE id = %(id)s AND status = 'pending'
        RETURNING id, conversation_id, kind, booking_id, status, decided_by, reason,
                  decided_at, resumed_at, created_at
        """,
        {
            "id": approval_id,
            "status": status,
            "decided_by": decided_by,
            "reason": reason,
            "now": now,
        },
    ).fetchone()
    return None if row is None else ApprovalRow(*row)


def mark_resumed(conn: Connection[TupleRow], approval_id: UUID, now: datetime) -> None:
    conn.execute(
        "UPDATE approvals SET resumed_at = %s WHERE id = %s AND resumed_at IS NULL",
        (now, approval_id),
    )


def list_unresumed(conn: Connection[TupleRow]) -> list[ApprovalRow]:
    """Decided approvals whose workflow never resumed, e.g. after a crash, oldest first."""
    rows = conn.execute(
        "SELECT id, conversation_id, kind, booking_id, status, decided_by, reason,"
        " decided_at, resumed_at, created_at"
        " FROM approvals"
        " WHERE status <> 'pending' AND resumed_at IS NULL ORDER BY decided_at, id"
    ).fetchall()
    return [ApprovalRow(*row) for row in rows]

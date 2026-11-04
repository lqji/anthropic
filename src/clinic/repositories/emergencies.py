"""SQL for the `emergencies` table (schema in db/schema/05_safety.sql)."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import Connection
from psycopg.rows import TupleRow


@dataclass(frozen=True)
class EmergencyRow:
    id: int
    conversation_id: UUID
    matched_rule: str
    excerpt: str
    status: str
    created_at: datetime


def create(
    conn: Connection[TupleRow],
    conversation_id: UUID,
    matched_rule: str,
    excerpt: str,
    now: datetime,
) -> None:
    conn.execute(
        """
        INSERT INTO emergencies (conversation_id, matched_rule, excerpt, created_at)
        VALUES (%(conversation_id)s, %(matched_rule)s, %(excerpt)s, %(now)s)
        """,
        {
            "conversation_id": conversation_id,
            "matched_rule": matched_rule,
            "excerpt": excerpt,
            "now": now,
        },
    )


def list_by_status(conn: Connection[TupleRow], status: str) -> list[EmergencyRow]:
    """Emergencies with the given status, newest first."""
    rows = conn.execute(
        """
        SELECT id, conversation_id, matched_rule, excerpt, status, created_at
        FROM emergencies WHERE status = %s
        ORDER BY created_at DESC, id DESC
        """,
        (status,),
    ).fetchall()
    return [EmergencyRow(*row) for row in rows]

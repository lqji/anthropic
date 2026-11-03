"""SQL for the `messages` table (schema in sql/01_conversations.sql)."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import Connection
from psycopg.rows import TupleRow


@dataclass(frozen=True)
class MessageRow:
    role: str
    text: str
    intent: str | None
    outcome: str | None
    created_at: datetime


def add(
    conn: Connection[TupleRow],
    conversation_id: UUID,
    role: str,
    text: str,
    now: datetime,
    intent: str | None = None,
    outcome: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO messages (conversation_id, role, text, intent, outcome, created_at)
        VALUES (%(conversation_id)s, %(role)s, %(text)s, %(intent)s, %(outcome)s, %(now)s)
        """,
        {
            "conversation_id": conversation_id,
            "role": role,
            "text": text,
            "intent": intent,
            "outcome": outcome,
            "now": now,
        },
    )


def list_for(conn: Connection[TupleRow], conversation_id: UUID) -> list[MessageRow]:
    """All messages of a conversation, oldest first."""
    rows = conn.execute(
        """
        SELECT role, text, intent, outcome, created_at
        FROM messages WHERE conversation_id = %s ORDER BY id
        """,
        (conversation_id,),
    ).fetchall()
    return [MessageRow(*row) for row in rows]

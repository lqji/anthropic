"""SQL for the `messages` table (schema in db/schema/01_conversations.sql)."""

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


# Replies that did not go through the conversation graph, so their turns are not numbered:
# emergencies (CFD-08) and the "still waiting" answer to a paused conversation (CFD-14).
NON_GRAPH_OUTCOMES = ("emergency", "still_waiting")


def count_graph_turns(conn: Connection[TupleRow], conversation_id: UUID) -> int:
    """Stored patient messages that went through the graph. This count + 1 numbers the next turn.

    The count comes from what is *stored*, not from the checkpoint: a turn whose storing failed
    leaves the count unchanged, so its retry gets the same number and replaces the turn in the
    checkpoint instead of duplicating it. A patient message counts unless the reply stored right
    after it came from outside the graph (`NON_GRAPH_OUTCOMES`).
    """
    row = conn.execute(
        """
        SELECT count(*) FROM messages p
        WHERE p.conversation_id = %(conversation_id)s AND p.role = 'patient'
          AND COALESCE(
                (SELECT a.outcome FROM messages a
                 WHERE a.conversation_id = p.conversation_id
                   AND a.role = 'assistant' AND a.id > p.id
                 ORDER BY a.id LIMIT 1),
                '') <> ALL(%(skip)s)
        """,
        {"conversation_id": conversation_id, "skip": list(NON_GRAPH_OUTCOMES)},
    ).fetchone()
    assert row is not None  # noqa: S101 - count(*) always returns a row
    return int(row[0])

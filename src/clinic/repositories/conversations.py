"""SQL for the `conversations` table (schema in sql/01_conversations.sql)."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import Connection
from psycopg.rows import TupleRow


@dataclass(frozen=True)
class ConversationRow:
    id: UUID
    patient_ref: str
    status: str
    created_at: datetime


def create(
    conn: Connection[TupleRow], conversation_id: UUID, patient_ref: str, now: datetime
) -> ConversationRow:
    row = conn.execute(
        """
        INSERT INTO conversations (id, patient_ref, created_at)
        VALUES (%(id)s, %(patient_ref)s, %(now)s)
        RETURNING id, patient_ref, status, created_at
        """,
        {"id": conversation_id, "patient_ref": patient_ref, "now": now},
    ).fetchone()
    assert row is not None  # noqa: S101 - INSERT ... RETURNING always returns a row
    return ConversationRow(*row)


def get(conn: Connection[TupleRow], conversation_id: UUID) -> ConversationRow | None:
    row = conn.execute(
        "SELECT id, patient_ref, status, created_at FROM conversations WHERE id = %s",
        (conversation_id,),
    ).fetchone()
    return None if row is None else ConversationRow(*row)


def set_status(conn: Connection[TupleRow], conversation_id: UUID, status: str) -> None:
    conn.execute(
        "UPDATE conversations SET status = %s WHERE id = %s AND status <> %s",
        (status, conversation_id, status),
    )

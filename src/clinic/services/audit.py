"""Reading the audit list (CFD-30). Rows are written by the repositories, in the transaction of
the change they describe; this service only reads."""

from uuid import UUID

from psycopg_pool import ConnectionPool

from clinic.repositories import audit
from clinic.repositories.audit import AuditEvent


class AuditService:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def list_events(
        self,
        limit: int,
        offset: int,
        booking_id: int | None = None,
        conversation_id: UUID | None = None,
    ) -> list[AuditEvent]:
        """Newest first, optionally for one booking or one conversation."""
        with self._pool.connection() as conn:
            return audit.list_events(
                conn,
                limit=limit,
                offset=offset,
                booking_id=booking_id,
                conversation_id=conversation_id,
            )

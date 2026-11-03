import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row

from clinic.db import CONNECT_TIMEOUT_S, STATEMENT_TIMEOUT_MS


def setup_checkpointer(database_url: str) -> None:
    """Create or migrate the checkpoint tables. Idempotent, so it runs on every start.

    Uses its own autocommit connection: the migrations include CREATE INDEX CONCURRENTLY,
    which cannot run inside a transaction.
    """
    with psycopg.connect(
        database_url,
        autocommit=True,
        row_factory=dict_row,
        connect_timeout=CONNECT_TIMEOUT_S,
        options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS}",
    ) as conn:
        PostgresSaver(conn).setup()

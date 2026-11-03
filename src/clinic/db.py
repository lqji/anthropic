import logging

import psycopg
import psycopg_pool
from psycopg_pool import ConnectionPool

from clinic.config import Settings

logger = logging.getLogger("clinic.db")

CONNECT_TIMEOUT_S = 5
STATEMENT_TIMEOUT_MS = 5000


def make_pool(settings: Settings) -> ConnectionPool:
    """Build an explicitly sized pool. The caller opens and closes it."""
    return ConnectionPool(
        settings.database_url,
        min_size=2,
        max_size=settings.db_pool_max_size,
        timeout=settings.db_pool_timeout_s,
        kwargs={
            "connect_timeout": CONNECT_TIMEOUT_S,
            "options": f"-c statement_timeout={STATEMENT_TIMEOUT_MS}",
        },
        open=False,
    )


def ping(pool: ConnectionPool) -> bool:
    """True if a connection can be taken from the pool and answers a trivial query."""
    try:
        with pool.connection() as conn:
            conn.execute("SELECT 1")
    except (psycopg.Error, psycopg_pool.PoolTimeout) as exc:
        logger.error("db.ping_failed", extra={"fields": {"error": type(exc).__name__}})
        return False
    return True

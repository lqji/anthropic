import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
import psycopg_pool
from fastapi import FastAPI
from psycopg_pool import ConnectionPool

from clinic import __version__
from clinic.api import health
from clinic.clock import SystemClock
from clinic.config import Settings
from clinic.db import make_pool
from clinic.deps import Deps
from clinic.errors import install_error_handlers
from clinic.locks import ConversationLocks
from clinic.observability import configure_logging, install_request_logging
from clinic.rag.loader import KnowledgeLoader
from clinic.schema import apply_schema

logger = logging.getLogger("clinic.startup")

PREPARE_RETRY_S = 5.0


def prepare_database(pool: ConnectionPool, settings: Settings) -> bool:
    """Create the tables and load the clinic documents. True when done.

    An unreachable database is logged and gives False, so the api still starts and /health can
    report it. Any other error stops startup. Statements are bounded by the pool's
    statement_timeout, which also covers waiting for locks.
    """
    try:
        with pool.connection() as conn:
            apply_schema(conn)
            KnowledgeLoader(settings.clinic_docs_dir).load(conn)
    except (psycopg.OperationalError, psycopg_pool.PoolTimeout) as exc:
        logger.error(
            "startup.database_unavailable",
            extra={"fields": {"error": type(exc).__name__, "step": "schema_and_documents"}},
        )
        return False
    return True


def keep_preparing(pool: ConnectionPool, settings: Settings, stop: threading.Event) -> None:
    """Retry `prepare_database` until it succeeds or the app shuts down."""
    while not stop.wait(PREPARE_RETRY_S):
        if prepare_database(pool, settings):
            logger.info("startup.database_prepared_after_retry")
            return


def create_app(settings: Settings) -> FastAPI:
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = make_pool(settings)
        # Do not wait for the first connection: /health must be able to report a down database.
        pool.open(wait=False)
        stop_retry = threading.Event()
        retry: threading.Thread | None = None
        if not prepare_database(pool, settings):
            retry = threading.Thread(
                target=keep_preparing,
                args=(pool, settings, stop_retry),
                name="prepare-database",
                daemon=True,
            )
            retry.start()
        app.state.deps = Deps(
            settings=settings, pool=pool, clock=SystemClock(), locks=ConversationLocks()
        )
        try:
            yield
        finally:
            stop_retry.set()
            if retry is not None:
                retry.join(timeout=10)
            pool.close(timeout=5)

    app = FastAPI(title="Clinic Front-Desk Assistant", version=__version__, lifespan=lifespan)
    install_request_logging(app)
    install_error_handlers(app)
    app.include_router(health.router)
    return app

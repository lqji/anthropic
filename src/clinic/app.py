import logging
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


def prepare_database(pool: ConnectionPool, settings: Settings) -> None:
    """Create the tables and load the clinic documents.

    An unreachable database is logged, not fatal, so the api still starts and /health reports
    it; restart the api once the database is back. Any other error stops startup.
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


def create_app(settings: Settings) -> FastAPI:
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = make_pool(settings)
        # Do not wait for the first connection: /health must be able to report a down database.
        pool.open(wait=False)
        prepare_database(pool, settings)
        app.state.deps = Deps(
            settings=settings, pool=pool, clock=SystemClock(), locks=ConversationLocks()
        )
        try:
            yield
        finally:
            pool.close(timeout=5)

    app = FastAPI(title="Clinic Front-Desk Assistant", version=__version__, lifespan=lifespan)
    install_request_logging(app)
    install_error_handlers(app)
    app.include_router(health.router)
    return app

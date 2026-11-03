import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI
from langgraph.checkpoint.postgres import PostgresSaver

from clinic import __version__
from clinic.api import health
from clinic.clock import SystemClock
from clinic.config import Settings
from clinic.db import CONNECT_TIMEOUT_S, make_checkpoint_pool, make_pool
from clinic.deps import Deps
from clinic.errors import install_error_handlers
from clinic.locks import ConversationLocks
from clinic.observability import configure_logging, install_request_logging
from clinic.schema import apply_schema
from clinic.workflows.checkpointer import setup_checkpointer
from clinic.workflows.graph import build_graph

logger = logging.getLogger("clinic.startup")


def prepare_database(settings: Settings) -> bool:
    """Create the app tables and the checkpoint tables. True when done.

    Both steps are idempotent, so they run on every start. An unreachable database is logged and
    gives False, so the api still starts and /health can report it; any other error stops startup.
    """
    try:
        with psycopg.connect(settings.database_url, connect_timeout=CONNECT_TIMEOUT_S) as conn:
            apply_schema(conn)
        setup_checkpointer(settings.database_url)
    except psycopg.OperationalError as exc:
        logger.error(
            "startup.database_unavailable",
            extra={"fields": {"error": type(exc).__name__, "step": "schema_and_checkpointer"}},
        )
        return False
    return True


def create_app(settings: Settings) -> FastAPI:
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        prepare_database(settings)
        pool = make_pool(settings)
        checkpoint_pool = make_checkpoint_pool(settings)
        # Do not wait for the first connection: /health must be able to report a down database.
        pool.open(wait=False)
        checkpoint_pool.open(wait=False)
        app.state.deps = Deps(
            settings=settings,
            pool=pool,
            clock=SystemClock(),
            locks=ConversationLocks(),
            graph=build_graph(settings, PostgresSaver(checkpoint_pool)),
        )
        try:
            yield
        finally:
            checkpoint_pool.close(timeout=5)
            pool.close(timeout=5)

    app = FastAPI(title="Clinic Front-Desk Assistant", version=__version__, lifespan=lifespan)
    install_request_logging(app)
    install_error_handlers(app)
    app.include_router(health.router)
    return app

import logging
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import psycopg
import psycopg_pool
from fastapi import FastAPI
from psycopg_pool import ConnectionPool

from clinic import __version__
from clinic.api import approvals, conversations, emergencies, health
from clinic.clock import SystemClock
from clinic.config import Settings
from clinic.db import make_pool
from clinic.deps import Deps
from clinic.errors import install_error_handlers
from clinic.locks import ConversationLocks
from clinic.observability import configure_logging, install_request_logging
from clinic.rag.loader import KnowledgeLoader
from clinic.schema import apply_schema
from clinic.services.approval import ApprovalResumer, ApprovalService, UnavailableResumer
from clinic.services.conversation import ConversationWorkflow, UnavailableWorkflow

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


def keep_preparing(
    pool: ConnectionPool,
    settings: Settings,
    stop: threading.Event,
    on_ready: Callable[[], None],
) -> None:
    """Retry `prepare_database` until it succeeds or the app shuts down, then run `on_ready`.

    Only an unreachable database is retried. Any other error (missing docs directory, SQL bug)
    will not fix itself: it is logged with its traceback as a structured line and retrying stops,
    mirroring the first start, where the same errors stop startup.
    """
    while not stop.wait(PREPARE_RETRY_S):
        try:
            prepared = prepare_database(pool, settings)
            if prepared:
                on_ready()
        except Exception:
            logger.exception("startup.database_prepare_failed")
            return
        if prepared:
            logger.info("startup.database_prepared_after_retry")
            return


def create_app(
    settings: Settings,
    workflow: ConversationWorkflow | None = None,
    approval_resumer: ApprovalResumer | None = None,
) -> FastAPI:
    """`workflow` runs each conversation turn and `approval_resumer` resumes it after a staff
    decision. Until CFD-05 wires in its graph, both answer 503."""
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = make_pool(settings)
        # Do not wait for the first connection: /health must be able to report a down database.
        pool.open(wait=False)
        deps = Deps(
            settings=settings,
            pool=pool,
            clock=SystemClock(),
            locks=ConversationLocks(),
            workflow=workflow or UnavailableWorkflow(),
            approval_resumer=approval_resumer or UnavailableResumer(),
        )

        def on_ready() -> None:
            # Decisions stored before a crash but never applied are resumed now.
            ApprovalService(deps.pool, deps.clock, deps.locks, deps.approval_resumer).reconcile()

        stop_retry = threading.Event()
        retry: threading.Thread | None = None
        if prepare_database(pool, settings):
            on_ready()
        else:
            retry = threading.Thread(
                target=keep_preparing,
                args=(pool, settings, stop_retry, on_ready),
                name="prepare-database",
                daemon=True,
            )
            retry.start()
        app.state.deps = deps
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
    app.include_router(conversations.router)
    app.include_router(emergencies.router)
    app.include_router(approvals.router)
    return app

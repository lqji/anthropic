import logging
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI
from langgraph.checkpoint.postgres import PostgresSaver

from clinic import __version__
from clinic.api import appointments, approvals, conversations, emergencies, health
from clinic.clock import Clock, SystemClock
from clinic.config import Settings
from clinic.db import (
    CONNECT_TIMEOUT_S,
    SETUP_STATEMENT_TIMEOUT_MS,
    make_checkpoint_pool,
    make_pool,
)
from clinic.deps import Deps
from clinic.errors import install_error_handlers
from clinic.llm import make_llm
from clinic.locks import ConversationLocks
from clinic.observability import configure_logging, install_request_logging
from clinic.rag.loader import KnowledgeLoader
from clinic.schema import apply_schema
from clinic.seed import seed_appointments
from clinic.services.approval import ApprovalResumer, ApprovalService
from clinic.services.conversation import ConversationWorkflow
from clinic.services.graph_workflow import GraphWorkflow
from clinic.workflows.approval import GraphApprovalResumer
from clinic.workflows.checkpointer import setup_checkpointer
from clinic.workflows.deps import WorkflowDeps
from clinic.workflows.graph import build_graph

logger = logging.getLogger("clinic.startup")

PREPARE_RETRY_S = 5.0

# SQLSTATEs that mean "the server is going away or not ready yet", so trying again can help:
# admin_shutdown, crash_shutdown, cannot_connect_now (still starting up).
_RETRYABLE_SQLSTATES = frozenset({"57P01", "57P02", "57P03"})


def is_unreachable(exc: psycopg.OperationalError) -> bool:
    """True if the database could not be reached, False if it answered with an error.

    No SQLSTATE means the connection failed or dropped; class 08 is a connection exception. Any
    other operational error (e.g. a statement timeout during a migration) will not fix itself.
    """
    sqlstate = exc.sqlstate
    return sqlstate is None or sqlstate.startswith("08") or sqlstate in _RETRYABLE_SQLSTATES


def prepare_database(settings: Settings, clock: Clock) -> bool:
    """Create the app tables, load the clinic documents, seed the demo slots on first start and
    set up checkpointing.

    True when done. Every step is idempotent, so they run on every start. An unreachable database
    is logged and gives False, so the api still starts and /health can report it; any other error
    stops startup.
    """
    try:
        with psycopg.connect(
            settings.database_url,
            connect_timeout=CONNECT_TIMEOUT_S,
            options=f"-c statement_timeout={SETUP_STATEMENT_TIMEOUT_MS}",
        ) as conn:
            apply_schema(conn, settings.db_dir)
            KnowledgeLoader(settings.clinic_docs_dir).load(conn)
            seed_appointments(conn, settings.db_dir, settings.clinic_timezone, clock.now())
        setup_checkpointer(settings.database_url)
    except psycopg.OperationalError as exc:
        if not is_unreachable(exc):
            raise
        logger.error(
            "startup.database_unavailable",
            extra={
                "fields": {
                    "error": type(exc).__name__,
                    "detail": str(exc).strip(),
                    "step": "schema_documents_and_checkpointer",
                }
            },
        )
        return False
    return True


def keep_preparing(
    settings: Settings,
    clock: Clock,
    stop: threading.Event,
    on_ready: Callable[[], None] | None = None,
) -> None:
    """Retry `prepare_database` until it succeeds or the app shuts down, then run `on_ready`.

    Only an unreachable database is retried (see `is_unreachable`). Any other error (SQL bug,
    timed-out migration) will not fix itself: it is logged with its traceback and retrying stops,
    as it would stop a first start.
    """
    while not stop.wait(PREPARE_RETRY_S):
        try:
            prepared = prepare_database(settings, clock)
            if prepared and on_ready is not None:
                on_ready()
        except Exception:
            logger.exception("startup.database_prepare_failed")
            return
        if prepared:
            logger.info("startup.database_prepared_after_retry")
            return


def create_app(
    settings: Settings,
    *,
    clock: Clock | None = None,
    workflow: ConversationWorkflow | None = None,
    approval_resumer: ApprovalResumer | None = None,
) -> FastAPI:
    """`clock` defaults to the system clock; tests pass a FixedClock.

    Each conversation turn runs through the checkpointed graph. `workflow` replaces it, in tests
    that need a slow or failing turn. `approval_resumer` resumes a conversation paused for staff
    approval; it defaults to resuming the compiled graph.
    """
    configure_logging()
    app_clock = clock or SystemClock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        stop_retry = threading.Event()
        retry: threading.Thread | None = None
        pool = make_pool(settings)
        checkpoint_pool = make_checkpoint_pool(settings)
        # Everything below is inside the try, so a startup error (a missing docs directory, a
        # graph that cannot be built, ...) still stops the retry thread and closes both pools
        # instead of leaking their worker threads.
        try:
            prepared = prepare_database(settings, app_clock)
            # Do not wait for the first connection: /health must be able to report a down database.
            pool.open(wait=False)
            checkpoint_pool.open(wait=False)
            llm = make_llm(settings)
            graph = build_graph(
                settings,
                PostgresSaver(checkpoint_pool),
                WorkflowDeps(settings=settings, pool=pool, clock=app_clock, llm=llm),
            )
            app.state.deps = Deps(
                settings=settings,
                pool=pool,
                clock=app_clock,
                locks=ConversationLocks(),
                llm=llm,
                graph=graph,
                workflow=workflow or GraphWorkflow(graph, pool),
                approval_resumer=approval_resumer or GraphApprovalResumer(graph),
            )
            deps = app.state.deps

            def on_ready() -> None:
                # Decisions stored before a crash but never applied are resumed now. A database
                # error here is logged, not fatal: like an unreachable database at start, it must
                # not stop the api, and staff can finish a stuck decision by repeating it.
                try:
                    ApprovalService(
                        deps.pool, deps.clock, deps.locks, deps.approval_resumer
                    ).reconcile()
                except psycopg.Error as exc:
                    logger.error(
                        "startup.reconcile_failed",
                        extra={"fields": {"error": type(exc).__name__}},
                    )

            if prepared:
                on_ready()
            else:
                # The database may come up after the api: keep trying in the background.
                retry = threading.Thread(
                    target=keep_preparing,
                    args=(settings, app_clock, stop_retry, on_ready),
                    name="prepare-database",
                    daemon=True,
                )
                retry.start()
            yield
        finally:
            stop_retry.set()
            if retry is not None:
                retry.join(timeout=10)
            checkpoint_pool.close(timeout=5)
            pool.close(timeout=5)

    app = FastAPI(title="Clinic Front-Desk Assistant", version=__version__, lifespan=lifespan)
    install_request_logging(app)
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(appointments.router)
    app.include_router(conversations.router)
    app.include_router(emergencies.router)
    app.include_router(approvals.router)
    return app

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from clinic import __version__
from clinic.api import health
from clinic.clock import SystemClock
from clinic.config import Settings
from clinic.db import make_pool
from clinic.deps import Deps
from clinic.errors import install_error_handlers
from clinic.llm import make_llm
from clinic.locks import ConversationLocks
from clinic.observability import configure_logging, install_request_logging


def create_app(settings: Settings) -> FastAPI:
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = make_pool(settings)
        # Do not wait for the first connection: /health must be able to report a down database.
        pool.open(wait=False)
        app.state.deps = Deps(
            settings=settings,
            pool=pool,
            clock=SystemClock(),
            locks=ConversationLocks(),
            llm=make_llm(settings),
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

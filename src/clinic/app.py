import os

from fastapi import FastAPI

from clinic import __version__


def create_app() -> FastAPI:
    app = FastAPI(title="Clinic Front-Desk Assistant", version=__version__)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": os.environ.get("APP_VERSION", __version__)}

    return app

import logging
import time
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from clinic import __version__
from clinic.app import create_app
from clinic.config import Settings


def test_health_ok_with_database(make_settings: Callable[..., Settings], database_url: str) -> None:
    app = create_app(make_settings(database_url=database_url))
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["version"]


def test_health_reports_the_version_of_the_code_whatever_APP_VERSION_says(
    make_settings: Callable[..., Settings], database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_VERSION", "dev")  # a .env copied from an old .env.example
    app = create_app(make_settings(database_url=database_url))
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["version"] == __version__


def test_health_degraded_when_database_is_down(
    make_settings: Callable[..., Settings], caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(make_settings(db_pool_timeout_s=1))
    with TestClient(app) as client:
        started = time.perf_counter()
        with caplog.at_level(logging.ERROR):
            response = client.get("/health")
        elapsed = time.perf_counter() - started
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unreachable"}
    assert elapsed < 6
    assert [r for r in caplog.records if r.name == "clinic.db" and r.levelno == logging.ERROR]

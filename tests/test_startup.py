import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import Connection
from psycopg.rows import TupleRow
from psycopg_pool import ConnectionPool

from clinic import app as app_module
from clinic import db as db_module
from clinic.app import create_app
from clinic.clock import SystemClock
from clinic.config import Settings
from clinic.rag.loader import KnowledgeLoader, LoadReport
from clinic.schema import apply_schema, schema_files

SEED_DIR = Path(__file__).resolve().parents[1] / "seed" / "clinic_docs"


def test_schema_files_are_packaged_in_order() -> None:
    names = schema_files()
    assert "04_knowledge.sql" in names
    assert names == sorted(names)


def test_apply_schema_twice_is_harmless(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as conn:
        apply_schema(conn)
        apply_schema(conn)
        row = conn.execute("SELECT to_regclass('doc_chunks') IS NOT NULL").fetchone()
    assert row == (True,)


def test_startup_creates_tables_and_loads_documents(
    make_settings: Callable[..., Settings], database_url: str
) -> None:
    with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as conn:
        conn.execute("DROP TABLE IF EXISTS doc_chunks, documents")

    settings = make_settings(database_url=database_url, clinic_docs_dir=SEED_DIR)
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
    with TestClient(create_app(settings)):  # second start: nothing to do, no error
        pass

    with psycopg.connect(database_url, connect_timeout=5) as conn:
        row = conn.execute("SELECT count(*) FROM documents").fetchone()
    assert row == (4,)


def test_startup_with_database_down_logs_and_still_serves_health(
    make_settings: Callable[..., Settings], caplog: pytest.LogCaptureFixture
) -> None:
    settings = make_settings(db_pool_timeout_s=1, clinic_docs_dir=SEED_DIR)

    with caplog.at_level(logging.ERROR, logger="clinic.startup"):
        with TestClient(create_app(settings)) as client:
            response = client.get("/health")

    assert response.status_code == 503
    assert [r.getMessage() for r in caplog.records if r.name == "clinic.startup"] == [
        "startup.database_unavailable"
    ]


def test_database_back_after_failed_start_is_prepared_by_retry(
    make_settings: Callable[..., Settings],
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as conn:
        conn.execute("DROP TABLE IF EXISTS doc_chunks, documents")
    calls = 0

    def down_once(conn: Connection[TupleRow]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise psycopg.OperationalError("database is starting up")
        apply_schema(conn)

    monkeypatch.setattr(app_module, "apply_schema", down_once)
    monkeypatch.setattr(app_module, "PREPARE_RETRY_S", 0.05)
    settings = make_settings(database_url=database_url, clinic_docs_dir=SEED_DIR)

    with TestClient(create_app(settings)):
        deadline = time.monotonic() + 10
        loaded: tuple[object, ...] | None = None
        while time.monotonic() < deadline:
            with psycopg.connect(database_url, connect_timeout=5) as conn:
                try:
                    loaded = conn.execute("SELECT count(*) FROM documents").fetchone()
                except psycopg.errors.UndefinedTable:
                    loaded = None
            if loaded == (4,):
                break
            time.sleep(0.05)

    assert calls == 2
    assert loaded == (4,)


def test_retry_that_hits_another_error_logs_it_and_stops(
    make_settings: Callable[..., Settings],
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0

    def down_then_broken(conn: Connection[TupleRow]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise psycopg.OperationalError("database is starting up")
        raise ValueError("broken schema file")

    monkeypatch.setattr(app_module, "apply_schema", down_then_broken)
    monkeypatch.setattr(app_module, "PREPARE_RETRY_S", 0.05)
    settings = make_settings(database_url=database_url, clinic_docs_dir=SEED_DIR)

    with caplog.at_level(logging.ERROR, logger="clinic.startup"):
        with TestClient(create_app(settings)):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not _failed(caplog):
                time.sleep(0.05)
            time.sleep(0.2)  # a stopped retry makes no further calls

    failed = _failed(caplog)
    assert len(failed) == 1
    assert failed[0].exc_info is not None
    assert "broken schema file" in str(failed[0].exc_info[1])
    assert calls == 2


def _failed(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == "startup.database_prepare_failed"]


def test_missing_documents_directory_stops_startup(
    make_settings: Callable[..., Settings], database_url: str, tmp_path: Path
) -> None:
    settings = make_settings(database_url=database_url, clinic_docs_dir=tmp_path / "missing")

    with pytest.raises(FileNotFoundError, match="missing"):
        with TestClient(create_app(settings)):
            pass


def test_prepare_database_loads_the_documents_and_creates_the_checkpoint_tables(
    make_settings: Callable[..., Settings], database_url: str
) -> None:
    with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as conn:
        conn.execute("DROP TABLE IF EXISTS doc_chunks, documents")
    settings = make_settings(database_url=database_url, clinic_docs_dir=SEED_DIR)

    assert app_module.prepare_database(settings, SystemClock()) is True

    with psycopg.connect(database_url, connect_timeout=5) as conn:
        documents = conn.execute("SELECT count(*) FROM documents").fetchone()
        checkpoints = conn.execute("SELECT to_regclass('checkpoints') IS NOT NULL").fetchone()
    assert documents == (4,)  # the document load runs inside prepare_database...
    assert checkpoints == (True,)  # ...next to the checkpoint setup CFD-05 does


def test_prepare_database_loads_documents_after_the_schema(
    make_settings: Callable[..., Settings],
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    real_apply_schema = apply_schema
    real_load = KnowledgeLoader.load

    def recording_apply_schema(conn: Connection[TupleRow]) -> None:
        order.append("schema")
        real_apply_schema(conn)

    def recording_load(self: KnowledgeLoader, conn: Connection[TupleRow]) -> LoadReport:
        order.append("documents")
        return real_load(self, conn)

    monkeypatch.setattr(app_module, "apply_schema", recording_apply_schema)
    monkeypatch.setattr(KnowledgeLoader, "load", recording_load)

    app_module.prepare_database(
        make_settings(database_url=database_url, clinic_docs_dir=SEED_DIR), SystemClock()
    )

    assert order == ["schema", "documents"]


def test_prepare_database_does_not_swallow_a_missing_documents_directory(
    make_settings: Callable[..., Settings], database_url: str, tmp_path: Path
) -> None:
    # Only an unreachable database is "retry later". A broken setup must stop startup.
    settings = make_settings(database_url=database_url, clinic_docs_dir=tmp_path / "missing")

    with pytest.raises(FileNotFoundError, match="missing"):
        app_module.prepare_database(settings, SystemClock())


@pytest.mark.parametrize("failure", ["missing_docs_dir", "graph_build_fails", "database_down"])
def test_startup_failure_closes_both_pools_and_stops_the_retry_thread(
    make_settings: Callable[..., Settings],
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    pools: list[ConnectionPool[Any]] = []

    def recording(
        make: Callable[[Settings], ConnectionPool[Any]],
    ) -> Callable[[Settings], ConnectionPool[Any]]:
        def build(settings: Settings) -> ConnectionPool[Any]:
            pool = make(settings)
            pools.append(pool)
            return pool

        return build

    # The names app.py uses: the app pool and the checkpoint pool.
    monkeypatch.setattr(app_module, "make_pool", recording(db_module.make_pool))
    monkeypatch.setattr(
        app_module, "make_checkpoint_pool", recording(db_module.make_checkpoint_pool)
    )

    def cannot_build(*args: object, **kwargs: object) -> None:
        raise RuntimeError("cannot build the graph")

    expected: type[Exception]
    if failure == "missing_docs_dir":  # fails before the pools are opened
        settings = make_settings(database_url=database_url, clinic_docs_dir=tmp_path / "missing")
        expected = FileNotFoundError
    elif failure == "graph_build_fails":  # fails after both pools are open: the leak
        monkeypatch.setattr(app_module, "build_graph", cannot_build)
        settings = make_settings(database_url=database_url, clinic_docs_dir=SEED_DIR)
        expected = RuntimeError
    else:  # the database is down, so the retry thread is running when the graph build fails
        monkeypatch.setattr(app_module, "build_graph", cannot_build)
        settings = make_settings(db_pool_timeout_s=1, clinic_docs_dir=SEED_DIR)
        expected = RuntimeError

    with pytest.raises(expected):
        with TestClient(create_app(settings)):
            pass

    assert len(pools) == 2  # the app pool and the checkpoint pool
    assert all(pool.closed for pool in pools)  # an error at startup must not leak their threads
    assert not [t for t in threading.enumerate() if t.name == "prepare-database"]

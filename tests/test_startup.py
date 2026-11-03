import logging
from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from clinic.app import create_app
from clinic.config import Settings
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


def test_missing_documents_directory_stops_startup(
    make_settings: Callable[..., Settings], database_url: str, tmp_path: Path
) -> None:
    settings = make_settings(database_url=database_url, clinic_docs_dir=tmp_path / "missing")

    with pytest.raises(FileNotFoundError, match="missing"):
        with TestClient(create_app(settings)):
            pass

import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from clinic.clock import FixedClock
from clinic.config import Settings
from clinic.llm import FakeLLM

UNREACHABLE_DB_URL = "postgresql://nobody:nothing@127.0.0.1:1/none"


@pytest.fixture(scope="session")
def database_url() -> str:
    """Connection string of the PostgreSQL used by data tests (CI service or compose.dev.yaml)."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is not set; start the database with compose.dev.yaml")
    return url


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    """Build Settings for tests: fake LLM, explicit values win over the environment."""

    def build(**overrides: Any) -> Settings:
        values: dict[str, Any] = {"llm_provider": "fake", "database_url": UNREACHABLE_DB_URL}
        values.update(overrides)
        return Settings(**values)

    return build


@pytest.fixture
def fixed_clock() -> FixedClock:
    """Monday 2026-11-02, 12:00 at the clinic (Asia/Muscat, UTC+4)."""
    return FixedClock(datetime(2026, 11, 2, 8, 0, tzinfo=UTC))


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()

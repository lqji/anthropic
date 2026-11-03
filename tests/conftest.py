import os
from collections.abc import Callable
from typing import Any

import pytest

from clinic.config import Settings

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

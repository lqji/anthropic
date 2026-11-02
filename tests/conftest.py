import os

import pytest


@pytest.fixture(scope="session")
def database_url() -> str:
    """Connection string of the PostgreSQL used by data tests (CI service or compose.dev.yaml)."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is not set; start the database with compose.dev.yaml")
    return url

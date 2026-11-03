import subprocess
import sys

import pytest

from clinic.config import ConfigError, load_settings


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DATABASE_URL", "LLM_PROVIDER", "OPENAI_API_KEY", "CLINIC_TIMEZONE"):
        monkeypatch.delenv(name, raising=False)


def test_missing_database_url_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    with pytest.raises(ConfigError) as caught:
        load_settings()
    assert str(caught.value) == (
        "Configuration error: missing or invalid environment variables: DATABASE_URL"
    )


def test_openai_provider_without_key_names_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(ConfigError) as caught:
        load_settings()
    assert "OPENAI_API_KEY" in str(caught.value)


def test_blank_key_counts_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    with pytest.raises(ConfigError):
        load_settings()


def test_fake_provider_needs_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    assert load_settings().openai_api_key is None


def test_unrelated_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("POSTGRES_USER", "clinic")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    assert load_settings().database_url.startswith("postgresql://")


def test_invalid_timezone_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("CLINIC_TIMEZONE", "Mars/Olympus")
    with pytest.raises(ConfigError) as caught:
        load_settings()
    assert "CLINIC_TIMEZONE" in str(caught.value)


def test_service_exits_without_traceback_when_config_is_missing() -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", "import clinic.main"],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert (
        "Configuration error: missing or invalid environment variables: DATABASE_URL"
        in result.stderr
    )
    assert "Traceback" not in result.stderr

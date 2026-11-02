from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from clinic import __version__


class ConfigError(Exception):
    """Raised at startup when the environment is missing or has invalid settings."""

    def __init__(self, names: list[str]) -> None:
        self.names = sorted(set(names))
        super().__init__(
            "Configuration error: missing or invalid environment variables: "
            + ", ".join(self.names)
        )


class Settings(BaseSettings):
    """All settings come from environment variables; unknown variables are ignored."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    database_url: str
    db_pool_max_size: int = Field(default=20, ge=2)
    db_pool_timeout_s: float = Field(default=5.0, gt=0)

    llm_provider: Literal["openai", "fake"] = "openai"
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"
    llm_timeout_s: float = Field(default=20.0, gt=0)
    llm_max_retries: int = Field(default=1, ge=0)

    clinic_timezone: str = "Asia/Muscat"
    emergency_number: str = "9999"
    history_window: int = Field(default=10, ge=1)
    intent_min_confidence: float = Field(default=0.6, ge=0, le=1)
    rag_min_score: float = Field(default=0.30, ge=0)
    rag_top_k: int = Field(default=3, ge=1)

    app_version: str = __version__

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def _blank_key_is_missing(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("clinic_timezone")
    @classmethod
    def _timezone_exists(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {value!r}") from exc
        return value


def load_settings() -> Settings:
    """Read settings from the environment or raise ConfigError naming every bad variable."""
    try:
        settings = Settings()
    except ValidationError as exc:
        raise ConfigError(
            [str(err["loc"][0]).upper() for err in exc.errors() if err["loc"]]
        ) from None
    if settings.llm_provider == "openai" and settings.openai_api_key is None:
        raise ConfigError(["OPENAI_API_KEY"])
    return settings

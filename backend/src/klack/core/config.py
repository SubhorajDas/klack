"""Validated application configuration.

Settings are instantiated by the application factory rather than at import time. This keeps
imports side-effect free and allows tests and alternate process types to inject configuration.
"""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class AppEnvironment(StrEnum):
    """Named deployment environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class LogFormat(StrEnum):
    """Supported log rendering formats."""

    CONSOLE = "console"
    JSON = "json"


class LogLevel(StrEnum):
    """Supported application log levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveFloat = Annotated[float, Field(gt=0)]


class Settings(BaseSettings):
    """Application settings loaded from environment variables and an optional local `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
        validate_default=True,
    )

    app_env: AppEnvironment
    app_name: str = "klack-api"
    app_version: str = "0.1.0"
    app_debug: bool = False
    api_v1_prefix: str = "/api/v1"

    log_level: LogLevel = LogLevel.INFO
    log_format: LogFormat = LogFormat.CONSOLE

    database_url: SecretStr
    db_pool_size: PositiveInt = 5
    db_max_overflow: NonNegativeInt = 5
    db_pool_timeout_seconds: PositiveFloat = 5.0
    db_pool_recycle_seconds: PositiveInt = 1_800
    db_connect_timeout_seconds: PositiveFloat = 5.0
    db_statement_timeout_ms: PositiveInt = 10_000
    db_idle_in_transaction_timeout_ms: PositiveInt = 30_000

    healthcheck_timeout_seconds: PositiveFloat = 2.0

    @field_validator("app_name", "app_version")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        """Reject configuration values that are present but empty."""
        if not value.strip():
            msg = "must not be blank"
            raise ValueError(msg)
        return value

    @field_validator("api_v1_prefix")
    @classmethod
    def validate_api_prefix(cls, value: str) -> str:
        """Require a normalized absolute API prefix."""
        if not value.startswith("/") or value == "/" or value.endswith("/"):
            msg = "must start with '/', must not be '/', and must not end with '/'"
            raise ValueError(msg)
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        """Accept only the selected async PostgreSQL SQLAlchemy driver."""
        raw_url = value.get_secret_value()
        if not raw_url.strip():
            msg = "must not be empty"
            raise ValueError(msg)

        try:
            url = make_url(raw_url)
        except ArgumentError as exc:
            msg = "must be a valid SQLAlchemy database URL"
            raise ValueError(msg) from exc

        if url.drivername != "postgresql+asyncpg":
            msg = "must use the postgresql+asyncpg driver"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def reject_unsafe_production_debug(self) -> Self:
        """Prevent unsafe debug output and non-structured production logs."""
        if self.app_env is AppEnvironment.PRODUCTION:
            if self.app_debug:
                msg = "APP_DEBUG must be false in production"
                raise ValueError(msg)
            if self.log_format is not LogFormat.JSON:
                msg = "LOG_FORMAT must be json in production"
                raise ValueError(msg)
        return self

    def database_url_value(self) -> str:
        """Return the database URL only at the infrastructure boundary."""
        return self.database_url.get_secret_value()

    def database_connect_args(self) -> dict[str, object]:
        """Build asyncpg connection safeguards for SQLAlchemy."""
        return {
            "timeout": self.db_connect_timeout_seconds,
            "server_settings": {
                "statement_timeout": str(self.db_statement_timeout_ms),
                "idle_in_transaction_session_timeout": str(self.db_idle_in_transaction_timeout_ms),
            },
        }

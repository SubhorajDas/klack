"""Validated application configuration.

Settings are instantiated by the application factory rather than at import time. This keeps
imports side-effect free and allows tests and alternate process types to inject configuration.
"""

from enum import StrEnum
from ipaddress import ip_address
from typing import Annotated, Self
from urllib.parse import urlsplit

import idna
from pydantic import EmailStr, Field, SecretStr, field_validator, model_validator
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
SecretLength = Annotated[SecretStr, Field(min_length=32)]
MAX_REFRESH_TOKEN_ROWS_PER_SESSION = 10_000


class DatabaseSettings(BaseSettings):
    """Migration-safe database configuration without unrelated runtime secrets."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
        validate_default=True,
    )

    database_url: SecretStr

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

    def database_url_value(self) -> str:
        """Return the database URL only at the infrastructure boundary."""
        return self.database_url.get_secret_value()


class Settings(DatabaseSettings):
    """Application settings loaded from environment variables and an optional local `.env`."""

    app_env: AppEnvironment
    app_name: str = "klack-api"
    app_version: str = "0.1.0"
    app_debug: bool = False
    api_v1_prefix: str = "/api/v1"

    log_level: LogLevel = LogLevel.INFO
    log_format: LogFormat = LogFormat.CONSOLE

    db_pool_size: PositiveInt = 5
    db_max_overflow: NonNegativeInt = 5
    db_pool_timeout_seconds: PositiveFloat = 5.0
    db_pool_recycle_seconds: PositiveInt = 1_800
    db_connect_timeout_seconds: PositiveFloat = 5.0
    db_statement_timeout_ms: PositiveInt = 10_000
    db_idle_in_transaction_timeout_ms: PositiveInt = 30_000

    healthcheck_timeout_seconds: PositiveFloat = 2.0

    auth_jwt_secret: SecretLength
    auth_refresh_secret: SecretLength
    auth_action_secret: SecretLength
    workspace_invitation_secret: SecretLength = SecretStr(
        "development-workspace-invitation-secret-change-me-00004",
    )
    auth_issuer: str = "klack-api"
    auth_audience: str = "klack-web"
    auth_access_ttl_seconds: Annotated[int, Field(ge=60, le=3_600)] = 900
    auth_refresh_ttl_seconds: Annotated[int, Field(ge=3_600, le=7_776_000)] = 2_592_000
    auth_refresh_min_interval_seconds: Annotated[int, Field(ge=30, le=900)] = 300
    auth_trusted_origin: str
    auth_public_web_origin: str
    auth_cookie_secure: bool = True
    auth_password_max_concurrency: Annotated[int, Field(ge=1, le=8)] = 2
    auth_max_active_sessions: Annotated[int, Field(ge=1, le=100)] = 10
    auth_email_verification_ttl_seconds: Annotated[int, Field(ge=900, le=604_800)] = 86_400
    auth_password_recovery_ttl_seconds: Annotated[int, Field(ge=300, le=86_400)] = 3_600
    auth_rate_limit_window_seconds: Annotated[int, Field(ge=60, le=86_400)] = 900
    auth_rate_limit_block_seconds: Annotated[int, Field(ge=60, le=86_400)] = 900
    auth_registration_rate_limit: Annotated[int, Field(ge=1, le=100)] = 5
    auth_login_rate_limit: Annotated[int, Field(ge=1, le=100)] = 10
    auth_email_action_rate_limit: Annotated[int, Field(ge=1, le=100)] = 5
    auth_action_complete_rate_limit: Annotated[int, Field(ge=1, le=100)] = 10
    workspace_invitation_ttl_seconds: Annotated[int, Field(ge=900, le=2_592_000)] = 604_800

    smtp_host: str | None = None
    smtp_port: Annotated[int, Field(ge=1, le=65_535)] = 587
    smtp_starttls: bool = True
    smtp_use_ssl: bool = False
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_timeout_seconds: PositiveFloat = 10.0
    email_from_address: EmailStr | None = None
    identity_outbox_batch_size: Annotated[int, Field(ge=1, le=1_000)] = 50
    identity_outbox_lease_seconds: Annotated[int, Field(ge=30, le=3_600)] = 300
    identity_cleanup_retention_seconds: Annotated[int, Field(ge=0, le=31_536_000)] = 604_800
    identity_cleanup_batch_size: Annotated[int, Field(ge=1, le=10_000)] = 1_000
    identity_worker_poll_seconds: Annotated[int, Field(ge=1, le=3_600)] = 10
    identity_cleanup_interval_seconds: Annotated[int, Field(ge=60, le=604_800)] = 3_600

    @field_validator(
        "smtp_host",
        "smtp_username",
        "smtp_password",
        "email_from_address",
        mode="before",
    )
    @classmethod
    def normalize_optional_email_configuration(cls, value: object) -> object | None:
        """Treat empty optional worker environment variables as unconfigured."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("app_name", "app_version", "auth_issuer", "auth_audience")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        """Reject configuration values that are present but empty."""
        if not value.strip():
            msg = "must not be blank"
            raise ValueError(msg)
        return value

    @field_validator("auth_trusted_origin", "auth_public_web_origin")
    @classmethod
    def validate_auth_trusted_origin(cls, value: str) -> str:
        """Accept one exact HTTP(S) origin without credentials, paths, or query data."""
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            msg = "must be a valid HTTP(S) origin"
            raise ValueError(msg) from exc

        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            msg = "must be an HTTP(S) origin without credentials, path, query, or fragment"
            raise ValueError(msg)

        try:
            parsed_ip = ip_address(parsed.hostname)
        except ValueError:
            try:
                host = idna.encode(
                    parsed.hostname,
                    uts46=True,
                    std3_rules=True,
                ).decode("ascii")
            except idna.IDNAError as exc:
                msg = "must contain a valid hostname"
                raise ValueError(msg) from exc
        else:
            host = parsed_ip.compressed
            if parsed_ip.version == 6:
                host = f"[{host}]"
        scheme = parsed.scheme.lower()
        default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        authority = f"{host}:{port}" if port is not None and not default_port else host
        return f"{scheme}://{authority}"

    @field_validator("api_v1_prefix")
    @classmethod
    def validate_api_prefix(cls, value: str) -> str:
        """Require a normalized absolute API prefix."""
        if not value.startswith("/") or value == "/" or value.endswith("/"):
            msg = "must start with '/', must not be '/', and must not end with '/'"
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
        if self.app_env in {AppEnvironment.STAGING, AppEnvironment.PRODUCTION}:
            if not self.auth_cookie_secure:
                msg = "AUTH_COOKIE_SECURE must be true in staging and production"
                raise ValueError(msg)
            if not self.auth_trusted_origin.startswith("https://"):
                msg = "AUTH_TRUSTED_ORIGIN must use https in staging and production"
                raise ValueError(msg)
            if not self.auth_public_web_origin.startswith("https://"):
                msg = "AUTH_PUBLIC_WEB_ORIGIN must use https in staging and production"
                raise ValueError(msg)
            if any(
                "change-me" in secret.get_secret_value().casefold()
                for secret in (
                    self.auth_jwt_secret,
                    self.auth_refresh_secret,
                    self.auth_action_secret,
                    self.workspace_invitation_secret,
                )
            ):
                msg = "application secrets must not use checked-in example values"
                raise ValueError(msg)
        application_secrets = {
            self.auth_jwt_secret.get_secret_value(),
            self.auth_refresh_secret.get_secret_value(),
            self.auth_action_secret.get_secret_value(),
            self.workspace_invitation_secret.get_secret_value(),
        }
        if len(application_secrets) != 4:
            msg = (
                "AUTH_JWT_SECRET, AUTH_REFRESH_SECRET, AUTH_ACTION_SECRET, and "
                "WORKSPACE_INVITATION_SECRET must be different"
            )
            raise ValueError(msg)
        if self.smtp_starttls and self.smtp_use_ssl:
            msg = "SMTP_STARTTLS and SMTP_USE_SSL cannot both be true"
            raise ValueError(msg)
        if (self.smtp_username is None) != (self.smtp_password is None):
            msg = "SMTP_USERNAME and SMTP_PASSWORD must be configured together"
            raise ValueError(msg)
        if (self.smtp_host is None) != (self.email_from_address is None):
            msg = "SMTP_HOST and EMAIL_FROM_ADDRESS must be configured together"
            raise ValueError(msg)
        if self.auth_refresh_min_interval_seconds > self.auth_access_ttl_seconds:
            msg = "AUTH_REFRESH_MIN_INTERVAL_SECONDS must not exceed AUTH_ACCESS_TTL_SECONDS"
            raise ValueError(msg)
        retained_row_bound = (
            self.auth_refresh_ttl_seconds + self.auth_refresh_min_interval_seconds - 1
        ) // self.auth_refresh_min_interval_seconds
        if retained_row_bound > MAX_REFRESH_TOKEN_ROWS_PER_SESSION:
            msg = "configured refresh lifetime permits more than 10000 retained rows per session"
            raise ValueError(msg)
        return self

    def database_connect_args(self) -> dict[str, object]:
        """Build asyncpg connection safeguards for SQLAlchemy."""
        return {
            "timeout": self.db_connect_timeout_seconds,
            "server_settings": {
                "statement_timeout": str(self.db_statement_timeout_ms),
                "idle_in_transaction_session_timeout": str(self.db_idle_in_transaction_timeout_ms),
            },
        }

    def auth_jwt_secret_value(self) -> str:
        """Return JWT key material only at the token-signing boundary."""
        return self.auth_jwt_secret.get_secret_value()

    def auth_refresh_secret_value(self) -> str:
        """Return refresh/CSRF HMAC key material only at the token boundary."""
        return self.auth_refresh_secret.get_secret_value()

    def auth_action_secret_value(self) -> str:
        """Return email-action/outbox key material only at its security boundary."""
        return self.auth_action_secret.get_secret_value()

    def workspace_invitation_secret_value(self) -> str:
        """Return invitation-token key material only at its security boundary."""
        return self.workspace_invitation_secret.get_secret_value()

    def smtp_password_value(self) -> str | None:
        """Return the SMTP password only at the email-delivery boundary."""
        if self.smtp_password is None:
            return None
        return self.smtp_password.get_secret_value()

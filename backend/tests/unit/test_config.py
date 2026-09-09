"""Configuration validation contracts."""

import pytest
from pydantic import ValidationError

from klack.core.config import AppEnvironment, DatabaseSettings, LogFormat, Settings


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "app_env": AppEnvironment.TEST,
        "database_url": "postgresql+asyncpg://klack:very-secret@db:5432/klack",
        "auth_jwt_secret": "test-jwt-secret-at-least-thirty-two-bytes",
        "auth_refresh_secret": "test-refresh-secret-at-least-thirty-two-bytes",
        "auth_action_secret": "test-action-secret-at-least-thirty-two-bytes",
        "auth_trusted_origin": "http://test",
        "auth_public_web_origin": "http://test",
        "auth_cookie_secure": True,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_settings_load_typed_values() -> None:
    settings = make_settings(
        app_name="klack-test",
        log_format=LogFormat.JSON,
        db_pool_size=8,
        db_max_overflow=0,
    )

    assert settings.app_env is AppEnvironment.TEST
    assert settings.app_name == "klack-test"
    assert settings.log_format is LogFormat.JSON
    assert settings.db_pool_size == 8
    assert settings.db_max_overflow == 0


@pytest.mark.parametrize(
    "database_url",
    [
        "",
        "sqlite+aiosqlite:///test.db",
        "postgresql://klack:secret@db/klack",
        "not a database url",
    ],
)
def test_settings_reject_non_async_postgresql_urls(database_url: str) -> None:
    with pytest.raises(ValidationError, match=r"postgresql\+asyncpg|valid SQLAlchemy|empty"):
        make_settings(database_url=database_url)


def test_settings_validation_errors_redact_invalid_database_url() -> None:
    secret = "must-not-appear"

    with pytest.raises(ValidationError) as exc_info:
        make_settings(database_url=f"postgresql://klack:{secret}@db/klack")

    error_text = str(exc_info.value)
    assert secret not in error_text
    assert "input_value" not in error_text


def test_database_settings_require_only_a_valid_database_url() -> None:
    settings = DatabaseSettings(
        _env_file=None,
        database_url="postgresql+asyncpg://klack:very-secret@db:5432/klack",
    )

    assert settings.database_url_value().endswith("@db:5432/klack")


def test_settings_require_environment_and_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AUTH_JWT_SECRET", raising=False)
    monkeypatch.delenv("AUTH_REFRESH_SECRET", raising=False)
    monkeypatch.delenv("AUTH_ACTION_SECRET", raising=False)
    monkeypatch.delenv("AUTH_TRUSTED_ORIGIN", raising=False)
    monkeypatch.delenv("AUTH_PUBLIC_WEB_ORIGIN", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)  # type: ignore[call-arg]

    errors = {error["loc"][0] for error in exc_info.value.errors()}
    assert errors == {
        "app_env",
        "database_url",
        "auth_jwt_secret",
        "auth_refresh_secret",
        "auth_action_secret",
        "auth_trusted_origin",
        "auth_public_web_origin",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("db_pool_size", 0),
        ("db_max_overflow", -1),
        ("db_pool_timeout_seconds", 0),
        ("healthcheck_timeout_seconds", -1),
    ],
)
def test_settings_reject_invalid_positive_bounds(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{field: value})


def test_settings_reject_unsafe_production_debug() -> None:
    with pytest.raises(ValidationError, match="APP_DEBUG must be false"):
        make_settings(
            app_env=AppEnvironment.PRODUCTION,
            app_debug=True,
            log_format=LogFormat.JSON,
        )


def test_settings_require_structured_production_logs() -> None:
    with pytest.raises(ValidationError, match="LOG_FORMAT must be json"):
        make_settings(app_env=AppEnvironment.PRODUCTION, log_format=LogFormat.CONSOLE)


@pytest.mark.parametrize(
    ("origin", "canonical"),
    [
        ("https://BÜCHER.example:443/", "https://xn--bcher-kva.example"),
        ("https://faß.de", "https://xn--fa-hia.de"),
        ("http://127.0.0.1:80", "http://127.0.0.1"),
        ("http://[2001:0db8::1]:80", "http://[2001:db8::1]"),
    ],
)
def test_settings_canonicalize_browser_origins(origin: str, canonical: str) -> None:
    assert make_settings(auth_trusted_origin=origin).auth_trusted_origin == canonical


@pytest.mark.parametrize("app_env", [AppEnvironment.STAGING, AppEnvironment.PRODUCTION])
@pytest.mark.parametrize("field", ["auth_jwt_secret", "auth_refresh_secret", "auth_action_secret"])
def test_deployed_settings_reject_checked_in_secret_markers(
    app_env: AppEnvironment,
    field: str,
) -> None:
    overrides: dict[str, object] = {
        "app_env": app_env,
        "auth_trusted_origin": "https://app.example",
        "auth_public_web_origin": "https://app.example",
        "log_format": LogFormat.JSON,
        field: "known-change-me-secret-that-is-long-enough",
    }

    with pytest.raises(ValidationError, match="checked-in example"):
        make_settings(**overrides)


def test_development_settings_allow_documented_example_secrets() -> None:
    settings = make_settings(
        app_env=AppEnvironment.DEVELOPMENT,
        auth_jwt_secret="development-jwt-secret-change-me-00000001",
        auth_refresh_secret="development-refresh-secret-change-me-00002",
        auth_action_secret="development-action-secret-change-me-0000003",
        auth_trusted_origin="http://127.0.0.1:8000",
        auth_public_web_origin="http://127.0.0.1:3000",
        auth_cookie_secure=False,
    )

    assert settings.app_env is AppEnvironment.DEVELOPMENT


def test_settings_require_distinct_auth_secrets() -> None:
    shared = "shared-auth-secret-at-least-thirty-two-bytes"
    with pytest.raises(ValidationError, match="must be different"):
        make_settings(auth_jwt_secret=shared, auth_refresh_secret=shared)


def test_settings_bound_refresh_rotation_to_access_lifetime() -> None:
    with pytest.raises(ValidationError, match="must not exceed"):
        make_settings(
            auth_access_ttl_seconds=60,
            auth_refresh_min_interval_seconds=61,
        )


def test_settings_bound_retained_refresh_rows_per_session() -> None:
    with pytest.raises(ValidationError, match="more than 10000 retained rows"):
        make_settings(
            auth_refresh_ttl_seconds=7_776_000,
            auth_refresh_min_interval_seconds=30,
        )


@pytest.mark.parametrize("prefix", ["api/v1", "/", "/api/v1/"])
def test_settings_reject_non_normalized_api_prefix(prefix: str) -> None:
    with pytest.raises(ValidationError, match="must start with"):
        make_settings(api_v1_prefix=prefix)


def test_database_secret_is_redacted() -> None:
    settings = make_settings()

    assert "very-secret" not in repr(settings)
    assert "very-secret" not in str(settings.model_dump())
    assert "very-secret" in settings.database_url_value()


def test_database_connect_args_include_safeguards() -> None:
    settings = make_settings(
        db_connect_timeout_seconds=3.5,
        db_statement_timeout_ms=4_000,
        db_idle_in_transaction_timeout_ms=8_000,
    )

    assert settings.database_connect_args() == {
        "timeout": 3.5,
        "server_settings": {
            "statement_timeout": "4000",
            "idle_in_transaction_session_timeout": "8000",
        },
    }


def test_settings_are_frozen() -> None:
    settings = make_settings()

    with pytest.raises(ValidationError, match="frozen"):
        settings.app_name = "changed"  # type: ignore[misc]


def test_smtp_configuration_is_optional_but_coherent() -> None:
    disabled = make_settings(
        smtp_host="",
        smtp_username="",
        smtp_password="",
        email_from_address="",
    )
    assert disabled.smtp_host is None
    assert disabled.email_from_address is None

    with pytest.raises(ValidationError, match="SMTP_HOST and EMAIL_FROM_ADDRESS"):
        make_settings(smtp_host="smtp.example")
    with pytest.raises(ValidationError, match="configured together"):
        make_settings(smtp_username="mailer")
    with pytest.raises(ValidationError, match="cannot both be true"):
        make_settings(smtp_starttls=True, smtp_use_ssl=True)


def test_smtp_password_is_redacted_and_available_only_at_boundary() -> None:
    settings = make_settings(
        smtp_host="smtp.example",
        email_from_address="noreply@example.com",
        smtp_username="mailer",
        smtp_password="smtp-password-secret",
    )

    assert "smtp-password-secret" not in repr(settings)
    assert settings.smtp_password_value() == "smtp-password-secret"

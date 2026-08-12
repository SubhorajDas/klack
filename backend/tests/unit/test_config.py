"""Configuration validation contracts."""

import pytest
from pydantic import ValidationError

from klack.core.config import AppEnvironment, LogFormat, Settings


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "app_env": AppEnvironment.TEST,
        "database_url": "postgresql+asyncpg://klack:very-secret@db:5432/klack",
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


def test_settings_require_environment_and_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)  # type: ignore[call-arg]

    errors = {error["loc"][0] for error in exc_info.value.errors()}
    assert errors == {"app_env", "database_url"}


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

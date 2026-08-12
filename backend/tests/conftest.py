"""Shared test fixtures for the backend foundation."""

from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from klack.bootstrap import create_app
from klack.core.config import AppEnvironment, LogFormat, Settings
from klack.core.db.session import DatabaseHealthCheck


@pytest.fixture
def settings() -> Settings:
    """Return isolated test settings without consulting dotenv files."""
    return Settings(
        _env_file=None,
        app_env=AppEnvironment.TEST,
        app_name="klack-api-test",
        app_version="test-release",
        log_format=LogFormat.JSON,
        database_url="postgresql+asyncpg://klack:secret@127.0.0.1:5432/klack_test",
    )


@pytest.fixture
def healthy_database_check() -> DatabaseHealthCheck:
    async def check_database() -> None:
        return None

    return check_database


@pytest.fixture
def app(settings: Settings, healthy_database_check: DatabaseHealthCheck) -> Iterator[FastAPI]:
    yield create_app(settings, database_health_check=healthy_database_check)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Run ASGI lifespan explicitly; HTTPX's transport intentionally does not."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://test") as test_client,
    ):
        yield test_client

"""PostgreSQL integration checks for the async infrastructure."""

import os

import pytest
from sqlalchemy import text

from klack.core.config import AppEnvironment, Settings
from klack.core.db.session import create_engine

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1",
    reason="set RUN_INTEGRATION_TESTS=1 with a real PostgreSQL DATABASE_URL",
)
async def test_real_postgresql_connection() -> None:
    settings = Settings(_env_file=None, app_env=AppEnvironment.TEST)  # type: ignore[call-arg]
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            result = await connection.scalar(text("SELECT 1"))
        assert result == 1
    finally:
        await engine.dispose()

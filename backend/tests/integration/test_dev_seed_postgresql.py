"""PostgreSQL concurrency checks for the explicit development fixture command."""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import delete, select

from klack.core.config import AppEnvironment
from klack.core.container import AppContainer, build_container
from klack.dev_seed import (
    DEMO_ACCOUNTS,
    DEMO_WORKSPACE_ID,
    DevelopmentSeedSettings,
    seed_development_data,
)
from klack.modules.identity.infrastructure.models import (
    PasswordCredentialRecord,
    UserRecord,
)
from klack.modules.workspaces.infrastructure.models import MembershipRecord, WorkspaceRecord

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 with a migrated PostgreSQL test database",
    ),
]

PASSWORD = "Swagger fixture password"


def _settings() -> DevelopmentSeedSettings:
    return DevelopmentSeedSettings(  # type: ignore[call-arg]
        _env_file=None,
        app_env=AppEnvironment.DEVELOPMENT,
        database_url=SecretStr(os.environ["DATABASE_URL"]),
        auth_jwt_secret=SecretStr("integration-seed-jwt-secret-at-least-thirty-two-bytes"),
        auth_refresh_secret=SecretStr(
            "integration-seed-refresh-secret-at-least-thirty-two-bytes",
        ),
        auth_action_secret=SecretStr("integration-seed-action-secret-at-least-thirty-two-bytes"),
        workspace_invitation_secret=SecretStr(
            "integration-seed-workspace-secret-at-least-thirty-two-bytes",
        ),
        auth_trusted_origin="http://test",
        auth_public_web_origin="http://test",
        auth_cookie_secure=False,
        dev_seed_enabled=True,
        dev_seed_password=SecretStr(PASSWORD),
    )


@pytest.fixture(scope="module")
def migrated_schema() -> None:
    backend_root = Path(__file__).parents[2]
    command.upgrade(Config(backend_root / "alembic.ini"), "head")


@pytest.fixture
async def seed_container(migrated_schema: None) -> AsyncIterator[AppContainer]:
    del migrated_schema
    container = build_container(_settings())
    try:
        yield container
    finally:
        await container.engine.dispose()


async def _cleanup(container: AppContainer) -> None:
    async with container.session_factory() as session:
        await session.execute(
            delete(WorkspaceRecord).where(WorkspaceRecord.id == DEMO_WORKSPACE_ID)
        )
        await session.execute(
            delete(UserRecord).where(
                UserRecord.id.in_([account.id for account in DEMO_ACCOUNTS]),
            ),
        )
        await session.commit()


async def _seed(container: AppContainer) -> None:
    async with container.session_factory() as session:
        await seed_development_data(
            session,
            password=PASSWORD,
            password_manager=container.password_manager,
        )


async def test_concurrent_seed_runs_create_one_valid_fixture(
    seed_container: AppContainer,
) -> None:
    await _cleanup(seed_container)
    try:
        await asyncio.gather(_seed(seed_container), _seed(seed_container))

        async with seed_container.session_factory() as session:
            users = (
                await session.scalars(
                    select(UserRecord)
                    .where(UserRecord.id.in_([account.id for account in DEMO_ACCOUNTS]))
                    .order_by(UserRecord.id),
                )
            ).all()
            credentials = (
                await session.scalars(
                    select(PasswordCredentialRecord).order_by(PasswordCredentialRecord.user_id),
                )
            ).all()
            workspace = await session.get(WorkspaceRecord, DEMO_WORKSPACE_ID)
            memberships = (
                await session.scalars(
                    select(MembershipRecord).where(
                        MembershipRecord.workspace_id == DEMO_WORKSPACE_ID,
                    ),
                )
            ).all()

        assert [(user.id, user.email) for user in users] == [
            (account.id, account.email) for account in DEMO_ACCOUNTS
        ]
        assert workspace is not None
        assert len(credentials) == 4
        assert len(memberships) == 3
        verifications = [
            await seed_container.password_manager.verify_async(PASSWORD, item.password_hash)
            for item in credentials
        ]
        assert all(verification.valid for verification in verifications)
    finally:
        await _cleanup(seed_container)

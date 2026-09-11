"""PostgreSQL message durability and authorization-race checks."""

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from klack.core.config import AppEnvironment, Settings
from klack.core.container import AppContainer, build_container
from klack.modules.channels.application.service import (
    ChannelContentAccessService,
    ChannelService,
)
from klack.modules.channels.domain.errors import ChannelArchived
from klack.modules.channels.infrastructure.models import (
    ChannelMembershipRecord,
    ChannelRecord,
)
from klack.modules.channels.infrastructure.repository import SqlAlchemyChannelRepository
from klack.modules.identity.infrastructure.models import UserRecord
from klack.modules.messaging.application.service import MessageService
from klack.modules.messaging.domain.entities import Message
from klack.modules.messaging.infrastructure.models import MessageRecord
from klack.modules.messaging.infrastructure.repository import SqlAlchemyMessageRepository
from klack.modules.workspaces.application.service import WorkspaceAccessService
from klack.modules.workspaces.domain.errors import WorkspaceNotFound
from klack.modules.workspaces.infrastructure.models import MembershipRecord, WorkspaceRecord
from klack.modules.workspaces.infrastructure.repository import SqlAlchemyWorkspaceRepository

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 with a migrated PostgreSQL test database",
    ),
]


@dataclass(frozen=True, slots=True)
class SeededConversation:
    workspace_id: UUID
    channel_id: UUID
    owner_id: UUID
    member_id: UUID


def _settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env=AppEnvironment.TEST,
        auth_jwt_secret=SecretStr("integration-jwt-secret-at-least-thirty-two-bytes"),
        auth_refresh_secret=SecretStr("integration-refresh-secret-at-least-thirty-two-bytes"),
        auth_action_secret=SecretStr("integration-action-secret-at-least-thirty-two-bytes"),
        workspace_invitation_secret=SecretStr(
            "integration-workspace-secret-at-least-thirty-two-bytes",
        ),
        auth_trusted_origin="http://test",
        auth_public_web_origin="http://test",
        auth_cookie_secure=False,
    )


@pytest.fixture(scope="module")
def migrated_message_schema() -> None:
    backend_root = Path(__file__).parents[2]
    command.upgrade(Config(backend_root / "alembic.ini"), "head")


@pytest.fixture
async def message_container(migrated_message_schema: None) -> AsyncIterator[AppContainer]:
    del migrated_message_schema
    container = build_container(_settings())
    try:
        yield container
    finally:
        await container.engine.dispose()


def _message_service(container: AppContainer, session: AsyncSession) -> MessageService:
    return MessageService(
        repository=SqlAlchemyMessageRepository(session),
        channel_access=ChannelContentAccessService(
            repository=SqlAlchemyChannelRepository(session),
            workspace_access=WorkspaceAccessService(SqlAlchemyWorkspaceRepository(session)),
        ),
        policy=container.message_policy,
    )


def _channel_service(container: AppContainer, session: AsyncSession) -> ChannelService:
    return ChannelService(
        repository=SqlAlchemyChannelRepository(session),
        workspace_access=WorkspaceAccessService(SqlAlchemyWorkspaceRepository(session)),
        policy=container.channel_policy,
    )


async def _seed_conversation(container: AppContainer) -> SeededConversation:
    now = datetime.now(UTC)
    seeded = SeededConversation(
        workspace_id=uuid4(),
        channel_id=uuid4(),
        owner_id=uuid4(),
        member_id=uuid4(),
    )
    async with container.session_factory() as session:
        session.add_all(
            [
                UserRecord(
                    id=user_id,
                    email=f"message-integration+{user_id.hex}@example.com",
                    email_verified_at=None,
                    created_at=now,
                    disabled_at=None,
                )
                for user_id in (seeded.owner_id, seeded.member_id)
            ],
        )
        await session.flush()
        session.add(
            WorkspaceRecord(
                id=seeded.workspace_id,
                name="Message integration lab",
                created_by_user_id=seeded.owner_id,
                created_at=now,
                updated_at=now,
            ),
        )
        await session.flush()
        session.add_all(
            [
                MembershipRecord(
                    workspace_id=seeded.workspace_id,
                    user_id=seeded.owner_id,
                    role="owner",
                    joined_at=now,
                ),
                MembershipRecord(
                    workspace_id=seeded.workspace_id,
                    user_id=seeded.member_id,
                    role="member",
                    joined_at=now,
                ),
            ],
        )
        session.add(
            ChannelRecord(
                id=seeded.channel_id,
                workspace_id=seeded.workspace_id,
                name=f"messages-{seeded.channel_id.hex}",
                visibility="public",
                created_by_user_id=seeded.owner_id,
                created_at=now,
                updated_at=now,
                archived_at=None,
                archived_by_user_id=None,
            ),
        )
        await session.flush()
        session.add_all(
            [
                ChannelMembershipRecord(
                    workspace_id=seeded.workspace_id,
                    channel_id=seeded.channel_id,
                    user_id=user_id,
                    added_by_user_id=seeded.owner_id,
                    joined_at=now,
                )
                for user_id in (seeded.owner_id, seeded.member_id)
            ],
        )
        await session.commit()
    return seeded


async def _cleanup(container: AppContainer, seeded: SeededConversation) -> None:
    async with container.session_factory() as session:
        await session.execute(
            delete(WorkspaceRecord).where(WorkspaceRecord.id == seeded.workspace_id),
        )
        await session.execute(
            delete(UserRecord).where(
                UserRecord.id.in_([seeded.owner_id, seeded.member_id]),
            ),
        )
        await session.commit()


async def test_membership_removal_revokes_access_without_deleting_authored_history(
    message_container: AppContainer,
) -> None:
    seeded = await _seed_conversation(message_container)
    try:
        async with message_container.session_factory() as session:
            created = await _message_service(message_container, session).create_message(
                actor_user_id=seeded.member_id,
                workspace_id=seeded.workspace_id,
                channel_id=seeded.channel_id,
                body="durable after departure",
            )
        async with message_container.session_factory() as session:
            await session.execute(
                delete(MembershipRecord).where(
                    MembershipRecord.workspace_id == seeded.workspace_id,
                    MembershipRecord.user_id == seeded.member_id,
                ),
            )
            await session.commit()
        async with message_container.session_factory() as session:
            durable = await session.get(MessageRecord, created.id)
            assert durable is not None
            assert durable.body == "durable after departure"
            assert (
                await session.get(
                    ChannelMembershipRecord,
                    (seeded.channel_id, seeded.member_id),
                )
                is None
            )
            with pytest.raises(WorkspaceNotFound):
                await _message_service(message_container, session).list_messages(
                    actor_user_id=seeded.member_id,
                    workspace_id=seeded.workspace_id,
                    channel_id=seeded.channel_id,
                )
    finally:
        await _cleanup(message_container, seeded)


async def test_concurrent_archive_and_send_leave_consistent_committed_state(
    message_container: AppContainer,
) -> None:
    seeded = await _seed_conversation(message_container)

    async def archive() -> object:
        async with message_container.session_factory() as session:
            return await _channel_service(message_container, session).archive_channel(
                actor_user_id=seeded.owner_id,
                workspace_id=seeded.workspace_id,
                channel_id=seeded.channel_id,
            )

    async def send() -> Message:
        async with message_container.session_factory() as session:
            return await _message_service(message_container, session).create_message(
                actor_user_id=seeded.member_id,
                workspace_id=seeded.workspace_id,
                channel_id=seeded.channel_id,
                body="race-safe",
            )

    try:
        archived_outcome, sent_outcome = await asyncio.gather(
            archive(),
            send(),
            return_exceptions=True,
        )
        assert not isinstance(archived_outcome, Exception)
        assert isinstance(sent_outcome, (Message, ChannelArchived))
        async with message_container.session_factory() as session:
            channel = await session.get(ChannelRecord, seeded.channel_id)
            assert channel is not None
            assert channel.archived_at is not None
            durable_message = (
                None
                if isinstance(sent_outcome, ChannelArchived)
                else await session.get(MessageRecord, sent_outcome.id)
            )
            assert (durable_message is not None) is isinstance(sent_outcome, Message)
    finally:
        await _cleanup(message_container, seeded)

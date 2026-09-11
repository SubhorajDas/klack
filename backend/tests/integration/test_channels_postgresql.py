"""PostgreSQL channel containment, uniqueness, and lock-order integration checks."""

import asyncio
import os
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from klack.core.config import AppEnvironment, Settings
from klack.core.container import AppContainer, build_container
from klack.modules.channels.application.service import ChannelService, ChannelView
from klack.modules.channels.domain.entities import ChannelMembership, ChannelVisibility
from klack.modules.channels.domain.errors import (
    ChannelArchived,
    ChannelNameConflict,
    ChannelPermissionDenied,
)
from klack.modules.channels.infrastructure.models import (
    ChannelMembershipRecord,
    ChannelRecord,
)
from klack.modules.channels.infrastructure.repository import SqlAlchemyChannelRepository
from klack.modules.identity.infrastructure.models import UserRecord
from klack.modules.workspaces.application.ports import WorkspaceRepository
from klack.modules.workspaces.application.service import (
    InvitationCreation,
    WorkspaceAccessService,
    WorkspaceService,
)
from klack.modules.workspaces.domain.entities import Workspace, WorkspaceMembership, WorkspaceRole
from klack.modules.workspaces.infrastructure.models import WorkspaceRecord
from klack.modules.workspaces.infrastructure.repository import SqlAlchemyWorkspaceRepository

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 with a migrated PostgreSQL test database",
    ),
]


@dataclass
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value


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
def migrated_schema() -> None:
    """Bring the explicitly configured test database to the repository head."""
    backend_root = Path(__file__).parents[2]
    command.upgrade(Config(backend_root / "alembic.ini"), "head")


@pytest.fixture
async def channel_container(migrated_schema: None) -> AsyncIterator[AppContainer]:
    del migrated_schema
    container = build_container(_settings())
    try:
        yield container
    finally:
        await container.engine.dispose()


def _workspace_service(
    container: AppContainer,
    session: AsyncSession,
    clock: MutableClock,
    *,
    repository: WorkspaceRepository | None = None,
) -> WorkspaceService:
    return WorkspaceService(
        repository=repository or SqlAlchemyWorkspaceRepository(session),
        invitation_tokens=container.workspace_invitation_tokens,
        policy=container.workspace_policy,
        clock=clock,
    )


def _channel_service(
    container: AppContainer,
    session: AsyncSession,
    clock: MutableClock,
    *,
    workspace_repository: WorkspaceRepository | None = None,
) -> ChannelService:
    workspace_repository = workspace_repository or SqlAlchemyWorkspaceRepository(session)
    return ChannelService(
        repository=SqlAlchemyChannelRepository(session),
        workspace_access=WorkspaceAccessService(workspace_repository),
        policy=container.channel_policy,
        clock=clock,
    )


async def _seed_users(
    container: AppContainer,
    *,
    count: int,
    created_at: datetime,
) -> list[UUID]:
    user_ids = [uuid4() for _index in range(count)]
    async with container.session_factory() as session:
        session.add_all(
            [
                UserRecord(
                    id=user_id,
                    email=f"channel-integration+{user_id.hex}@example.com",
                    email_verified_at=None,
                    created_at=created_at,
                    disabled_at=None,
                )
                for user_id in user_ids
            ],
        )
        await session.commit()
    return user_ids


async def _create_workspace(
    container: AppContainer,
    clock: MutableClock,
    *,
    owner_id: UUID,
) -> Workspace:
    async with container.session_factory() as session:
        return await _workspace_service(container, session, clock).create_workspace(
            actor_user_id=owner_id,
            name="Channel concurrency lab",
        )


async def _create_invitation(
    container: AppContainer,
    clock: MutableClock,
    *,
    owner_id: UUID,
    workspace_id: UUID,
) -> InvitationCreation:
    async with container.session_factory() as session:
        return await _workspace_service(container, session, clock).create_invitation(
            actor_user_id=owner_id,
            workspace_id=workspace_id,
        )


async def _accept_invitation(
    container: AppContainer,
    clock: MutableClock,
    *,
    user_id: UUID,
    raw_token: str,
) -> WorkspaceMembership:
    async with container.session_factory() as session:
        return await _workspace_service(container, session, clock).accept_invitation(
            actor_user_id=user_id,
            raw_token=raw_token,
        )


async def _add_workspace_member(
    container: AppContainer,
    clock: MutableClock,
    *,
    owner_id: UUID,
    workspace_id: UUID,
    user_id: UUID,
    role: WorkspaceRole = WorkspaceRole.MEMBER,
) -> None:
    invitation = await _create_invitation(
        container,
        clock,
        owner_id=owner_id,
        workspace_id=workspace_id,
    )
    await _accept_invitation(
        container,
        clock,
        user_id=user_id,
        raw_token=invitation.raw_token,
    )
    if role is not WorkspaceRole.MEMBER:
        async with container.session_factory() as session:
            await _workspace_service(container, session, clock).change_membership_role(
                actor_user_id=owner_id,
                workspace_id=workspace_id,
                target_user_id=user_id,
                role=role,
            )


async def _create_channel(
    container: AppContainer,
    clock: MutableClock,
    *,
    actor_user_id: UUID,
    workspace_id: UUID,
    name: str = "general",
) -> ChannelView:
    async with container.session_factory() as session:
        return await _channel_service(container, session, clock).create_channel(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            name=name,
            visibility=ChannelVisibility.PUBLIC,
        )


async def _cleanup(
    container: AppContainer,
    *,
    workspace_ids: Sequence[UUID],
    user_ids: Sequence[UUID],
) -> None:
    async with container.session_factory() as session:
        if workspace_ids:
            await session.execute(
                delete(WorkspaceRecord).where(WorkspaceRecord.id.in_(workspace_ids)),
            )
        if user_ids:
            await session.execute(delete(UserRecord).where(UserRecord.id.in_(user_ids)))
        await session.commit()


async def test_workspace_membership_removal_cascades_channel_membership(
    channel_container: AppContainer,
) -> None:
    clock = MutableClock(datetime.now(UTC))
    owner_id, member_id = await _seed_users(channel_container, count=2, created_at=clock.value)
    workspace = await _create_workspace(channel_container, clock, owner_id=owner_id)
    await _add_workspace_member(
        channel_container,
        clock,
        owner_id=owner_id,
        workspace_id=workspace.id,
        user_id=member_id,
    )
    channel = await _create_channel(
        channel_container,
        clock,
        actor_user_id=owner_id,
        workspace_id=workspace.id,
    )
    async with channel_container.session_factory() as session:
        await _channel_service(channel_container, session, clock).add_membership(
            actor_user_id=owner_id,
            workspace_id=workspace.id,
            channel_id=channel.channel.id,
            target_user_id=member_id,
        )

    try:
        async with channel_container.session_factory() as session:
            await _workspace_service(channel_container, session, clock).remove_membership(
                actor_user_id=owner_id,
                workspace_id=workspace.id,
                target_user_id=member_id,
            )
        async with channel_container.session_factory() as session:
            assert (
                await session.get(ChannelMembershipRecord, (channel.channel.id, member_id)) is None
            )
            assert await session.get(ChannelRecord, channel.channel.id) is not None
    finally:
        await _cleanup(
            channel_container,
            workspace_ids=[workspace.id],
            user_ids=[owner_id, member_id],
        )


async def test_concurrent_duplicate_channel_creation_has_one_durable_winner(
    channel_container: AppContainer,
) -> None:
    clock = MutableClock(datetime.now(UTC))
    (owner_id,) = await _seed_users(channel_container, count=1, created_at=clock.value)
    workspace = await _create_workspace(channel_container, clock, owner_id=owner_id)

    async def create() -> ChannelView:
        return await _create_channel(
            channel_container,
            clock,
            actor_user_id=owner_id,
            workspace_id=workspace.id,
            name="platform",
        )

    try:
        outcomes = await asyncio.gather(create(), create(), return_exceptions=True)
        successes = [item for item in outcomes if isinstance(item, ChannelView)]
        conflicts = [item for item in outcomes if isinstance(item, ChannelNameConflict)]
        assert len(successes) == 1
        assert len(conflicts) == 1

        async with channel_container.session_factory() as session:
            channels = (
                await session.scalars(
                    select(ChannelRecord).where(ChannelRecord.workspace_id == workspace.id),
                )
            ).all()
        assert [(item.id, item.name) for item in channels] == [
            (successes[0].channel.id, "platform"),
        ]
    finally:
        await _cleanup(
            channel_container,
            workspace_ids=[workspace.id],
            user_ids=[owner_id],
        )


class PausingWorkspaceRepository(SqlAlchemyWorkspaceRepository):
    """Pause after acquiring a workspace lock to coordinate a deterministic race."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        lock_acquired: asyncio.Event,
        release_lock: asyncio.Event,
    ) -> None:
        super().__init__(session)
        self._lock_acquired = lock_acquired
        self._release_lock = release_lock
        self._paused = False

    async def get_workspace(
        self,
        workspace_id: UUID,
        *,
        for_update: bool = False,
    ) -> Workspace | None:
        workspace = await super().get_workspace(workspace_id, for_update=for_update)
        if for_update and not self._paused:
            self._paused = True
            self._lock_acquired.set()
            await self._release_lock.wait()
        return workspace


class SignallingWorkspaceRepository(SqlAlchemyWorkspaceRepository):
    """Signal immediately before attempting a workspace lock."""

    def __init__(self, session: AsyncSession, *, lock_attempted: asyncio.Event) -> None:
        super().__init__(session)
        self._lock_attempted = lock_attempted

    async def get_workspace(
        self,
        workspace_id: UUID,
        *,
        for_update: bool = False,
    ) -> Workspace | None:
        if for_update:
            self._lock_attempted.set()
        return await super().get_workspace(workspace_id, for_update=for_update)


async def test_demotion_serializes_before_channel_reauthorization(
    channel_container: AppContainer,
) -> None:
    clock = MutableClock(datetime.now(UTC))
    owner_id, administrator_id = await _seed_users(
        channel_container,
        count=2,
        created_at=clock.value,
    )
    workspace = await _create_workspace(channel_container, clock, owner_id=owner_id)
    await _add_workspace_member(
        channel_container,
        clock,
        owner_id=owner_id,
        workspace_id=workspace.id,
        user_id=administrator_id,
        role=WorkspaceRole.ADMIN,
    )
    lock_acquired = asyncio.Event()
    release_lock = asyncio.Event()
    lock_attempted = asyncio.Event()

    async def demote() -> WorkspaceMembership:
        async with channel_container.session_factory() as session:
            repository = PausingWorkspaceRepository(
                session,
                lock_acquired=lock_acquired,
                release_lock=release_lock,
            )
            return await _workspace_service(
                channel_container,
                session,
                clock,
                repository=repository,
            ).change_membership_role(
                actor_user_id=owner_id,
                workspace_id=workspace.id,
                target_user_id=administrator_id,
                role=WorkspaceRole.MEMBER,
            )

    async def create_as_administrator() -> ChannelView:
        async with channel_container.session_factory() as session:
            repository = SignallingWorkspaceRepository(
                session,
                lock_attempted=lock_attempted,
            )
            return await _channel_service(
                channel_container,
                session,
                clock,
                workspace_repository=repository,
            ).create_channel(
                actor_user_id=administrator_id,
                workspace_id=workspace.id,
                name="should-not-exist",
                visibility=ChannelVisibility.PUBLIC,
            )

    demotion_task = asyncio.create_task(demote())
    channel_task: asyncio.Task[ChannelView] | None = None
    try:
        await asyncio.wait_for(lock_acquired.wait(), timeout=5)
        channel_task = asyncio.create_task(create_as_administrator())
        await asyncio.wait_for(lock_attempted.wait(), timeout=5)
        release_lock.set()

        demoted = await asyncio.wait_for(demotion_task, timeout=5)
        assert demoted.role is WorkspaceRole.MEMBER
        with pytest.raises(ChannelPermissionDenied):
            await asyncio.wait_for(channel_task, timeout=5)

        async with channel_container.session_factory() as session:
            channels = (
                await session.scalars(
                    select(ChannelRecord).where(ChannelRecord.workspace_id == workspace.id),
                )
            ).all()
        assert channels == []
    finally:
        release_lock.set()
        if not demotion_task.done():
            demotion_task.cancel()
        if channel_task is not None and not channel_task.done():
            channel_task.cancel()
        await _cleanup(
            channel_container,
            workspace_ids=[workspace.id],
            user_ids=[owner_id, administrator_id],
        )


async def test_concurrent_archive_and_join_leave_consistent_state(
    channel_container: AppContainer,
) -> None:
    clock = MutableClock(datetime.now(UTC))
    owner_id, member_id = await _seed_users(channel_container, count=2, created_at=clock.value)
    workspace = await _create_workspace(channel_container, clock, owner_id=owner_id)
    await _add_workspace_member(
        channel_container,
        clock,
        owner_id=owner_id,
        workspace_id=workspace.id,
        user_id=member_id,
    )
    channel = await _create_channel(
        channel_container,
        clock,
        actor_user_id=owner_id,
        workspace_id=workspace.id,
    )

    async def archive() -> ChannelView:
        async with channel_container.session_factory() as session:
            return await _channel_service(channel_container, session, clock).archive_channel(
                actor_user_id=owner_id,
                workspace_id=workspace.id,
                channel_id=channel.channel.id,
            )

    async def join() -> ChannelMembership:
        async with channel_container.session_factory() as session:
            return await _channel_service(channel_container, session, clock).join_channel(
                actor_user_id=member_id,
                workspace_id=workspace.id,
                channel_id=channel.channel.id,
            )

    try:
        archived_outcome, joined_outcome = await asyncio.gather(
            archive(),
            join(),
            return_exceptions=True,
        )
        assert isinstance(archived_outcome, ChannelView)
        assert isinstance(joined_outcome, (ChannelMembership, ChannelArchived))
        async with channel_container.session_factory() as session:
            durable_channel = await session.get(ChannelRecord, channel.channel.id)
            durable_membership = await session.get(
                ChannelMembershipRecord,
                (channel.channel.id, member_id),
            )
        assert durable_channel is not None
        assert durable_channel.archived_at is not None
        assert (durable_membership is not None) is isinstance(joined_outcome, ChannelMembership)
    finally:
        await _cleanup(
            channel_container,
            workspace_ids=[workspace.id],
            user_ids=[owner_id, member_id],
        )

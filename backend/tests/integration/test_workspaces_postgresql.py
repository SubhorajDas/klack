"""PostgreSQL workspace locking, uniqueness, and invitation integration checks."""

import asyncio
import os
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
from klack.modules.identity.infrastructure.models import UserRecord
from klack.modules.workspaces.application.ports import (
    WorkspaceConflict,
    WorkspaceRepository,
)
from klack.modules.workspaces.application.service import (
    InvitationCreation,
    WorkspaceService,
)
from klack.modules.workspaces.domain.entities import (
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
)
from klack.modules.workspaces.domain.errors import (
    InvalidInvitationToken,
    InvitationNotActive,
    OwnerInvariantViolation,
    WorkspacePermissionDenied,
)
from klack.modules.workspaces.infrastructure.models import (
    InvitationRecord,
    MembershipRecord,
    WorkspaceRecord,
)
from klack.modules.workspaces.infrastructure.repository import (
    SqlAlchemyWorkspaceRepository,
)

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
async def workspace_container(migrated_schema: None) -> AsyncIterator[AppContainer]:
    """Build real process dependencies after Alembic has created the schema."""
    del migrated_schema
    container = build_container(_settings())
    try:
        yield container
    finally:
        await container.engine.dispose()


def _service(
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
                    email=f"workspace-integration+{user_id.hex}@example.com",
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
        return await _service(container, session, clock).create_workspace(
            actor_user_id=owner_id,
            name="PostgreSQL concurrency lab",
        )


async def _create_invitation(
    container: AppContainer,
    clock: MutableClock,
    *,
    owner_id: UUID,
    workspace_id: UUID,
) -> InvitationCreation:
    async with container.session_factory() as session:
        return await _service(container, session, clock).create_invitation(
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
        return await _service(container, session, clock).accept_invitation(
            actor_user_id=user_id,
            raw_token=raw_token,
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


async def test_concurrent_double_acceptance_has_one_durable_winner(
    workspace_container: AppContainer,
) -> None:
    clock = MutableClock(datetime.now(UTC))
    owner_id, first_recipient_id, second_recipient_id = await _seed_users(
        workspace_container,
        count=3,
        created_at=clock.value,
    )
    workspace = await _create_workspace(workspace_container, clock, owner_id=owner_id)
    invitation = await _create_invitation(
        workspace_container,
        clock,
        owner_id=owner_id,
        workspace_id=workspace.id,
    )

    try:
        outcomes = await asyncio.gather(
            _accept_invitation(
                workspace_container,
                clock,
                user_id=first_recipient_id,
                raw_token=invitation.raw_token,
            ),
            _accept_invitation(
                workspace_container,
                clock,
                user_id=second_recipient_id,
                raw_token=invitation.raw_token,
            ),
            return_exceptions=True,
        )
        successes = [item for item in outcomes if isinstance(item, WorkspaceMembership)]
        rejected = [item for item in outcomes if isinstance(item, InvalidInvitationToken)]
        unexpected = [
            item
            for item in outcomes
            if not isinstance(item, (WorkspaceMembership, InvalidInvitationToken))
        ]

        assert unexpected == []
        assert len(successes) == 1
        assert len(rejected) == 1

        async with workspace_container.session_factory() as session:
            memberships = (
                await session.scalars(
                    select(MembershipRecord).where(
                        MembershipRecord.workspace_id == workspace.id,
                    ),
                )
            ).all()
            durable_invitation = await session.get(
                InvitationRecord,
                invitation.invitation.id,
            )

        winner = successes[0]
        assert {(item.user_id, item.role) for item in memberships} == {
            (owner_id, str(WorkspaceRole.OWNER)),
            (winner.user_id, str(WorkspaceRole.MEMBER)),
        }
        assert durable_invitation is not None
        assert durable_invitation.accepted_by_user_id == winner.user_id
        assert durable_invitation.accepted_at is not None
        assert durable_invitation.revoked_at is None
    finally:
        await _cleanup(
            workspace_container,
            workspace_ids=[workspace.id],
            user_ids=[owner_id, first_recipient_id, second_recipient_id],
        )


async def test_concurrent_accept_and_revoke_have_one_consistent_winner(
    workspace_container: AppContainer,
) -> None:
    clock = MutableClock(datetime.now(UTC))
    owner_id, recipient_id = await _seed_users(
        workspace_container,
        count=2,
        created_at=clock.value,
    )
    workspace = await _create_workspace(workspace_container, clock, owner_id=owner_id)
    invitation = await _create_invitation(
        workspace_container,
        clock,
        owner_id=owner_id,
        workspace_id=workspace.id,
    )

    async def revoke() -> None:
        async with workspace_container.session_factory() as session:
            service = _service(
                container=workspace_container,
                session=session,
                clock=clock,
            )
            await service.revoke_invitation(
                actor_user_id=owner_id,
                workspace_id=workspace.id,
                invitation_id=invitation.invitation.id,
            )

    try:
        accepted_outcome, revoked_outcome = await asyncio.gather(
            _accept_invitation(
                workspace_container,
                clock,
                user_id=recipient_id,
                raw_token=invitation.raw_token,
            ),
            revoke(),
            return_exceptions=True,
        )
        accepted_won = isinstance(accepted_outcome, WorkspaceMembership)
        revoked_won = revoked_outcome is None
        assert accepted_won is not revoked_won
        if accepted_won:
            assert isinstance(revoked_outcome, InvitationNotActive)
        else:
            assert isinstance(accepted_outcome, InvalidInvitationToken)

        async with workspace_container.session_factory() as session:
            durable_invitation = await session.get(
                InvitationRecord,
                invitation.invitation.id,
            )
            recipient_membership = await session.get(
                MembershipRecord,
                (workspace.id, recipient_id),
            )

        assert durable_invitation is not None
        assert (durable_invitation.accepted_at is not None) is accepted_won
        assert (durable_invitation.revoked_at is not None) is revoked_won
        assert (recipient_membership is not None) is accepted_won
    finally:
        await _cleanup(
            workspace_container,
            workspace_ids=[workspace.id],
            user_ids=[owner_id, recipient_id],
        )


async def test_concurrent_owner_departures_preserve_one_owner(
    workspace_container: AppContainer,
) -> None:
    clock = MutableClock(datetime.now(UTC))
    first_owner_id, second_owner_id = await _seed_users(
        workspace_container,
        count=2,
        created_at=clock.value,
    )
    workspace = await _create_workspace(
        workspace_container,
        clock,
        owner_id=first_owner_id,
    )
    invitation = await _create_invitation(
        workspace_container,
        clock,
        owner_id=first_owner_id,
        workspace_id=workspace.id,
    )
    await _accept_invitation(
        workspace_container,
        clock,
        user_id=second_owner_id,
        raw_token=invitation.raw_token,
    )
    async with workspace_container.session_factory() as session:
        await _service(workspace_container, session, clock).change_membership_role(
            actor_user_id=first_owner_id,
            workspace_id=workspace.id,
            target_user_id=second_owner_id,
            role=WorkspaceRole.OWNER,
        )

    async def leave(user_id: UUID) -> None:
        async with workspace_container.session_factory() as session:
            await _service(workspace_container, session, clock).leave_workspace(
                actor_user_id=user_id,
                workspace_id=workspace.id,
            )

    try:
        outcomes = await asyncio.gather(
            leave(first_owner_id),
            leave(second_owner_id),
            return_exceptions=True,
        )
        assert sum(item is None for item in outcomes) == 1
        assert sum(isinstance(item, OwnerInvariantViolation) for item in outcomes) == 1

        async with workspace_container.session_factory() as session:
            memberships = (
                await session.scalars(
                    select(MembershipRecord).where(
                        MembershipRecord.workspace_id == workspace.id,
                    ),
                )
            ).all()
        assert len(memberships) == 1
        assert memberships[0].role == str(WorkspaceRole.OWNER)
        assert memberships[0].user_id in {first_owner_id, second_owner_id}
    finally:
        await _cleanup(
            workspace_container,
            workspace_ids=[workspace.id],
            user_ids=[first_owner_id, second_owner_id],
        )


async def test_database_uniqueness_rejects_concurrent_duplicate_membership(
    workspace_container: AppContainer,
) -> None:
    clock = MutableClock(datetime.now(UTC))
    owner_id, recipient_id = await _seed_users(
        workspace_container,
        count=2,
        created_at=clock.value,
    )
    workspace = await _create_workspace(workspace_container, clock, owner_id=owner_id)
    first = await _create_invitation(
        workspace_container,
        clock,
        owner_id=owner_id,
        workspace_id=workspace.id,
    )
    clock.value += timedelta(microseconds=1)
    second = await _create_invitation(
        workspace_container,
        clock,
        owner_id=owner_id,
        workspace_id=workspace.id,
    )

    async def accept_directly(invitation_id: UUID) -> bool:
        async with workspace_container.session_factory() as session:
            repository = SqlAlchemyWorkspaceRepository(session)
            try:
                await repository.accept_invitation(
                    invitation_id=invitation_id,
                    membership=WorkspaceMembership(
                        workspace_id=workspace.id,
                        user_id=recipient_id,
                        role=WorkspaceRole.MEMBER,
                        joined_at=clock.value,
                    ),
                    accepted_at=clock.value,
                )
                await repository.commit()
            except WorkspaceConflict:
                await repository.rollback()
                return False
            return True

    try:
        outcomes = await asyncio.gather(
            accept_directly(first.invitation.id),
            accept_directly(second.invitation.id),
        )
        assert outcomes.count(True) == 1
        assert outcomes.count(False) == 1

        async with workspace_container.session_factory() as session:
            membership = await session.get(
                MembershipRecord,
                (workspace.id, recipient_id),
            )
            invitations = (
                await session.scalars(
                    select(InvitationRecord).where(
                        InvitationRecord.id.in_(
                            [first.invitation.id, second.invitation.id],
                        ),
                    ),
                )
            ).all()
        assert membership is not None
        assert sum(item.accepted_at is not None for item in invitations) == 1
    finally:
        await _cleanup(
            workspace_container,
            workspace_ids=[workspace.id],
            user_ids=[owner_id, recipient_id],
        )


class PausingWorkspaceRepository(SqlAlchemyWorkspaceRepository):
    """Pause after acquiring the workspace lock to coordinate a deterministic race."""

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
    """Signal immediately before attempting the workspace lock."""

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


async def test_demotion_serializes_before_invitation_reauthorization(
    workspace_container: AppContainer,
) -> None:
    clock = MutableClock(datetime.now(UTC))
    owner_id, administrator_id = await _seed_users(
        workspace_container,
        count=2,
        created_at=clock.value,
    )
    workspace = await _create_workspace(workspace_container, clock, owner_id=owner_id)
    invitation = await _create_invitation(
        workspace_container,
        clock,
        owner_id=owner_id,
        workspace_id=workspace.id,
    )
    await _accept_invitation(
        workspace_container,
        clock,
        user_id=administrator_id,
        raw_token=invitation.raw_token,
    )
    async with workspace_container.session_factory() as session:
        await _service(workspace_container, session, clock).change_membership_role(
            actor_user_id=owner_id,
            workspace_id=workspace.id,
            target_user_id=administrator_id,
            role=WorkspaceRole.ADMIN,
        )

    lock_acquired = asyncio.Event()
    release_lock = asyncio.Event()
    lock_attempted = asyncio.Event()

    async def demote() -> WorkspaceMembership:
        async with workspace_container.session_factory() as session:
            repository = PausingWorkspaceRepository(
                session,
                lock_acquired=lock_acquired,
                release_lock=release_lock,
            )
            return await _service(
                workspace_container,
                session,
                clock,
                repository=repository,
            ).change_membership_role(
                actor_user_id=owner_id,
                workspace_id=workspace.id,
                target_user_id=administrator_id,
                role=WorkspaceRole.MEMBER,
            )

    async def create_as_administrator() -> InvitationCreation:
        async with workspace_container.session_factory() as session:
            repository = SignallingWorkspaceRepository(
                session,
                lock_attempted=lock_attempted,
            )
            return await _service(
                workspace_container,
                session,
                clock,
                repository=repository,
            ).create_invitation(
                actor_user_id=administrator_id,
                workspace_id=workspace.id,
            )

    demotion_task = asyncio.create_task(demote())
    invitation_task: asyncio.Task[InvitationCreation] | None = None
    try:
        await asyncio.wait_for(lock_acquired.wait(), timeout=5)
        invitation_task = asyncio.create_task(create_as_administrator())
        await asyncio.wait_for(lock_attempted.wait(), timeout=5)
        release_lock.set()

        demoted = await asyncio.wait_for(demotion_task, timeout=5)
        assert demoted.role is WorkspaceRole.MEMBER
        with pytest.raises(WorkspacePermissionDenied):
            await asyncio.wait_for(invitation_task, timeout=5)

        async with workspace_container.session_factory() as session:
            durable_membership = await session.get(
                MembershipRecord,
                (workspace.id, administrator_id),
            )
            invitations = (
                await session.scalars(
                    select(InvitationRecord).where(
                        InvitationRecord.workspace_id == workspace.id,
                    ),
                )
            ).all()
        assert durable_membership is not None
        assert durable_membership.role == str(WorkspaceRole.MEMBER)
        assert [item.id for item in invitations] == [invitation.invitation.id]
    finally:
        release_lock.set()
        if not demotion_task.done():
            demotion_task.cancel()
        if invitation_task is not None and not invitation_task.done():
            invitation_task.cancel()
        await _cleanup(
            workspace_container,
            workspace_ids=[workspace.id],
            user_ids=[owner_id, administrator_id],
        )

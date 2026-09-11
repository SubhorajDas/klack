"""FK-backed unit tests for workspace persistence and state mapping.

SQLite is used only for fast ORM/unit-of-work checks. PostgreSQL integration tests cover the
actual row-locking and migration contracts.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from klack.core.db.metadata import target_metadata
from klack.modules.identity.infrastructure.models import UserRecord
from klack.modules.workspaces.application.ports import WorkspaceConflict
from klack.modules.workspaces.domain.entities import (
    Workspace,
    WorkspaceInvitation,
    WorkspaceMembership,
    WorkspaceRole,
)
from klack.modules.workspaces.infrastructure.models import WorkspaceRecord
from klack.modules.workspaces.infrastructure.repository import (
    SqlAlchemyWorkspaceRepository,
)

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
OWNER_ID = UUID("11111111-1111-4111-8111-111111111111")
MEMBER_ID = UUID("22222222-2222-4222-8222-222222222222")
OTHER_ID = UUID("33333333-3333-4333-8333-333333333333")


@pytest.fixture
async def repository() -> AsyncIterator[SqlAlchemyWorkspaceRepository]:
    """Create the complete schema and seed identity principals required by workspace FKs."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def configure_sqlite(dbapi_connection: object, _connection_record: object) -> None:
        dbapi_connection.create_function("char_length", 1, len)  # type: ignore[attr-defined]
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(target_metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all(
            [
                UserRecord(
                    id=user_id,
                    email=f"workspace-unit+{index}@example.com",
                    email_verified_at=None,
                    created_at=NOW,
                    disabled_at=None,
                )
                for index, user_id in enumerate((OWNER_ID, MEMBER_ID, OTHER_ID), start=1)
            ],
        )
        await session.commit()
        yield SqlAlchemyWorkspaceRepository(session)
    await engine.dispose()


def make_workspace(
    *,
    workspace_id: UUID | None = None,
    name: str = "Persistence Lab",
) -> Workspace:
    return Workspace(
        id=workspace_id or uuid4(),
        name=name,
        created_by_user_id=OWNER_ID,
        created_at=NOW,
        updated_at=NOW,
    )


def make_membership(
    workspace_id: UUID,
    user_id: UUID,
    *,
    role: WorkspaceRole = WorkspaceRole.MEMBER,
    offset: int = 0,
) -> WorkspaceMembership:
    return WorkspaceMembership(
        workspace_id=workspace_id,
        user_id=user_id,
        role=role,
        joined_at=NOW + timedelta(minutes=offset),
    )


def make_invitation(
    workspace_id: UUID,
    *,
    invitation_id: UUID | None = None,
    token_hash: str | None = None,
    offset: int = 0,
) -> WorkspaceInvitation:
    created_at = NOW + timedelta(minutes=offset)
    return WorkspaceInvitation(
        id=invitation_id or uuid4(),
        workspace_id=workspace_id,
        created_by_user_id=OWNER_ID,
        token_hash=token_hash or (uuid4().hex + uuid4().hex),
        created_at=created_at,
        expires_at=created_at + timedelta(days=7),
        accepted_at=None,
        accepted_by_user_id=None,
        revoked_at=None,
        revoked_by_user_id=None,
    )


async def add_workspace(
    repository: SqlAlchemyWorkspaceRepository,
    *,
    workspace: Workspace | None = None,
) -> Workspace:
    created = workspace or make_workspace()
    await repository.add_workspace(
        created,
        make_membership(
            created.id,
            OWNER_ID,
            role=WorkspaceRole.OWNER,
        ),
    )
    await repository.commit()
    return created


async def test_workspace_and_membership_crud_with_fk_cascade(
    repository: SqlAlchemyWorkspaceRepository,
) -> None:
    workspace = await add_workspace(repository)

    assert [item.id for item in await repository.list_workspaces(user_id=OWNER_ID)] == [
        workspace.id,
    ]
    assert await repository.list_workspaces(user_id=OTHER_ID) == []
    assert await repository.get_workspace(uuid4()) is None
    loaded = await repository.get_workspace(workspace.id, for_update=True)
    assert loaded is not None
    assert loaded.name == "Persistence Lab"
    assert loaded.created_by_user_id == OWNER_ID

    renamed_at = NOW + timedelta(minutes=1)
    await repository.update_workspace_name(
        workspace_id=workspace.id,
        name="Renamed Lab",
        updated_at=renamed_at,
    )
    await repository.commit()
    renamed = await repository.get_workspace(workspace.id)
    assert renamed is not None
    assert renamed.name == "Renamed Lab"
    assert renamed.updated_at.replace(tzinfo=UTC) == renamed_at

    invitation = make_invitation(workspace.id)
    await repository.add_invitation(invitation)
    await repository.commit()
    await repository.accept_invitation(
        invitation_id=invitation.id,
        membership=make_membership(workspace.id, MEMBER_ID, offset=2),
        accepted_at=NOW + timedelta(minutes=2),
    )
    await repository.commit()

    memberships = await repository.list_memberships(workspace_id=workspace.id)
    assert [(item.user_id, item.role) for item in memberships] == [
        (OWNER_ID, WorkspaceRole.OWNER),
        (MEMBER_ID, WorkspaceRole.MEMBER),
    ]
    assert (
        await repository.count_memberships_by_role(
            workspace_id=workspace.id,
            role=WorkspaceRole.OWNER,
        )
        == 1
    )
    assert (
        await repository.count_memberships_by_role(
            workspace_id=workspace.id,
            role=WorkspaceRole.ADMIN,
        )
        == 0
    )
    member = await repository.get_membership(
        workspace_id=workspace.id,
        user_id=MEMBER_ID,
        for_update=True,
    )
    assert member is not None
    assert member.role is WorkspaceRole.MEMBER
    assert (
        await repository.get_membership(
            workspace_id=workspace.id,
            user_id=OTHER_ID,
        )
        is None
    )

    await repository.update_membership_role(
        workspace_id=workspace.id,
        user_id=MEMBER_ID,
        role=WorkspaceRole.ADMIN,
    )
    await repository.commit()
    promoted = await repository.get_membership(
        workspace_id=workspace.id,
        user_id=MEMBER_ID,
    )
    assert promoted is not None
    assert promoted.role is WorkspaceRole.ADMIN

    await repository.remove_membership(
        workspace_id=workspace.id,
        user_id=MEMBER_ID,
    )
    await repository.commit()
    assert (
        await repository.get_membership(
            workspace_id=workspace.id,
            user_id=MEMBER_ID,
        )
        is None
    )

    # Workspace deletion is intentionally not an application use case yet, but the database
    # ownership contract must cascade all workspace-owned rows.
    await repository._session.execute(
        delete(WorkspaceRecord).where(WorkspaceRecord.id == workspace.id),
    )
    await repository.commit()
    assert await repository.get_workspace(workspace.id) is None
    assert await repository.list_memberships(workspace_id=workspace.id) == []
    assert await repository.list_invitations(workspace_id=workspace.id) == []


async def test_invitation_add_list_lookup_accept_revoke_and_mapping(
    repository: SqlAlchemyWorkspaceRepository,
) -> None:
    workspace = await add_workspace(repository)
    revoked = make_invitation(workspace.id, offset=0)
    accepted = make_invitation(workspace.id, offset=1)
    await repository.add_invitation(revoked)
    await repository.add_invitation(accepted)
    await repository.commit()

    invitations = await repository.list_invitations(workspace_id=workspace.id)
    assert [item.id for item in invitations] == [accepted.id, revoked.id]
    assert invitations[0].token_hash == accepted.token_hash
    assert invitations[0].expires_at.replace(tzinfo=UTC) == accepted.expires_at
    assert await repository.get_invitation_workspace_id(accepted.id) == workspace.id
    assert await repository.get_invitation_workspace_id(uuid4()) is None
    assert (
        await repository.get_invitation(
            workspace_id=uuid4(),
            invitation_id=accepted.id,
        )
        is None
    )

    revoked_at = NOW + timedelta(minutes=2)
    await repository.revoke_invitation(
        invitation_id=revoked.id,
        revoked_at=revoked_at,
        revoked_by_user_id=OWNER_ID,
    )
    await repository.accept_invitation(
        invitation_id=accepted.id,
        membership=make_membership(workspace.id, MEMBER_ID, offset=3),
        accepted_at=NOW + timedelta(minutes=3),
    )
    await repository.commit()

    durable_revoked = await repository.get_invitation(
        workspace_id=workspace.id,
        invitation_id=revoked.id,
        for_update=True,
    )
    durable_accepted = await repository.get_invitation(
        workspace_id=workspace.id,
        invitation_id=accepted.id,
    )
    assert durable_revoked is not None
    assert durable_revoked.revoked_by_user_id == OWNER_ID
    assert durable_revoked.revoked_at is not None
    assert durable_accepted is not None
    assert durable_accepted.accepted_by_user_id == MEMBER_ID
    assert durable_accepted.accepted_at is not None
    assert durable_accepted.revoked_at is None


async def test_duplicate_workspace_flush_is_translated_and_rolled_back(
    repository: SqlAlchemyWorkspaceRepository,
) -> None:
    workspace = await add_workspace(repository)

    with pytest.raises(WorkspaceConflict):
        await repository.add_workspace(
            make_workspace(workspace_id=workspace.id, name="Conflicting Name"),
            make_membership(
                workspace.id,
                OWNER_ID,
                role=WorkspaceRole.OWNER,
            ),
        )

    durable = await repository.get_workspace(workspace.id)
    assert durable is not None
    assert durable.name == workspace.name
    assert len(await repository.list_memberships(workspace_id=workspace.id)) == 1


async def test_commit_translates_unique_invitation_conflict_and_explicit_rollback(
    repository: SqlAlchemyWorkspaceRepository,
) -> None:
    workspace = await add_workspace(repository)
    token_hash = "a" * 64
    original = make_invitation(workspace.id, token_hash=token_hash)
    await repository.add_invitation(original)
    await repository.commit()

    await repository.add_invitation(
        make_invitation(
            workspace.id,
            token_hash=token_hash,
            offset=1,
        ),
    )
    with pytest.raises(WorkspaceConflict):
        await repository.commit()

    await repository.update_workspace_name(
        workspace_id=workspace.id,
        name="Not Committed",
        updated_at=NOW + timedelta(minutes=2),
    )
    await repository.rollback()

    durable = await repository.get_workspace(workspace.id)
    assert durable is not None
    assert durable.name == workspace.name
    assert [item.id for item in await repository.list_invitations(workspace_id=workspace.id)] == [
        original.id,
    ]

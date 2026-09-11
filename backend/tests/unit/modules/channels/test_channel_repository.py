"""FK-backed unit tests for channel persistence and visibility queries.

SQLite is used only for fast ORM/unit-of-work checks. PostgreSQL integration tests cover the
actual row-locking and migration contracts.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from klack.core.db.metadata import target_metadata
from klack.modules.channels.application.ports import ChannelConflict
from klack.modules.channels.domain.entities import (
    Channel,
    ChannelMembership,
    ChannelVisibility,
)
from klack.modules.channels.infrastructure.models import (
    ChannelMembershipRecord,
    ChannelRecord,
)
from klack.modules.channels.infrastructure.repository import (
    SqlAlchemyChannelRepository,
)
from klack.modules.identity.infrastructure.models import UserRecord
from klack.modules.workspaces.domain.entities import WorkspaceRole
from klack.modules.workspaces.infrastructure.models import (
    MembershipRecord,
    WorkspaceRecord,
)

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
OWNER_ID = UUID("11111111-1111-4111-8111-111111111111")
MEMBER_ID = UUID("22222222-2222-4222-8222-222222222222")
MANAGER_ID = UUID("33333333-3333-4333-8333-333333333333")
OUTSIDER_ID = UUID("44444444-4444-4444-8444-444444444444")


@pytest.fixture
async def repository() -> AsyncIterator[SqlAlchemyChannelRepository]:
    """Create the full schema and identity principals required by channel FKs."""
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
                    email=f"channel-unit+{index}@example.com",
                    email_verified_at=None,
                    created_at=NOW,
                    disabled_at=None,
                )
                for index, user_id in enumerate(
                    (OWNER_ID, MEMBER_ID, MANAGER_ID, OUTSIDER_ID),
                    start=1,
                )
            ],
        )
        await session.commit()
        yield SqlAlchemyChannelRepository(session)
    await engine.dispose()


async def add_workspace(
    repository: SqlAlchemyChannelRepository,
    *,
    members: tuple[tuple[UUID, WorkspaceRole], ...],
    name: str = "Channel Lab",
) -> UUID:
    workspace_id = uuid4()
    repository._session.add(
        WorkspaceRecord(
            id=workspace_id,
            name=name,
            created_by_user_id=OWNER_ID,
            created_at=NOW,
            updated_at=NOW,
        ),
    )
    await repository._session.flush()
    repository._session.add_all(
        [
            MembershipRecord(
                workspace_id=workspace_id,
                user_id=user_id,
                role=str(role),
                joined_at=NOW + timedelta(minutes=index),
            )
            for index, (user_id, role) in enumerate(members)
        ],
    )
    await repository._session.commit()
    return workspace_id


def make_channel(
    workspace_id: UUID,
    *,
    name: str = "engineering",
    visibility: ChannelVisibility = ChannelVisibility.PUBLIC,
    channel_id: UUID | None = None,
    offset: int = 0,
    archived: bool = False,
) -> Channel:
    created_at = NOW + timedelta(minutes=offset)
    archived_at = created_at + timedelta(seconds=30) if archived else None
    return Channel(
        id=channel_id or uuid4(),
        workspace_id=workspace_id,
        name=name,
        visibility=visibility,
        created_by_user_id=OWNER_ID,
        created_at=created_at,
        updated_at=archived_at or created_at,
        archived_at=archived_at,
        archived_by_user_id=OWNER_ID if archived else None,
    )


def make_channel_membership(
    channel: Channel,
    user_id: UUID,
    *,
    workspace_id: UUID | None = None,
    offset: int = 0,
) -> ChannelMembership:
    return ChannelMembership(
        workspace_id=workspace_id or channel.workspace_id,
        channel_id=channel.id,
        user_id=user_id,
        added_by_user_id=OWNER_ID,
        joined_at=NOW + timedelta(minutes=offset),
    )


async def add_channel(
    repository: SqlAlchemyChannelRepository,
    channel: Channel,
    *,
    creator_joined_offset: int = 0,
) -> None:
    await repository.add_channel(
        channel,
        make_channel_membership(
            channel,
            OWNER_ID,
            offset=creator_joined_offset,
        ),
    )
    await repository.commit()


def as_utc(value: datetime) -> datetime:
    """Restore UTC metadata omitted by SQLite's datetime adapter."""
    return value.replace(tzinfo=UTC)


async def test_channel_and_membership_crud_round_trip_domain_mapping(
    repository: SqlAlchemyChannelRepository,
) -> None:
    workspace_id = await add_workspace(
        repository,
        members=(
            (OWNER_ID, WorkspaceRole.OWNER),
            (MEMBER_ID, WorkspaceRole.MEMBER),
        ),
    )
    channel = make_channel(
        workspace_id,
        visibility=ChannelVisibility.PRIVATE,
        channel_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    )
    await add_channel(repository, channel, creator_joined_offset=2)

    assert (
        await repository.get_channel(
            workspace_id=uuid4(),
            channel_id=channel.id,
        )
        is None
    )
    loaded = await repository.get_channel(
        workspace_id=workspace_id,
        channel_id=channel.id,
        for_update=True,
    )
    assert loaded is not None
    assert loaded.id == channel.id
    assert loaded.workspace_id == workspace_id
    assert loaded.name == "engineering"
    assert loaded.visibility is ChannelVisibility.PRIVATE
    assert loaded.created_by_user_id == OWNER_ID
    assert as_utc(loaded.created_at) == channel.created_at
    assert as_utc(loaded.updated_at) == channel.updated_at
    assert not loaded.is_archived

    raw_channel = await repository._session.get(ChannelRecord, channel.id)
    assert raw_channel is not None
    assert raw_channel.visibility == "private"
    raw_creator = await repository._session.get(
        ChannelMembershipRecord,
        (channel.id, OWNER_ID),
    )
    assert raw_creator is not None
    assert raw_creator.workspace_id == workspace_id
    assert raw_creator.added_by_user_id == OWNER_ID

    member_membership = make_channel_membership(channel, MEMBER_ID, offset=1)
    await repository.add_membership(member_membership)
    await repository.commit()

    loaded_member = await repository.get_membership(
        channel_id=channel.id,
        user_id=MEMBER_ID,
        for_update=True,
    )
    assert loaded_member is not None
    assert loaded_member.workspace_id == member_membership.workspace_id
    assert loaded_member.channel_id == member_membership.channel_id
    assert loaded_member.user_id == member_membership.user_id
    assert loaded_member.added_by_user_id == member_membership.added_by_user_id
    assert as_utc(loaded_member.joined_at) == member_membership.joined_at
    assert (
        await repository.get_membership(
            channel_id=channel.id,
            user_id=MANAGER_ID,
        )
        is None
    )
    memberships = await repository.list_memberships(channel_id=channel.id)
    assert [membership.user_id for membership in memberships] == [
        MEMBER_ID,
        OWNER_ID,
    ]
    assert as_utc(memberships[0].joined_at) == member_membership.joined_at

    await repository.remove_membership(
        channel_id=channel.id,
        user_id=MEMBER_ID,
    )
    await repository.commit()
    assert await repository.list_memberships(channel_id=channel.id) == [
        memberships[1],
    ]

    await repository._session.execute(
        delete(ChannelRecord).where(ChannelRecord.id == channel.id),
    )
    await repository.commit()
    assert (
        await repository.get_channel(
            workspace_id=workspace_id,
            channel_id=channel.id,
        )
        is None
    )
    assert await repository.list_memberships(channel_id=channel.id) == []


async def test_visible_listing_filters_private_and_archived_channels_and_orders_names(
    repository: SqlAlchemyChannelRepository,
) -> None:
    workspace_id = await add_workspace(
        repository,
        members=(
            (OWNER_ID, WorkspaceRole.OWNER),
            (MEMBER_ID, WorkspaceRole.MEMBER),
            (MANAGER_ID, WorkspaceRole.ADMIN),
        ),
    )
    public = make_channel(workspace_id, name="zeta-public")
    joined_private = make_channel(
        workspace_id,
        name="alpha-private",
        visibility=ChannelVisibility.PRIVATE,
    )
    hidden_private = make_channel(
        workspace_id,
        name="middle-private",
        visibility=ChannelVisibility.PRIVATE,
    )
    archived_public = make_channel(
        workspace_id,
        name="beta-archived",
        archived=True,
    )
    for channel in (public, joined_private, hidden_private, archived_public):
        await add_channel(repository, channel)
    await repository.add_membership(
        make_channel_membership(joined_private, MEMBER_ID, offset=5),
    )
    await repository.commit()

    visible = await repository.list_channels(
        workspace_id=workspace_id,
        actor_user_id=MEMBER_ID,
        can_view_private=False,
        include_archived=False,
    )
    assert [channel.name for channel, _membership in visible] == [
        "alpha-private",
        "zeta-public",
    ]
    assert [membership is not None for _channel, membership in visible] == [
        True,
        False,
    ]

    visible_with_archived = await repository.list_channels(
        workspace_id=workspace_id,
        actor_user_id=MEMBER_ID,
        can_view_private=False,
        include_archived=True,
    )
    assert [channel.name for channel, _membership in visible_with_archived] == [
        "alpha-private",
        "beta-archived",
        "zeta-public",
    ]

    manager_view = await repository.list_channels(
        workspace_id=workspace_id,
        actor_user_id=MANAGER_ID,
        can_view_private=True,
        include_archived=True,
    )
    assert [channel.name for channel, _membership in manager_view] == [
        "alpha-private",
        "beta-archived",
        "middle-private",
        "zeta-public",
    ]
    assert all(membership is None for _channel, membership in manager_view)

    assert (
        await repository.list_channels(
            workspace_id=uuid4(),
            actor_user_id=MEMBER_ID,
            can_view_private=True,
            include_archived=True,
        )
        == []
    )


async def test_channel_update_archive_unarchive_and_rollback(
    repository: SqlAlchemyChannelRepository,
) -> None:
    workspace_id = await add_workspace(
        repository,
        members=((OWNER_ID, WorkspaceRole.OWNER),),
    )
    channel = make_channel(workspace_id)
    await add_channel(repository, channel)

    renamed_at = NOW + timedelta(minutes=1)
    await repository.update_channel(
        channel_id=channel.id,
        name="platform",
        visibility=ChannelVisibility.PRIVATE,
        updated_at=renamed_at,
    )
    await repository.commit()
    updated = await repository.get_channel(
        workspace_id=workspace_id,
        channel_id=channel.id,
    )
    assert updated is not None
    assert updated.name == "platform"
    assert updated.visibility is ChannelVisibility.PRIVATE
    assert as_utc(updated.updated_at) == renamed_at

    archived_at = NOW + timedelta(minutes=2)
    await repository.set_channel_archived(
        channel_id=channel.id,
        archived_at=archived_at,
        archived_by_user_id=OWNER_ID,
        updated_at=archived_at,
    )
    await repository.commit()
    archived = await repository.get_channel(
        workspace_id=workspace_id,
        channel_id=channel.id,
    )
    assert archived is not None
    assert archived.is_archived
    assert archived.archived_at is not None
    assert as_utc(archived.archived_at) == archived_at
    assert archived.archived_by_user_id == OWNER_ID

    restored_at = NOW + timedelta(minutes=3)
    await repository.set_channel_archived(
        channel_id=channel.id,
        archived_at=None,
        archived_by_user_id=None,
        updated_at=restored_at,
    )
    await repository.commit()
    restored = await repository.get_channel(
        workspace_id=workspace_id,
        channel_id=channel.id,
    )
    assert restored is not None
    assert not restored.is_archived
    assert restored.archived_by_user_id is None
    assert as_utc(restored.updated_at) == restored_at

    await repository.update_channel(
        channel_id=channel.id,
        name="not-committed",
        visibility=ChannelVisibility.PUBLIC,
        updated_at=NOW + timedelta(minutes=4),
    )
    await repository.rollback()
    durable = await repository.get_channel(
        workspace_id=workspace_id,
        channel_id=channel.id,
    )
    assert durable is not None
    assert durable.name == "platform"
    assert durable.visibility is ChannelVisibility.PRIVATE


async def test_unique_channel_and_membership_conflicts_are_translated_and_rolled_back(
    repository: SqlAlchemyChannelRepository,
) -> None:
    workspace_id = await add_workspace(
        repository,
        members=((OWNER_ID, WorkspaceRole.OWNER),),
    )
    original = make_channel(workspace_id, name="general")
    second = make_channel(workspace_id, name="random")
    await add_channel(repository, original)
    await add_channel(repository, second)

    duplicate = make_channel(workspace_id, name="general")
    with pytest.raises(ChannelConflict):
        await repository.add_channel(
            duplicate,
            make_channel_membership(duplicate, OWNER_ID),
        )
    assert (
        await repository.get_channel(
            workspace_id=workspace_id,
            channel_id=duplicate.id,
        )
        is None
    )

    with pytest.raises(ChannelConflict):
        await repository.update_channel(
            channel_id=second.id,
            name="general",
            visibility=second.visibility,
            updated_at=NOW + timedelta(minutes=1),
        )
        await repository.commit()
    durable_second = await repository.get_channel(
        workspace_id=workspace_id,
        channel_id=second.id,
    )
    assert durable_second is not None
    assert durable_second.name == "random"

    with pytest.raises(ChannelConflict):
        await repository.add_membership(
            make_channel_membership(original, OWNER_ID),
        )
    assert len(await repository.list_memberships(channel_id=original.id)) == 1


async def test_channel_membership_requires_membership_in_the_same_workspace(
    repository: SqlAlchemyChannelRepository,
) -> None:
    first_workspace_id = await add_workspace(
        repository,
        members=(
            (OWNER_ID, WorkspaceRole.OWNER),
            (MEMBER_ID, WorkspaceRole.MEMBER),
        ),
        name="First Workspace",
    )
    second_workspace_id = await add_workspace(
        repository,
        members=(
            (OWNER_ID, WorkspaceRole.OWNER),
            (OUTSIDER_ID, WorkspaceRole.MEMBER),
        ),
        name="Second Workspace",
    )
    channel = make_channel(first_workspace_id)
    await add_channel(repository, channel)

    # The user exists, but has no membership in the channel's workspace.
    with pytest.raises(ChannelConflict):
        await repository.add_membership(
            make_channel_membership(channel, OUTSIDER_ID),
        )

    # The user belongs to this asserted workspace, but the channel does not.
    with pytest.raises(ChannelConflict):
        await repository.add_membership(
            make_channel_membership(
                channel,
                OUTSIDER_ID,
                workspace_id=second_workspace_id,
            ),
        )

    assert [
        membership.user_id
        for membership in await repository.list_memberships(channel_id=channel.id)
    ] == [OWNER_ID]
    assert (
        await repository.get_channel(
            workspace_id=second_workspace_id,
            channel_id=channel.id,
        )
        is None
    )


async def test_workspace_membership_and_workspace_deletes_cascade_channel_state(
    repository: SqlAlchemyChannelRepository,
) -> None:
    workspace_id = await add_workspace(
        repository,
        members=(
            (OWNER_ID, WorkspaceRole.OWNER),
            (MEMBER_ID, WorkspaceRole.MEMBER),
        ),
    )
    channel = make_channel(workspace_id)
    await add_channel(repository, channel)
    await repository.add_membership(
        make_channel_membership(channel, MEMBER_ID, offset=1),
    )
    await repository.commit()

    await repository._session.execute(
        delete(MembershipRecord).where(
            MembershipRecord.workspace_id == workspace_id,
            MembershipRecord.user_id == MEMBER_ID,
        ),
    )
    await repository.commit()
    assert (
        await repository.get_membership(
            channel_id=channel.id,
            user_id=MEMBER_ID,
        )
        is None
    )
    assert [
        membership.user_id
        for membership in await repository.list_memberships(channel_id=channel.id)
    ] == [OWNER_ID]

    await repository._session.execute(
        delete(WorkspaceRecord).where(WorkspaceRecord.id == workspace_id),
    )
    await repository.commit()
    assert (
        await repository.get_channel(
            workspace_id=workspace_id,
            channel_id=channel.id,
        )
        is None
    )
    remaining_memberships = await repository._session.scalars(
        select(ChannelMembershipRecord).where(
            ChannelMembershipRecord.channel_id == channel.id,
        ),
    )
    assert remaining_memberships.all() == []

"""FK-backed unit tests for message persistence and history pagination."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import delete, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from klack.core.db.metadata import target_metadata
from klack.modules.channels.infrastructure.models import (
    ChannelMembershipRecord,
    ChannelRecord,
)
from klack.modules.identity.infrastructure.models import UserRecord
from klack.modules.messaging.domain.entities import Message
from klack.modules.messaging.infrastructure.models import MessageRecord
from klack.modules.messaging.infrastructure.repository import SqlAlchemyMessageRepository
from klack.modules.workspaces.infrastructure.models import MembershipRecord, WorkspaceRecord

NOW = datetime(2026, 9, 11, 17, 0, tzinfo=UTC)
AUTHOR_ID = UUID(int=1)
WORKSPACE_ID = UUID(int=10)
OTHER_WORKSPACE_ID = UUID(int=11)
CHANNEL_ID = UUID(int=20)
OTHER_CHANNEL_ID = UUID(int=21)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
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
    async with factory() as database_session:
        database_session.add(
            UserRecord(
                id=AUTHOR_ID,
                email="message-author@example.com",
                email_verified_at=None,
                created_at=NOW,
                disabled_at=None,
            ),
        )
        for workspace_id, channel_id, name in (
            (WORKSPACE_ID, CHANNEL_ID, "general"),
            (OTHER_WORKSPACE_ID, OTHER_CHANNEL_ID, "other"),
        ):
            database_session.add(
                WorkspaceRecord(
                    id=workspace_id,
                    name=f"Workspace {workspace_id.int}",
                    created_by_user_id=AUTHOR_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
            )
            await database_session.flush()
            database_session.add(
                MembershipRecord(
                    workspace_id=workspace_id,
                    user_id=AUTHOR_ID,
                    role="owner",
                    joined_at=NOW,
                ),
            )
            database_session.add(
                ChannelRecord(
                    id=channel_id,
                    workspace_id=workspace_id,
                    name=name,
                    visibility="public",
                    created_by_user_id=AUTHOR_ID,
                    created_at=NOW,
                    updated_at=NOW,
                    archived_at=None,
                    archived_by_user_id=None,
                ),
            )
            await database_session.flush()
            database_session.add(
                ChannelMembershipRecord(
                    workspace_id=workspace_id,
                    channel_id=channel_id,
                    user_id=AUTHOR_ID,
                    added_by_user_id=AUTHOR_ID,
                    joined_at=NOW,
                ),
            )
        await database_session.commit()
        yield database_session
    await engine.dispose()


def make_message(
    message_id: int,
    *,
    channel_id: UUID = CHANNEL_ID,
    workspace_id: UUID = WORKSPACE_ID,
    created_at: datetime = NOW,
    body: str | None = "hello",
    edited_at: datetime | None = None,
    deleted_at: datetime | None = None,
) -> Message:
    return Message(
        id=UUID(int=message_id),
        workspace_id=workspace_id,
        channel_id=channel_id,
        author_user_id=AUTHOR_ID,
        body=body,
        created_at=created_at,
        edited_at=edited_at,
        deleted_at=deleted_at,
    )


def as_utc(value: datetime | None) -> datetime | None:
    return None if value is None else value.replace(tzinfo=UTC)


async def test_message_round_trip_update_and_tombstone(session: AsyncSession) -> None:
    repository = SqlAlchemyMessageRepository(session)
    message = make_message(100, body="  formatted\nmessage  ")
    await repository.add_message(message)
    await repository.commit()

    assert (
        await repository.get_message(
            workspace_id=OTHER_WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            message_id=message.id,
        )
        is None
    )
    loaded = await repository.get_message(
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        message_id=message.id,
        for_update=True,
    )
    assert loaded is not None
    assert loaded.body == "  formatted\nmessage  "
    assert as_utc(loaded.created_at) == NOW
    assert not loaded.is_deleted

    edited_at = NOW + timedelta(minutes=1)
    await repository.update_message(
        message_id=message.id,
        body="edited",
        edited_at=edited_at,
        deleted_at=None,
    )
    await repository.commit()
    deleted_at = NOW + timedelta(minutes=2)
    await repository.update_message(
        message_id=message.id,
        body=None,
        edited_at=edited_at,
        deleted_at=deleted_at,
    )
    await repository.commit()
    tombstone = await repository.get_message(
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        message_id=message.id,
    )
    assert tombstone is not None
    assert tombstone.body is None
    assert as_utc(tombstone.edited_at) == edited_at
    assert as_utc(tombstone.deleted_at) == deleted_at
    assert tombstone.is_deleted
    await repository.rollback()


async def test_history_is_stably_ordered_and_keyset_paginated(session: AsyncSession) -> None:
    repository = SqlAlchemyMessageRepository(session)
    messages = [
        make_message(1, created_at=NOW),
        make_message(2, created_at=NOW),
        make_message(3, created_at=NOW + timedelta(seconds=1)),
        make_message(
            4,
            channel_id=OTHER_CHANNEL_ID,
            workspace_id=OTHER_WORKSPACE_ID,
            created_at=NOW + timedelta(seconds=2),
        ),
    ]
    for message in messages:
        await repository.add_message(message)
    await repository.commit()
    first = await repository.list_messages(
        channel_id=CHANNEL_ID,
        before_created_at=None,
        before_message_id=None,
        limit=2,
    )
    assert [message.id.int for message in first] == [3, 2]
    second = await repository.list_messages(
        channel_id=CHANNEL_ID,
        before_created_at=first[-1].created_at,
        before_message_id=first[-1].id,
        limit=2,
    )
    assert [message.id.int for message in second] == [1]


@pytest.mark.parametrize(
    ("body", "deleted_at"),
    [("   ", None), (None, None), ("still present", NOW)],
)
async def test_database_rejects_invalid_live_or_deleted_state(
    session: AsyncSession,
    body: str | None,
    deleted_at: datetime | None,
) -> None:
    repository = SqlAlchemyMessageRepository(session)
    with pytest.raises(IntegrityError):
        await repository.add_message(make_message(200, body=body, deleted_at=deleted_at))
    await repository.rollback()


async def test_database_rejects_cross_workspace_channel_pair(session: AsyncSession) -> None:
    repository = SqlAlchemyMessageRepository(session)
    with pytest.raises(IntegrityError):
        await repository.add_message(
            make_message(201, workspace_id=OTHER_WORKSPACE_ID, channel_id=CHANNEL_ID),
        )
    await repository.rollback()


async def test_membership_removal_preserves_history_and_workspace_delete_cascades_it(
    session: AsyncSession,
) -> None:
    repository = SqlAlchemyMessageRepository(session)
    message = make_message(300)
    await repository.add_message(message)
    await repository.commit()
    await session.execute(
        delete(ChannelMembershipRecord).where(
            ChannelMembershipRecord.channel_id == CHANNEL_ID,
            ChannelMembershipRecord.user_id == AUTHOR_ID,
        ),
    )
    await session.execute(
        delete(MembershipRecord).where(
            MembershipRecord.workspace_id == WORKSPACE_ID,
            MembershipRecord.user_id == AUTHOR_ID,
        ),
    )
    await session.commit()
    assert await session.get(MessageRecord, message.id) is not None
    await session.execute(delete(WorkspaceRecord).where(WorkspaceRecord.id == WORKSPACE_ID))
    await session.commit()
    assert await session.get(MessageRecord, message.id) is None

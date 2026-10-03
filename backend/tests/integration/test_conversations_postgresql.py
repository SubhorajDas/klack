"""Concurrent conversation changes on real PostgreSQL."""

import asyncio
import os
from uuid import UUID

import pytest
from sqlalchemy import delete
from test_messages_postgresql import _message_service, _seed_conversation
from test_messages_postgresql import message_container as message_container
from test_messages_postgresql import (
    migrated_message_schema as migrated_message_schema,
)

from klack.core.container import AppContainer
from klack.modules.channels.application.service import ChannelContentAccessService
from klack.modules.channels.infrastructure.models import ChannelMembershipRecord
from klack.modules.channels.infrastructure.repository import SqlAlchemyChannelRepository
from klack.modules.messaging.application.conversations import ConversationService
from klack.modules.messaging.infrastructure.conversations import SqlAlchemyConversationRepository
from klack.modules.messaging.infrastructure.repository import SqlAlchemyMessageRepository
from klack.modules.realtime.infrastructure.repository import SqlAlchemyRealtimeEventRepository
from klack.modules.workspaces.application.service import WorkspaceAccessService
from klack.modules.workspaces.infrastructure.repository import SqlAlchemyWorkspaceRepository

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL"),
]


def conversation(session):
    workspaces = WorkspaceAccessService(SqlAlchemyWorkspaceRepository(session))
    return ConversationService(
        SqlAlchemyConversationRepository(session),
        SqlAlchemyMessageRepository(session),
        ChannelContentAccessService(
            repository=SqlAlchemyChannelRepository(session), workspace_access=workspaces
        ),
        workspaces,
        SqlAlchemyRealtimeEventRepository(session),
    )


async def test_concurrent_direct_creation_reactions_and_cursors(message_container: AppContainer):
    container = message_container
    seeded = await _seed_conversation(container)

    async def open_pair(actor: UUID, target: UUID):
        async with container.session_factory() as session:
            return await conversation(session).open_direct(seeded.workspace_id, actor, target)

    first, second = await asyncio.gather(
        open_pair(seeded.owner_id, seeded.member_id), open_pair(seeded.member_id, seeded.owner_id)
    )
    assert first.channel.id == second.channel.id
    channel_id = first.channel.id
    async with container.session_factory() as session:
        service = _message_service(container, session, realtime=True)
        root = await service.create_message(
            actor_user_id=seeded.owner_id,
            workspace_id=seeded.workspace_id,
            channel_id=channel_id,
            body="Root",
        )

    async def reply(index: int):
        async with container.session_factory() as session:
            return await _message_service(container, session, realtime=True).create_message(
                actor_user_id=seeded.member_id,
                workspace_id=seeded.workspace_id,
                channel_id=channel_id,
                body=f"Reply {index}",
                reply_to_message_id=root.id,
            )

    replies = await asyncio.gather(*(reply(i) for i in range(4)))

    async def react(actor: UUID):
        async with container.session_factory() as session:
            return await conversation(session).react(
                seeded.workspace_id, channel_id, actor, root.id, "👍", True
            )

    await asyncio.gather(react(seeded.owner_id), react(seeded.owner_id), react(seeded.member_id))

    async def read(message_id: UUID):
        async with container.session_factory() as session:
            return await conversation(session).read_state(
                seeded.workspace_id, channel_id, seeded.owner_id, message_id
            )

    await asyncio.gather(*(read(m.id) for m in [*reversed(replies), root]))
    async with container.session_factory() as session:
        state = await conversation(session).read_state(
            seeded.workspace_id, channel_id, seeded.owner_id
        )
        assert state == (max(replies, key=lambda m: (m.created_at, m.id)).id, 0)
        page = await _message_service(container, session).list_messages(
            actor_user_id=seeded.owner_id, workspace_id=seeded.workspace_id, channel_id=channel_id
        )
        assert len(page.messages) == 5
        assert all(m.quote and m.quote.id == root.id for m in page.messages if m.id != root.id)
        original = next(m for m in page.messages if m.id == root.id)
        assert len(original.reactions) == 2
        assert original.revision == 3


async def test_alerts_postgresql_threads_read_snapshot_and_membership(
    message_container: AppContainer,
):
    container = message_container
    seeded = await _seed_conversation(container)
    other = await _seed_conversation(container)
    async with container.session_factory() as session:
        service = _message_service(container, session)
        root = await service.create_message(
            actor_user_id=seeded.owner_id,
            workspace_id=seeded.workspace_id,
            channel_id=seeded.channel_id,
            body="Unread root",
        )
        reply = await service.create_message(
            actor_user_id=seeded.owner_id,
            workspace_id=seeded.workspace_id,
            channel_id=seeded.channel_id,
            body="Unread thread reply",
            reply_to_message_id=root.id,
        )
        await service.create_message(
            actor_user_id=other.owner_id,
            workspace_id=other.workspace_id,
            channel_id=other.channel_id,
            body="Another workspace",
        )
        alerts = await conversation(session).alerts(seeded.workspace_id, seeded.member_id)
        assert len(alerts) == 1
        channel, preview, count = alerts[0]
        assert channel.channel.id == seeded.channel_id
        assert preview.id == reply.id and preview.reply_to_message_id == root.id
        assert count == 2
        assert await conversation(session).alerts(seeded.workspace_id, seeded.owner_id) == []
    # A message committed after the displayed snapshot must survive marking it read.
    async with container.session_factory() as session:
        newest = await _message_service(container, session).create_message(
            actor_user_id=seeded.owner_id,
            workspace_id=seeded.workspace_id,
            channel_id=seeded.channel_id,
            body="Arrived later",
        )
    async with container.session_factory() as session:
        await conversation(session).read_state(
            seeded.workspace_id, seeded.channel_id, seeded.member_id, reply.id
        )
    async with container.session_factory() as session:
        alerts = await conversation(session).alerts(seeded.workspace_id, seeded.member_id)
        assert len(alerts) == 1 and alerts[0][1].id == newest.id and alerts[0][2] == 1
        await session.execute(
            delete(ChannelMembershipRecord).where(
                ChannelMembershipRecord.channel_id == seeded.channel_id,
                ChannelMembershipRecord.user_id == seeded.member_id,
            )
        )
        await session.commit()
    async with container.session_factory() as session:
        assert await conversation(session).alerts(seeded.workspace_id, seeded.member_id) == []

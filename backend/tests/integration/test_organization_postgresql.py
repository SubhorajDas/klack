"""Check mentions, notification policies and concurrent bookmarks on PostgreSQL."""

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from test_conversations_postgresql import conversation
from test_messages_postgresql import _cleanup, _message_service, _seed_conversation
from test_messages_postgresql import message_container as message_container
from test_messages_postgresql import migrated_message_schema as migrated_message_schema

from klack.core.container import AppContainer
from klack.modules.channels.infrastructure.models import ChannelMembershipRecord
from klack.modules.messaging.api.engagement import change_mark, collection
from klack.modules.messaging.infrastructure.models import (
    MentionRecord,
    NotificationPauseRecord,
    NotificationPreferenceRecord,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL"),
]


async def test_organization_persistence_concurrency_and_policy(message_container: AppContainer):
    container = message_container
    seeded = await _seed_conversation(container)
    try:
        async with container.session_factory() as session:
            document = {
                "type": "doc",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "mention",
                                "attrs": {"id": str(seeded.member_id), "label": "member"},
                            }
                        ],
                    }
                ],
            }
            service = _message_service(container, session)
            root = await service.create_message(
                actor_user_id=seeded.owner_id,
                workspace_id=seeded.workspace_id,
                channel_id=seeded.channel_id,
                body="@member",
                document=document,
            )
            await service.create_message(
                actor_user_id=seeded.owner_id,
                workspace_id=seeded.workspace_id,
                channel_id=seeded.channel_id,
                body="Ordinary update",
            )

        async def mark(kind, actor):
            async with container.session_factory() as session:
                return await change_mark(
                    seeded.workspace_id,
                    seeded.channel_id,
                    root.id,
                    kind,
                    True,
                    conversation(session),
                    session,
                    actor,
                )

        await asyncio.gather(
            mark("pin", seeded.owner_id),
            mark("pin", seeded.member_id),
            mark("save", seeded.owner_id),
            mark("save", seeded.owner_id),
        )
        async with container.session_factory() as session:
            assert (
                len((await collection(session, seeded.owner_id, seeded.channel_id, 0, 50)).items)
                == 1
            )
            assert len((await collection(session, seeded.owner_id, None, 0, 50)).items) == 1
            assert (await collection(session, seeded.member_id, None, 0, 50)).items == []
            session.add(
                NotificationPreferenceRecord(
                    channel_id=seeded.channel_id, user_id=seeded.member_id, mode="mentions"
                )
            )
            await session.commit()
            alerts = await conversation(session).global_alerts(seeded.member_id)
            assert len(alerts) == 1 and alerts[0][1].id == root.id and alerts[0][2] == 1
            counts = await conversation(session).unread_counts(seeded.member_id)
            assert counts[0][3] == 2
            session.add(
                NotificationPauseRecord(
                    user_id=seeded.member_id, paused_until=datetime.now(UTC) + timedelta(minutes=30)
                )
            )
            await session.commit()
            assert await conversation(session).global_alerts(seeded.member_id) == []
            await session.execute(
                delete(NotificationPauseRecord).where(
                    NotificationPauseRecord.user_id == seeded.member_id
                )
            )
            await session.commit()
            service = _message_service(container, session)
            await service.edit_message(
                actor_user_id=seeded.owner_id,
                workspace_id=seeded.workspace_id,
                channel_id=seeded.channel_id,
                message_id=root.id,
                body="Updated reference",
            )
            assert (
                list(
                    await session.scalars(
                        select(MentionRecord).where(MentionRecord.message_id == root.id)
                    )
                )
                == []
            )
            assert await conversation(session).global_alerts(seeded.member_id) == []
            assert (await collection(session, seeded.owner_id, None, 0, 50)).items[
                0
            ].message.body == "Updated reference"
            await session.execute(
                delete(ChannelMembershipRecord).where(
                    ChannelMembershipRecord.channel_id == seeded.channel_id,
                    ChannelMembershipRecord.user_id == seeded.owner_id,
                )
            )
            await session.commit()
            assert (await collection(session, seeded.owner_id, None, 0, 50)).items == []
    finally:
        await _cleanup(container, seeded)

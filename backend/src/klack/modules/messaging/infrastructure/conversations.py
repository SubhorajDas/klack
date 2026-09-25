"""Persistence for direct conversations, reactions, and read positions."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from klack.modules.channels.domain.entities import Channel
from klack.modules.channels.infrastructure.models import ChannelMembershipRecord, ChannelRecord
from klack.modules.channels.infrastructure.repository import SqlAlchemyChannelRepository
from klack.modules.messaging.infrastructure.models import (
    MessageRecord,
    ReactionRecord,
    ReadCursorRecord,
)


class SqlAlchemyConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def direct(self, workspace_id: UUID, actor: UUID, target: UUID) -> Channel:
        key = ":".join(sorted([actor.hex, target.hex]))
        row = await self.session.scalar(
            select(ChannelRecord).where(
                ChannelRecord.workspace_id == workspace_id, ChannelRecord.direct_key == key
            )
        )
        if row is None:
            now = datetime.now(UTC)
            row = ChannelRecord(
                id=uuid4(),
                workspace_id=workspace_id,
                name=f"dm-{uuid4().hex}",
                visibility="private",
                direct_key=key,
                created_by_user_id=actor,
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)
            await self.session.flush()
        for user_id in (actor, target):
            member = await self.session.get(ChannelMembershipRecord, (row.id, user_id))
            if member is None:
                self.session.add(
                    ChannelMembershipRecord(
                        workspace_id=workspace_id,
                        channel_id=row.id,
                        user_id=user_id,
                        added_by_user_id=actor,
                        joined_at=datetime.now(UTC),
                    )
                )
        await self.session.flush()
        return SqlAlchemyChannelRepository._channel(row)

    async def list_direct(self, workspace_id: UUID, actor: UUID) -> list[Channel]:
        rows = await self.session.scalars(
            select(ChannelRecord)
            .join(ChannelMembershipRecord, ChannelMembershipRecord.channel_id == ChannelRecord.id)
            .where(
                ChannelRecord.workspace_id == workspace_id,
                ChannelRecord.direct_key.is_not(None),
                ChannelMembershipRecord.user_id == actor,
            )
            .order_by(ChannelRecord.created_at.desc())
        )
        return [SqlAlchemyChannelRepository._channel(row) for row in rows]

    async def reaction(self, message_id: UUID, actor: UUID, emoji: str, add: bool) -> bool:
        row = await self.session.get(ReactionRecord, (message_id, actor, emoji))
        if add and row is None:
            self.session.add(ReactionRecord(message_id=message_id, user_id=actor, emoji=emoji))
        elif not add and row is not None:
            await self.session.delete(row)
        else:
            return False
        await self.session.flush()
        return True

    async def advance(self, channel_id: UUID, actor: UUID, message_id: UUID) -> None:
        cursor = await self.session.get(ReadCursorRecord, (channel_id, actor))
        message = await self.session.get(MessageRecord, message_id)
        assert message is not None
        if cursor is None:
            self.session.add(
                ReadCursorRecord(channel_id=channel_id, user_id=actor, message_id=message_id)
            )
        else:
            previous = await self.session.get(MessageRecord, cursor.message_id)
            if previous is None or (message.created_at, message.id) > (
                previous.created_at,
                previous.id,
            ):
                cursor.message_id = message_id
        await self.session.flush()

    async def read_state(self, channel_id: UUID, actor: UUID) -> tuple[UUID | None, int]:
        cursor = await self.session.get(ReadCursorRecord, (channel_id, actor))
        query = (
            select(func.count())
            .select_from(MessageRecord)
            .where(
                MessageRecord.channel_id == channel_id,
                MessageRecord.author_user_id != actor,
                MessageRecord.deleted_at.is_(None),
            )
        )
        if cursor is not None:
            previous = await self.session.get(MessageRecord, cursor.message_id)
            if previous is not None:
                query = query.where(
                    or_(
                        MessageRecord.created_at > previous.created_at,
                        and_(
                            MessageRecord.created_at == previous.created_at,
                            MessageRecord.id > previous.id,
                        ),
                    )
                )
        return (
            None if cursor is None else cursor.message_id,
            await self.session.scalar(query) or 0,
        )

    async def commit(self) -> None:
        await self.session.commit()

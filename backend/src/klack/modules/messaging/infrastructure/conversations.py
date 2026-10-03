"""Persistence for direct conversations, reactions, and read positions."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.selectable import Subquery

from klack.modules.channels.domain.entities import Channel
from klack.modules.channels.infrastructure.models import (
    ChannelMembershipRecord,
    ChannelRecord,
    DirectAliasRecord,
)
from klack.modules.channels.infrastructure.repository import SqlAlchemyChannelRepository
from klack.modules.identity.infrastructure.models import UserRecord
from klack.modules.messaging.domain.entities import Message
from klack.modules.messaging.infrastructure.models import (
    MessageRecord,
    ReactionRecord,
    ReadCursorRecord,
)
from klack.modules.messaging.infrastructure.repository import SqlAlchemyMessageRepository
from klack.modules.workspaces.domain.entities import WorkspaceMembership, WorkspaceRole
from klack.modules.workspaces.infrastructure.models import MembershipRecord


class SqlAlchemyConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def direct(self, workspace_id: UUID, actor: UUID, target: UUID) -> Channel:
        key = ":".join(sorted([actor.hex, target.hex]))
        # Lock the two identities in order: opposite requests from different workspaces
        # must still create exactly one conversation for this pair.
        await self.session.scalars(
            select(UserRecord)
            .where(UserRecord.id.in_([actor, target]))
            .order_by(UserRecord.id)
            .with_for_update()
        )
        row = await self.session.scalar(
            select(ChannelRecord).where(ChannelRecord.direct_key == key)
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
                        workspace_id=row.workspace_id,
                        channel_id=row.id,
                        user_id=user_id,
                        added_by_user_id=actor,
                        joined_at=datetime.now(UTC),
                    )
                )
        await self.session.flush()
        return SqlAlchemyChannelRepository._channel(row)

    async def list_direct(self, workspace_id: UUID | None, actor: UUID) -> list[Channel]:
        rows = await self.session.scalars(
            select(ChannelRecord)
            .join(ChannelMembershipRecord, ChannelMembershipRecord.channel_id == ChannelRecord.id)
            .where(
                ChannelRecord.direct_key.is_not(None),
                ChannelMembershipRecord.user_id == actor,
            )
            .order_by(ChannelRecord.created_at.desc())
        )
        return [SqlAlchemyChannelRepository._channel(row) for row in rows]

    def _unread(self, workspace_id: UUID | None, actor: UUID) -> Subquery:
        previous = aliased(MessageRecord)
        unread = (
            select(
                MessageRecord.id.label("message_id"),
                func.count().over(partition_by=MessageRecord.channel_id).label("unread_count"),
                func.row_number()
                .over(
                    partition_by=MessageRecord.channel_id,
                    order_by=(MessageRecord.created_at.desc(), MessageRecord.id.desc()),
                )
                .label("position"),
            )
            .join(
                ChannelMembershipRecord,
                and_(
                    ChannelMembershipRecord.channel_id == MessageRecord.channel_id,
                    ChannelMembershipRecord.user_id == actor,
                ),
            )
            .outerjoin(
                ReadCursorRecord,
                and_(
                    ReadCursorRecord.channel_id == MessageRecord.channel_id,
                    ReadCursorRecord.user_id == actor,
                ),
            )
            .outerjoin(previous, previous.id == ReadCursorRecord.message_id)
            .where(
                *([] if workspace_id is None else [MessageRecord.workspace_id == workspace_id]),
                or_(
                    select(ChannelRecord.id)
                    .where(
                        ChannelRecord.id == MessageRecord.channel_id,
                        ChannelRecord.direct_key.is_not(None),
                    )
                    .exists(),
                    select(MembershipRecord.user_id)
                    .where(
                        MembershipRecord.workspace_id == MessageRecord.workspace_id,
                        MembershipRecord.user_id == actor,
                    )
                    .exists(),
                ),
                MessageRecord.author_user_id != actor,
                MessageRecord.deleted_at.is_(None),
                or_(
                    previous.id.is_(None),
                    MessageRecord.created_at > previous.created_at,
                    and_(
                        MessageRecord.created_at == previous.created_at,
                        MessageRecord.id > previous.id,
                    ),
                ),
            )
            .subquery()
        )
        return unread

    async def unread_counts(self, actor: UUID) -> list[tuple[UUID, UUID, bool, int]]:
        unread = self._unread(None, actor)
        rows = (
            await self.session.execute(
                select(
                    ChannelRecord.id,
                    ChannelRecord.workspace_id,
                    ChannelRecord.direct_key,
                    unread.c.unread_count,
                )
                .join(MessageRecord, MessageRecord.channel_id == ChannelRecord.id)
                .join(unread, unread.c.message_id == MessageRecord.id)
                .where(unread.c.position == 1)
            )
        ).all()
        return [
            (channel, workspace, key is not None, count) for channel, workspace, key, count in rows
        ]

    async def alerts(
        self, workspace_id: UUID | None, actor: UUID
    ) -> list[tuple[Channel, Message, int]]:
        unread = self._unread(workspace_id, actor)
        rows = (
            await self.session.execute(
                select(ChannelRecord, MessageRecord, unread.c.unread_count)
                .join(MessageRecord, MessageRecord.channel_id == ChannelRecord.id)
                .join(unread, unread.c.message_id == MessageRecord.id)
                .where(unread.c.position == 1)
                .order_by(MessageRecord.created_at.desc(), MessageRecord.id.desc())
            )
        ).all()
        messages = await SqlAlchemyMessageRepository(self.session)._messages(
            [row[1] for row in rows]
        )
        return [
            (SqlAlchemyChannelRepository._channel(row[0]), message, row[2])
            for row, message in zip(rows, messages, strict=True)
        ]

    async def resolve_direct(self, channel_id: UUID, actor: UUID) -> Channel | None:
        alias = await self.session.get(DirectAliasRecord, channel_id)
        canonical = alias.channel_id if alias else channel_id
        row = await self.session.scalar(
            select(ChannelRecord)
            .join(ChannelMembershipRecord)
            .where(
                ChannelRecord.id == canonical,
                ChannelRecord.direct_key.is_not(None),
                ChannelMembershipRecord.user_id == actor,
            )
        )
        return None if row is None else SqlAlchemyChannelRepository._channel(row)

    async def existing_direct(self, actor: UUID, target: UUID) -> Channel | None:
        row = await self.session.scalar(
            select(ChannelRecord).where(
                ChannelRecord.direct_key == ":".join(sorted([actor.hex, target.hex])),
            )
        )
        return None if row is None else await self.resolve_direct(row.id, actor)

    async def shared_workspace(self, actor: UUID, target: UUID) -> UUID | None:
        peer = aliased(MembershipRecord)
        return await self.session.scalar(
            select(MembershipRecord.workspace_id)
            .join(peer, peer.workspace_id == MembershipRecord.workspace_id)
            .where(MembershipRecord.user_id == actor, peer.user_id == target)
            .order_by(MembershipRecord.workspace_id)
            .limit(1)
        )

    async def contacts(self, actor: UUID) -> list[WorkspaceMembership]:
        mine = aliased(MembershipRecord)
        # Contacts are people in a shared workspace or an existing private conversation.
        direct_ids = select(ChannelMembershipRecord.channel_id).where(
            ChannelMembershipRecord.user_id == actor
        )
        rows = (
            await self.session.execute(
                select(ChannelMembershipRecord, UserRecord.email)
                .join(ChannelRecord, ChannelRecord.id == ChannelMembershipRecord.channel_id)
                .join(UserRecord, UserRecord.id == ChannelMembershipRecord.user_id)
                .where(
                    ChannelRecord.direct_key.is_not(None),
                    ChannelRecord.id.in_(direct_ids),
                    UserRecord.disabled_at.is_(None),
                )
            )
        ).all()
        people = {
            m.user_id: WorkspaceMembership(
                workspace_id=m.workspace_id,
                user_id=m.user_id,
                role=WorkspaceRole.MEMBER,
                joined_at=m.joined_at,
                display_name=email.split("@", 1)[0],
                email=email,
            )
            for m, email in rows
        }
        workspace_rows = (
            await self.session.execute(
                select(MembershipRecord, UserRecord.email)
                .join(UserRecord, UserRecord.id == MembershipRecord.user_id)
                .where(
                    MembershipRecord.workspace_id.in_(
                        select(mine.workspace_id).where(mine.user_id == actor)
                    ),
                    UserRecord.disabled_at.is_(None),
                )
                .order_by(MembershipRecord.workspace_id, MembershipRecord.user_id)
            )
        ).all()
        for m, email in workspace_rows:
            people.setdefault(
                m.user_id,
                WorkspaceMembership(
                    workspace_id=m.workspace_id,
                    user_id=m.user_id,
                    role=WorkspaceRole(m.role),
                    joined_at=m.joined_at,
                    display_name=email.split("@", 1)[0],
                    email=email,
                ),
            )
        return sorted(people.values(), key=lambda m: m.display_name or "")

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

"""Async SQLAlchemy implementation of channel persistence."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

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


class SqlAlchemyChannelRepository:
    """Persist channel state in the request-scoped transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_channel(
        self,
        channel: Channel,
        creator_membership: ChannelMembership,
    ) -> None:
        self._session.add(self._channel_record(channel))
        await self._flush()
        self._session.add(self._membership_record(creator_membership))

    async def list_channels(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        can_view_private: bool,
        include_archived: bool,
    ) -> list[tuple[Channel, ChannelMembership | None]]:
        membership_join = and_(
            ChannelMembershipRecord.channel_id == ChannelRecord.id,
            ChannelMembershipRecord.user_id == actor_user_id,
        )
        statement = (
            select(ChannelRecord, ChannelMembershipRecord)
            .outerjoin(ChannelMembershipRecord, membership_join)
            .where(ChannelRecord.workspace_id == workspace_id)
        )
        if not can_view_private:
            statement = statement.where(
                or_(
                    ChannelRecord.visibility == str(ChannelVisibility.PUBLIC),
                    ChannelMembershipRecord.user_id.is_not(None),
                ),
            )
        if not include_archived:
            statement = statement.where(ChannelRecord.archived_at.is_(None))
        rows = (
            await self._session.execute(
                statement.order_by(ChannelRecord.name, ChannelRecord.id),
            )
        ).all()
        return [
            (
                self._channel(channel_record),
                None if membership_record is None else self._membership(membership_record),
            )
            for channel_record, membership_record in rows
        ]

    async def get_channel(
        self,
        *,
        workspace_id: UUID,
        channel_id: UUID,
        for_update: bool = False,
    ) -> Channel | None:
        statement = select(ChannelRecord).where(
            ChannelRecord.workspace_id == workspace_id,
            ChannelRecord.id == channel_id,
        )
        if for_update:
            statement = statement.with_for_update()
        record = await self._session.scalar(statement)
        return None if record is None else self._channel(record)

    async def update_channel(
        self,
        *,
        channel_id: UUID,
        name: str,
        visibility: ChannelVisibility,
        updated_at: datetime,
    ) -> None:
        try:
            await self._session.execute(
                update(ChannelRecord)
                .where(ChannelRecord.id == channel_id)
                .values(
                    name=name,
                    visibility=str(visibility),
                    updated_at=updated_at,
                ),
            )
        except IntegrityError:
            await self._session.rollback()
            raise ChannelConflict from None

    async def set_channel_archived(
        self,
        *,
        channel_id: UUID,
        archived_at: datetime | None,
        archived_by_user_id: UUID | None,
        updated_at: datetime,
    ) -> None:
        await self._session.execute(
            update(ChannelRecord)
            .where(ChannelRecord.id == channel_id)
            .values(
                archived_at=archived_at,
                archived_by_user_id=archived_by_user_id,
                updated_at=updated_at,
            ),
        )

    async def get_membership(
        self,
        *,
        channel_id: UUID,
        user_id: UUID,
        for_update: bool = False,
    ) -> ChannelMembership | None:
        statement = select(ChannelMembershipRecord).where(
            ChannelMembershipRecord.channel_id == channel_id,
            ChannelMembershipRecord.user_id == user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        record = await self._session.scalar(statement)
        return None if record is None else self._membership(record)

    async def list_memberships(self, *, channel_id: UUID) -> list[ChannelMembership]:
        records = (
            await self._session.scalars(
                select(ChannelMembershipRecord)
                .where(ChannelMembershipRecord.channel_id == channel_id)
                .order_by(
                    ChannelMembershipRecord.joined_at,
                    ChannelMembershipRecord.user_id,
                ),
            )
        ).all()
        return [self._membership(record) for record in records]

    async def add_membership(self, membership: ChannelMembership) -> None:
        self._session.add(self._membership_record(membership))
        await self._flush()

    async def remove_membership(self, *, channel_id: UUID, user_id: UUID) -> None:
        await self._session.execute(
            delete(ChannelMembershipRecord).where(
                ChannelMembershipRecord.channel_id == channel_id,
                ChannelMembershipRecord.user_id == user_id,
            ),
        )

    async def commit(self) -> None:
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            raise ChannelConflict from None

    async def rollback(self) -> None:
        await self._session.rollback()

    async def _flush(self) -> None:
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            raise ChannelConflict from None

    @staticmethod
    def _channel(record: ChannelRecord) -> Channel:
        return Channel(
            id=record.id,
            workspace_id=record.workspace_id,
            name=record.name,
            visibility=ChannelVisibility(record.visibility),
            created_by_user_id=record.created_by_user_id,
            created_at=record.created_at,
            updated_at=record.updated_at,
            archived_at=record.archived_at,
            archived_by_user_id=record.archived_by_user_id,
        )

    @staticmethod
    def _membership(record: ChannelMembershipRecord) -> ChannelMembership:
        return ChannelMembership(
            workspace_id=record.workspace_id,
            channel_id=record.channel_id,
            user_id=record.user_id,
            added_by_user_id=record.added_by_user_id,
            joined_at=record.joined_at,
        )

    @staticmethod
    def _channel_record(channel: Channel) -> ChannelRecord:
        return ChannelRecord(
            id=channel.id,
            workspace_id=channel.workspace_id,
            name=channel.name,
            visibility=str(channel.visibility),
            created_by_user_id=channel.created_by_user_id,
            created_at=channel.created_at,
            updated_at=channel.updated_at,
            archived_at=channel.archived_at,
            archived_by_user_id=channel.archived_by_user_id,
        )

    @staticmethod
    def _membership_record(membership: ChannelMembership) -> ChannelMembershipRecord:
        return ChannelMembershipRecord(
            workspace_id=membership.workspace_id,
            channel_id=membership.channel_id,
            user_id=membership.user_id,
            added_by_user_id=membership.added_by_user_id,
            joined_at=membership.joined_at,
        )

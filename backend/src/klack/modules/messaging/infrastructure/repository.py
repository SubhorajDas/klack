"""Async SQLAlchemy implementation of message persistence."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from klack.modules.messaging.domain.entities import Message
from klack.modules.messaging.infrastructure.models import MessageRecord


class SqlAlchemyMessageRepository:
    """Persist channel messages in the request-scoped transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_message(self, message: Message) -> None:
        self._session.add(self._record(message))
        await self._session.flush()

    async def get_message(
        self,
        *,
        workspace_id: UUID,
        channel_id: UUID,
        message_id: UUID,
        for_update: bool = False,
    ) -> Message | None:
        statement = select(MessageRecord).where(
            MessageRecord.workspace_id == workspace_id,
            MessageRecord.channel_id == channel_id,
            MessageRecord.id == message_id,
        )
        if for_update:
            statement = statement.with_for_update()
        record = await self._session.scalar(statement)
        return None if record is None else self._message(record)

    async def list_messages(
        self,
        *,
        channel_id: UUID,
        before_created_at: datetime | None,
        before_message_id: UUID | None,
        limit: int,
    ) -> list[Message]:
        statement = select(MessageRecord).where(MessageRecord.channel_id == channel_id)
        if before_created_at is not None and before_message_id is not None:
            statement = statement.where(
                or_(
                    MessageRecord.created_at < before_created_at,
                    and_(
                        MessageRecord.created_at == before_created_at,
                        MessageRecord.id < before_message_id,
                    ),
                ),
            )
        records = (
            await self._session.scalars(
                statement.order_by(MessageRecord.created_at.desc(), MessageRecord.id.desc()).limit(
                    limit,
                ),
            )
        ).all()
        return [self._message(record) for record in records]

    async def update_message(
        self,
        *,
        message_id: UUID,
        body: str | None,
        edited_at: datetime | None,
        deleted_at: datetime | None,
    ) -> None:
        await self._session.execute(
            update(MessageRecord)
            .where(MessageRecord.id == message_id)
            .values(body=body, edited_at=edited_at, deleted_at=deleted_at),
        )

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    @staticmethod
    def _message(record: MessageRecord) -> Message:
        return Message(
            id=record.id,
            workspace_id=record.workspace_id,
            channel_id=record.channel_id,
            author_user_id=record.author_user_id,
            body=record.body,
            created_at=record.created_at,
            edited_at=record.edited_at,
            deleted_at=record.deleted_at,
        )

    @staticmethod
    def _record(message: Message) -> MessageRecord:
        return MessageRecord(
            id=message.id,
            workspace_id=message.workspace_id,
            channel_id=message.channel_id,
            author_user_id=message.author_user_id,
            body=message.body,
            created_at=message.created_at,
            edited_at=message.edited_at,
            deleted_at=message.deleted_at,
        )

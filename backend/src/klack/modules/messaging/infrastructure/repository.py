"""Async SQLAlchemy implementation of message persistence."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from klack.modules.files.domain import Attachment
from klack.modules.files.models import FileRecord
from klack.modules.messaging.domain.entities import Message, MessageQuote
from klack.modules.messaging.infrastructure.models import MessageRecord, ReactionRecord


class SqlAlchemyMessageRepository:
    """Persist channel messages in the request-scoped transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_message(self, message: Message) -> None:
        self._session.add(self._record(message))
        await self._session.flush()

    async def add_message_idempotently(self, message: Message) -> bool:
        values = self._record_values(message)
        bind = self._session.get_bind()
        if bind.dialect.name == "postgresql":
            statement = (
                postgresql_insert(MessageRecord)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=["channel_id", "author_user_id", "client_message_id"],
                )
            )
            result = await self._session.execute(statement)
        else:
            sqlite_statement = (
                sqlite_insert(MessageRecord)
                .values(
                    **values,
                )
                .on_conflict_do_nothing(
                    index_elements=["channel_id", "author_user_id", "client_message_id"],
                )
            )
            result = await self._session.execute(sqlite_statement)
        return bool(cast(CursorResult[Any], result).rowcount)

    async def get_message_by_client_id(
        self,
        *,
        channel_id: UUID,
        author_user_id: UUID,
        client_message_id: UUID,
    ) -> Message | None:
        record = await self._session.scalar(
            select(MessageRecord).where(
                MessageRecord.channel_id == channel_id,
                MessageRecord.author_user_id == author_user_id,
                MessageRecord.client_message_id == client_message_id,
            ),
        )
        return None if record is None else await self._message(record)

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
        return None if record is None else await self._message(record)

    async def list_messages(
        self,
        *,
        channel_id: UUID,
        before_created_at: datetime | None,
        before_message_id: UUID | None,
        limit: int,
    ) -> list[Message]:
        statement = select(MessageRecord).where(
            MessageRecord.channel_id == channel_id,
        )
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
        return await self._messages(records)

    async def message_context(self, anchor: Message, limit: int) -> list[Message]:
        """Read a bounded window around an authorized message, including tombstones."""
        older = await self.list_messages(
            channel_id=anchor.channel_id,
            before_created_at=anchor.created_at,
            before_message_id=anchor.id,
            limit=limit,
        )
        records = (
            await self._session.scalars(
                select(MessageRecord)
                .where(
                    MessageRecord.channel_id == anchor.channel_id,
                    or_(
                        MessageRecord.created_at > anchor.created_at,
                        and_(
                            MessageRecord.created_at == anchor.created_at,
                            MessageRecord.id > anchor.id,
                        ),
                    ),
                )
                .order_by(MessageRecord.created_at, MessageRecord.id)
                .limit(limit)
            )
        ).all()
        newer = await self._messages(records)
        return [*reversed(newer), anchor, *older]

    async def update_message(
        self,
        *,
        message_id: UUID,
        body: str | None,
        edited_at: datetime | None,
        deleted_at: datetime | None,
        revision: int = 1,
    ) -> None:
        await self._session.execute(
            update(MessageRecord)
            .where(MessageRecord.id == message_id)
            .values(
                body=body,
                edited_at=edited_at,
                deleted_at=deleted_at,
                revision=revision,
                **({"attachment_count": 0} if deleted_at is not None else {}),
            ),
        )

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    async def _message(self, record: MessageRecord) -> Message:
        return (await self._messages([record]))[0]

    async def _messages(self, records: Sequence[MessageRecord]) -> list[Message]:
        if not records:
            return []
        ids = [row.id for row in records]
        files: dict[UUID, list[Attachment]] = defaultdict(list)
        for file in (
            await self._session.scalars(
                select(FileRecord)
                .where(FileRecord.message_id.in_(ids), FileRecord.status == "attached")
                .order_by(FileRecord.position)
            )
        ).all():
            if file.message_id is not None:
                files[file.message_id].append(file.attachment())
        reactions: dict[UUID, list[tuple[str, UUID]]] = defaultdict(list)
        for message_id, emoji, user_id in (
            await self._session.execute(
                select(ReactionRecord.message_id, ReactionRecord.emoji, ReactionRecord.user_id)
                .where(ReactionRecord.message_id.in_(ids))
                .order_by(ReactionRecord.emoji, ReactionRecord.user_id)
            )
        ).all():
            reactions[message_id].append((emoji, user_id))
        target_ids = {row.reply_to_message_id for row in records if row.reply_to_message_id}
        targets = (
            (
                await self._session.scalars(
                    select(MessageRecord).where(MessageRecord.id.in_(target_ids))
                )
            ).all()
            if target_ids
            else []
        )
        quotes = {
            (row.channel_id, row.id): MessageQuote(
                id=row.id,
                author_user_id=row.author_user_id,
                body=None if row.deleted_at else (row.body or "")[:240],
                deleted_at=row.deleted_at,
                revision=row.revision,
                attachment_count=0 if row.deleted_at else row.attachment_count,
            )
            for row in targets
        }
        return [
            self._domain(
                row,
                tuple(reactions[row.id]),
                quotes.get((row.channel_id, row.reply_to_message_id))
                if row.reply_to_message_id
                else None,
                tuple(files[row.id]),
            )
            for row in records
        ]

    @staticmethod
    def _domain(
        record: MessageRecord,
        reactions: tuple[tuple[str, UUID], ...],
        quote: MessageQuote | None,
        attachments: tuple[Attachment, ...] = (),
    ) -> Message:
        return Message(
            attachments=attachments if record.deleted_at is None else (),
            reply_to_message_id=record.reply_to_message_id,
            reactions=reactions if record.deleted_at is None else (),
            quote=quote if record.deleted_at is None else None,
            id=record.id,
            workspace_id=record.workspace_id,
            channel_id=record.channel_id,
            author_user_id=record.author_user_id,
            body=record.body,
            created_at=record.created_at,
            edited_at=record.edited_at,
            deleted_at=record.deleted_at,
            client_message_id=record.client_message_id,
            revision=record.revision,
        )

    @staticmethod
    def _record(message: Message) -> MessageRecord:
        return MessageRecord(**SqlAlchemyMessageRepository._record_values(message))

    @staticmethod
    def _record_values(message: Message) -> dict[str, object]:
        return {
            "attachment_count": len(message.attachments),
            "reply_to_message_id": message.reply_to_message_id,
            "id": message.id,
            "workspace_id": message.workspace_id,
            "channel_id": message.channel_id,
            "author_user_id": message.author_user_id,
            "body": message.body,
            "created_at": message.created_at,
            "edited_at": message.edited_at,
            "deleted_at": message.deleted_at,
            "client_message_id": message.client_message_id,
            "revision": message.revision,
        }

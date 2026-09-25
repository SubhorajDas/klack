"""Transactional realtime event persistence and PostgreSQL notification."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from klack.modules.messaging.domain.entities import Message
from klack.modules.realtime.domain.entities import RealtimeEvent
from klack.modules.realtime.infrastructure.models import RealtimeEventRecord

REALTIME_NOTIFY_CHANNEL = "klack_realtime_events"


class SqlAlchemyRealtimeEventRepository:
    """Append and read body-free events using an existing transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append_message_changed(self, message: Message) -> None:
        event = RealtimeEvent(
            id=uuid4(),
            event_type="message.changed",
            workspace_id=message.workspace_id,
            channel_id=message.channel_id,
            entity_id=message.id,
            entity_revision=message.revision,
            occurred_at=datetime.now(UTC),
        )
        self._session.add(self._record(event))
        await self._session.flush()
        await self._session.execute(
            select(func.pg_notify(REALTIME_NOTIFY_CHANNEL, str(event.id))),
        )

    async def get_event(self, event_id: UUID) -> RealtimeEvent | None:
        record = await self._session.get(RealtimeEventRecord, event_id)
        return None if record is None else self._event(record)

    async def delete_events_before(self, *, before: datetime, limit: int) -> int:
        event_ids = (
            select(RealtimeEventRecord.id)
            .where(RealtimeEventRecord.occurred_at < before)
            .order_by(RealtimeEventRecord.occurred_at, RealtimeEventRecord.id)
            .limit(limit)
        )
        result = await self._session.execute(
            delete(RealtimeEventRecord).where(RealtimeEventRecord.id.in_(event_ids)),
        )
        await self._session.commit()
        return int(getattr(result, "rowcount", 0))

    @staticmethod
    def _record(event: RealtimeEvent) -> RealtimeEventRecord:
        return RealtimeEventRecord(
            id=event.id,
            event_type=event.event_type,
            workspace_id=event.workspace_id,
            channel_id=event.channel_id,
            entity_id=event.entity_id,
            entity_revision=event.entity_revision,
            occurred_at=event.occurred_at,
        )

    @staticmethod
    def _event(record: RealtimeEventRecord) -> RealtimeEvent:
        return RealtimeEvent(
            id=record.id,
            event_type=record.event_type,
            workspace_id=record.workspace_id,
            channel_id=record.channel_id,
            entity_id=record.entity_id,
            entity_revision=record.entity_revision,
            occurred_at=record.occurred_at,
        )

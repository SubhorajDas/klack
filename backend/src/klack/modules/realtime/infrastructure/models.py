"""SQLAlchemy records owned by realtime delivery."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from klack.core.db.base import Base


class RealtimeEventRecord(Base):
    """A body-free transactional signal for a durable entity change."""

    __tablename__ = "realtime_events"
    __table_args__ = (
        CheckConstraint("entity_revision >= 1", name="positive_entity_revision"),
        Index("ix_realtime_events_occurred_at", "occurred_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    channel_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    entity_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    entity_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

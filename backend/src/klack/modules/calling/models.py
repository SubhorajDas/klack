"""Durable call history and exclusive participant reservations."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from klack.core.db.base import Base


class CallRecord(Base):
    __tablename__ = "calling_calls"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "channel_id"],
            ["channel_channels.workspace_id", "channel_channels.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("caller_id <> callee_id", name="different_participants"),
        CheckConstraint(
            "status IN ('ringing','active','declined','missed','cancelled','ended')",
            name="supported_status",
        ),
        Index("ix_calling_calls_channel_created", "channel_id", "created_at"),
        Index("ix_calling_calls_status", "status"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    channel_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    caller_id: Mapped[UUID] = mapped_column(ForeignKey("identity_users.id"), nullable=False)
    callee_id: Mapped[UUID] = mapped_column(ForeignKey("identity_users.id"), nullable=False)
    caller_session: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    callee_session: Mapped[UUID | None] = mapped_column(Uuid)
    caller_device: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    callee_device: Mapped[UUID | None] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    caller_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    callee_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleanup_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class CallSeatRecord(Base):
    __tablename__ = "calling_seats"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("identity_users.id"), primary_key=True)
    call_id: Mapped[UUID] = mapped_column(
        ForeignKey("calling_calls.id", ondelete="CASCADE"), nullable=False, index=True
    )

"""SQLAlchemy persistence records owned by the messaging module."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from klack.core.db.base import Base


class MessageRecord(Base):
    """A durable live message or content-free deletion tombstone."""

    __tablename__ = "message_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "channel_id"],
            ["channel_channels.workspace_id", "channel_channels.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "((deleted_at IS NULL AND body IS NOT NULL "
            "AND char_length(body) <= 4000 AND (trim(body) <> '' OR attachment_count > 0)) "
            "OR (deleted_at IS NOT NULL AND body IS NULL AND attachment_count = 0))",
            name="live_body_or_deleted_tombstone",
        ),
        CheckConstraint(
            "edited_at IS NULL OR edited_at >= created_at",
            name="edit_not_before_creation",
        ),
        CheckConstraint(
            "deleted_at IS NULL OR deleted_at >= created_at",
            name="deletion_not_before_creation",
        ),
        Index(
            "ix_message_messages_channel_id_created_at_id",
            "channel_id",
            "created_at",
            "id",
        ),
        UniqueConstraint(
            "channel_id",
            "author_user_id",
            "client_message_id",
        ),
        CheckConstraint("revision >= 1", name="positive_revision"),
        CheckConstraint("attachment_count BETWEEN 0 AND 5", name="attachment_count_range"),
        UniqueConstraint("channel_id", "id"),
        ForeignKeyConstraint(
            ["channel_id", "reply_to_message_id"],
            ["message_messages.channel_id", "message_messages.id"],
        ),
        Index("ix_message_messages_reply_to", "reply_to_message_id", "created_at", "id"),
    )

    attachment_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    reply_to_message_id: Mapped[UUID | None] = mapped_column(Uuid)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    channel_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    author_user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("identity_users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    body: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    client_message_id: Mapped[UUID | None] = mapped_column(Uuid)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ReactionRecord(Base):
    """One user's reaction, unique per message and emoji."""

    __tablename__ = "message_reactions"
    message_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("message_messages.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("identity_users.id", ondelete="CASCADE"), primary_key=True
    )
    emoji: Mapped[str] = mapped_column(Text, primary_key=True)


class ReadCursorRecord(Base):
    """A member's monotonic position in channel history."""

    __tablename__ = "message_read_cursors"
    __table_args__ = (
        ForeignKeyConstraint(
            ["channel_id", "user_id"],
            ["channel_memberships.channel_id", "channel_memberships.user_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["channel_id", "message_id"], ["message_messages.channel_id", "message_messages.id"]
        ),
    )
    channel_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    message_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)

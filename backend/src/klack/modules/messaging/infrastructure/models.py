"""SQLAlchemy persistence records owned by the messaging module."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
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
            "AND char_length(body) BETWEEN 1 AND 4000 AND trim(body) <> '') "
            "OR (deleted_at IS NOT NULL AND body IS NULL))",
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
    )

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

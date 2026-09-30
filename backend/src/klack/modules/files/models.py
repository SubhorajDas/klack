"""Durable file reservations, links, and deletion work."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from klack.core.db.base import Base
from klack.modules.files.domain import Attachment


class FileRecord(Base):
    __tablename__ = "file_uploads"
    __table_args__ = (
        CheckConstraint("size > 0", name="positive_size"),
        CheckConstraint(
            "status IN ('pending','uploading','ready','attached','deleting','removed')",
            name="valid_status",
        ),
        Index("ix_file_uploads_channel_created", "channel_id", "created_at", "id"),
        Index("ix_file_uploads_cleanup", "status", "expires_at"),
        Index("ix_file_uploads_message_id", "message_id"),
        Index("ix_file_uploads_workspace_id", "workspace_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspace_workspaces.id", ondelete="RESTRICT")
    )
    channel_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("channel_channels.id", ondelete="RESTRICT")
    )
    uploader_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("identity_users.id", ondelete="RESTRICT")
    )
    message_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("message_messages.id", ondelete="RESTRICT")
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    filename: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger)
    content_type: Mapped[str] = mapped_column(Text, default="application/octet-stream")
    storage_key: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def attachment(self) -> Attachment:
        return Attachment(self.id, self.filename, self.size, self.content_type)

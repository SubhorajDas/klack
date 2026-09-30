"""Persistence-agnostic channel message values."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from klack.modules.files.domain import Attachment


@dataclass(frozen=True, slots=True)
class Message:
    """One durable message in a workspace channel."""

    id: UUID
    workspace_id: UUID
    channel_id: UUID
    author_user_id: UUID
    body: str | None
    created_at: datetime
    edited_at: datetime | None
    deleted_at: datetime | None
    client_message_id: UUID | None = None
    revision: int = 1
    parent_message_id: UUID | None = None
    reactions: tuple[tuple[str, UUID], ...] = ()
    reply_count: int = 0
    attachments: tuple[Attachment, ...] = ()

    @property
    def is_deleted(self) -> bool:
        """Return whether the message is a content-free tombstone."""
        return self.deleted_at is not None

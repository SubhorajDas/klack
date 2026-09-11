"""Persistence-agnostic channel message values."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


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

    @property
    def is_deleted(self) -> bool:
        """Return whether the message is a content-free tombstone."""
        return self.deleted_at is not None

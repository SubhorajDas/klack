"""Persistence-agnostic channel message values."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from klack.modules.files.domain import Attachment


@dataclass(frozen=True, slots=True)
class MessageQuote:
    """Current, shallow preview of a referenced message; never a copied body."""

    id: UUID
    author_user_id: UUID
    body: str | None
    deleted_at: datetime | None
    revision: int
    attachment_count: int = 0

    @classmethod
    def from_message(cls, message: "Message") -> "MessageQuote":
        return cls(
            id=message.id,
            author_user_id=message.author_user_id,
            body=None if message.is_deleted else (message.body or "")[:240],
            deleted_at=message.deleted_at,
            revision=message.revision,
            attachment_count=0 if message.is_deleted else len(message.attachments),
        )


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
    reply_to_message_id: UUID | None = None
    reactions: tuple[tuple[str, UUID], ...] = ()
    quote: MessageQuote | None = None
    attachments: tuple[Attachment, ...] = ()

    @property
    def is_deleted(self) -> bool:
        """Return whether the message is a content-free tombstone."""
        return self.deleted_at is not None

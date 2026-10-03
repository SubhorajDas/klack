"""Public request and response contracts for message endpoints."""

from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from klack.modules.files.domain import Attachment
from klack.modules.messaging.application.service import MessagePage
from klack.modules.messaging.domain.entities import Message


class StrictRequest(BaseModel):
    """Reject unexpected fields on message mutation requests."""

    model_config = ConfigDict(extra="forbid")


MessageBody = Annotated[str, Field(min_length=1, max_length=4_000)]


class CreateMessageRequest(StrictRequest):
    """Create a message in one channel."""

    body: str = Field(default="", max_length=4_000)
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=5)
    client_message_id: UUID | None = None
    reply_to_message_id: UUID | None = None

    @model_validator(mode="after")
    def require_content(self) -> Self:
        if not self.body.strip() and not self.attachment_ids:
            raise ValueError("A message needs text or an attachment.")
        if len(set(self.attachment_ids)) != len(self.attachment_ids):
            raise ValueError("Duplicate attachments are not allowed.")
        return self


class UpdateMessageRequest(StrictRequest):
    """Replace the body of an existing message."""

    body: str = Field(max_length=4_000)


class QuoteResponse(BaseModel):
    """A bounded preview with no recursive quoted content."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    author_user_id: UUID
    body: str | None
    deleted_at: datetime | None
    revision: int
    attachment_count: int


class MessageResponse(BaseModel):
    """A live message or a deleted-message tombstone."""

    id: UUID
    workspace_id: UUID
    channel_id: UUID
    author_user_id: UUID
    body: str | None
    created_at: datetime
    edited_at: datetime | None
    deleted_at: datetime | None
    client_message_id: UUID | None
    revision: int
    reply_to_message_id: UUID | None
    reactions: list[tuple[str, UUID]]
    quote: QuoteResponse | None
    attachments: list[Attachment]

    @classmethod
    def from_domain(cls, message: Message) -> Self:
        return cls(
            attachments=list(message.attachments) if not message.is_deleted else [],
            id=message.id,
            workspace_id=message.workspace_id,
            channel_id=message.channel_id,
            author_user_id=message.author_user_id,
            body=message.body,
            created_at=message.created_at,
            edited_at=message.edited_at,
            deleted_at=message.deleted_at,
            client_message_id=message.client_message_id,
            revision=message.revision,
            reply_to_message_id=message.reply_to_message_id,
            reactions=list(message.reactions),
            quote=QuoteResponse.model_validate(message.quote)
            if message.quote and not message.is_deleted
            else None,
        )


class MessagesResponse(BaseModel):
    """One reverse-chronological channel-history page."""

    messages: list[MessageResponse]
    next_before: UUID | None

    @classmethod
    def from_page(cls, page: MessagePage) -> Self:
        return cls(
            messages=[MessageResponse.from_domain(message) for message in page.messages],
            next_before=page.next_before,
        )

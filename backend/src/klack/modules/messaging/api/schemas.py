"""Public request and response contracts for message endpoints."""

from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from klack.modules.messaging.application.service import MessagePage
from klack.modules.messaging.domain.entities import Message


class StrictRequest(BaseModel):
    """Reject unexpected fields on message mutation requests."""

    model_config = ConfigDict(extra="forbid")


MessageBody = Annotated[str, Field(min_length=1, max_length=4_000)]


class CreateMessageRequest(StrictRequest):
    """Create a message in one channel."""

    body: MessageBody


class UpdateMessageRequest(StrictRequest):
    """Replace the body of an existing message."""

    body: MessageBody


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

    @classmethod
    def from_domain(cls, message: Message) -> Self:
        return cls(
            id=message.id,
            workspace_id=message.workspace_id,
            channel_id=message.channel_id,
            author_user_id=message.author_user_id,
            body=message.body,
            created_at=message.created_at,
            edited_at=message.edited_at,
            deleted_at=message.deleted_at,
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

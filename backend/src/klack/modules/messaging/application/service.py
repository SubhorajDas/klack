"""Channel message creation, history, editing, deletion, and authorization."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from klack.modules.channels.domain.errors import ChannelArchived
from klack.modules.messaging.application.ports import (
    ChannelContentAccessGateway,
    MessageRepository,
)
from klack.modules.messaging.domain.entities import Message
from klack.modules.messaging.domain.errors import (
    InvalidMessageBody,
    InvalidMessageCursor,
    MessageDeleted,
    MessageNotFound,
    MessagePermissionDenied,
)


def utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class MessagePolicy:
    """Validated message body and history-page bounds."""

    maximum_body_length: int = 4_000
    default_page_size: int = 50
    maximum_page_size: int = 100

    def __post_init__(self) -> None:
        if self.maximum_body_length < 1:
            raise ValueError("maximum_body_length must be positive")
        if not 1 <= self.default_page_size <= self.maximum_page_size:
            raise ValueError("default_page_size must be within the supported page range")


@dataclass(frozen=True, slots=True)
class MessagePage:
    """One reverse-chronological page and its continuation cursor."""

    messages: list[Message]
    next_before: UUID | None


class MessageService:
    """Own message transaction intent independently from HTTP and persistence."""

    def __init__(
        self,
        *,
        repository: MessageRepository,
        channel_access: ChannelContentAccessGateway,
        policy: MessagePolicy | None = None,
        clock: Callable[[], datetime] = utc_now,
        uuid_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._repository = repository
        self._channel_access = channel_access
        self._policy = policy or MessagePolicy()
        self._clock = clock
        self._uuid_factory = uuid_factory

    async def create_message(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
        body: str,
    ) -> Message:
        """Persist a message after locking and rechecking channel membership."""
        self._validate_body(body)
        channel = await self._channel_access.require_access(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
            for_update=True,
        )
        if channel.is_archived:
            await self._repository.rollback()
            raise ChannelArchived
        message = Message(
            id=self._uuid_factory(),
            workspace_id=workspace_id,
            channel_id=channel_id,
            author_user_id=actor_user_id,
            body=body,
            created_at=self._clock(),
            edited_at=None,
            deleted_at=None,
        )
        await self._repository.add_message(message)
        await self._repository.commit()
        return message

    async def list_messages(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
        before: UUID | None = None,
        limit: int | None = None,
    ) -> MessagePage:
        """Return a stable reverse-chronological page of channel history."""
        await self._channel_access.require_access(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
            for_update=False,
        )
        page_size = self._policy.default_page_size if limit is None else limit
        if not 1 <= page_size <= self._policy.maximum_page_size:
            raise ValueError("limit is outside the supported page range")
        cursor = None
        if before is not None:
            cursor = await self._repository.get_message(
                workspace_id=workspace_id,
                channel_id=channel_id,
                message_id=before,
            )
            if cursor is None:
                raise InvalidMessageCursor
        rows = await self._repository.list_messages(
            channel_id=channel_id,
            before_created_at=None if cursor is None else cursor.created_at,
            before_message_id=None if cursor is None else cursor.id,
            limit=page_size + 1,
        )
        has_more = len(rows) > page_size
        messages = rows[:page_size]
        next_before = messages[-1].id if has_more and messages else None
        return MessagePage(messages=messages, next_before=next_before)

    async def edit_message(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
        message_id: UUID,
        body: str,
    ) -> Message:
        """Edit a live message owned by the current channel member."""
        self._validate_body(body)
        channel = await self._channel_access.require_access(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
            for_update=True,
        )
        if channel.is_archived:
            await self._repository.rollback()
            raise ChannelArchived
        message = await self._owned_message(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
            message_id=message_id,
        )
        if message.is_deleted:
            await self._repository.rollback()
            raise MessageDeleted
        if message.body == body:
            await self._repository.rollback()
            return message
        edited_at = self._clock()
        await self._repository.update_message(
            message_id=message_id,
            body=body,
            edited_at=edited_at,
            deleted_at=None,
        )
        await self._repository.commit()
        return replace(message, body=body, edited_at=edited_at)

    async def delete_message(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
        message_id: UUID,
    ) -> None:
        """Idempotently replace an owned message's content with a tombstone."""
        await self._channel_access.require_access(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
            for_update=True,
        )
        message = await self._owned_message(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
            message_id=message_id,
        )
        if message.is_deleted:
            await self._repository.rollback()
            return
        await self._repository.update_message(
            message_id=message_id,
            body=None,
            edited_at=message.edited_at,
            deleted_at=self._clock(),
        )
        await self._repository.commit()

    async def _owned_message(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
        message_id: UUID,
    ) -> Message:
        message = await self._repository.get_message(
            workspace_id=workspace_id,
            channel_id=channel_id,
            message_id=message_id,
            for_update=True,
        )
        if message is None:
            await self._repository.rollback()
            raise MessageNotFound
        if message.author_user_id != actor_user_id:
            await self._repository.rollback()
            raise MessagePermissionDenied
        return message

    def _validate_body(self, body: str) -> None:
        if not body.strip() or len(body) > self._policy.maximum_body_length:
            raise InvalidMessageBody

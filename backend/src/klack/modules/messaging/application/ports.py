"""Persistence and channel-authorization ports used by messaging services."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from klack.modules.channels.domain.entities import Channel
from klack.modules.messaging.domain.entities import Message
from klack.modules.workspaces.domain.entities import WorkspaceMembership


class ChannelContentAccessGateway(Protocol):
    """Narrow cross-module gateway for explicit channel-content access."""

    async def require_access(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
        for_update: bool,
    ) -> Channel: ...


class MessageEventWriter(Protocol):
    """Append a realtime signal in the message transaction."""

    async def append_message_changed(self, message: Message) -> None: ...


class NullMessageEventWriter:
    """Disable realtime signaling without changing message correctness."""

    async def append_message_changed(self, message: Message) -> None:
        del message


class MessageRepository(Protocol):
    """Transactions and durable state required by message use cases."""

    async def add_message(self, message: Message) -> None: ...

    async def add_message_idempotently(self, message: Message) -> bool: ...

    async def get_message_by_client_id(
        self,
        *,
        channel_id: UUID,
        author_user_id: UUID,
        client_message_id: UUID,
    ) -> Message | None: ...

    async def get_message(
        self,
        *,
        workspace_id: UUID,
        channel_id: UUID,
        message_id: UUID,
        for_update: bool = False,
    ) -> Message | None: ...

    async def list_messages(
        self,
        *,
        channel_id: UUID,
        before_created_at: datetime | None,
        before_message_id: UUID | None,
        limit: int,
    ) -> list[Message]: ...

    async def message_context(self, anchor: Message, limit: int) -> list[Message]: ...

    async def update_message(
        self,
        *,
        message_id: UUID,
        body: str | None,
        edited_at: datetime | None,
        deleted_at: datetime | None,
        revision: int = 1,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class ConversationRepository(Protocol):
    """Conversation state updated under the workspace/channel authorization locks."""

    async def direct(self, workspace_id: UUID, actor: UUID, target: UUID) -> Channel: ...
    async def list_direct(self, workspace_id: UUID | None, actor: UUID) -> list[Channel]: ...
    async def alerts(
        self, workspace_id: UUID | None, actor: UUID
    ) -> list[tuple[Channel, Message, int]]: ...
    async def resolve_direct(self, channel_id: UUID, actor: UUID) -> Channel | None: ...
    async def existing_direct(self, actor: UUID, target: UUID) -> Channel | None: ...
    async def shared_workspace(self, actor: UUID, target: UUID) -> UUID | None: ...
    async def unread_counts(self, actor: UUID) -> list[tuple[UUID, UUID, bool, int]]: ...
    async def contacts(self, actor: UUID) -> list[WorkspaceMembership]: ...
    async def reaction(self, message_id: UUID, actor: UUID, emoji: str, add: bool) -> bool: ...
    async def advance(self, channel_id: UUID, actor: UUID, message_id: UUID) -> None: ...
    async def read_state(self, channel_id: UUID, actor: UUID) -> tuple[UUID | None, int]: ...
    async def commit(self) -> None: ...

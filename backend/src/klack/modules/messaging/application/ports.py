"""Persistence and channel-authorization ports used by messaging services."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from klack.modules.channels.domain.entities import Channel
from klack.modules.messaging.domain.entities import Message


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


class MessageRepository(Protocol):
    """Transactions and durable state required by message use cases."""

    async def add_message(self, message: Message) -> None: ...

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

    async def update_message(
        self,
        *,
        message_id: UUID,
        body: str | None,
        edited_at: datetime | None,
        deleted_at: datetime | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

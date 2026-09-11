"""Persistence and workspace-authorization ports used by channel services."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from klack.modules.channels.domain.entities import (
    Channel,
    ChannelMembership,
    ChannelVisibility,
)
from klack.modules.workspaces.domain.entities import WorkspaceMembership


class ChannelConflict(Exception):
    """A database constraint rejected channel state."""


class WorkspaceAccessGateway(Protocol):
    """Narrow cross-module access to durable workspace authorization."""

    async def require_membership(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        for_update: bool,
    ) -> WorkspaceMembership: ...

    async def get_membership(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        for_update: bool,
    ) -> WorkspaceMembership | None: ...


class ChannelRepository(Protocol):
    """Transactions and durable state required by channel use cases."""

    async def add_channel(
        self,
        channel: Channel,
        creator_membership: ChannelMembership,
    ) -> None: ...

    async def list_channels(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        can_view_private: bool,
        include_archived: bool,
    ) -> list[tuple[Channel, ChannelMembership | None]]: ...

    async def get_channel(
        self,
        *,
        workspace_id: UUID,
        channel_id: UUID,
        for_update: bool = False,
    ) -> Channel | None: ...

    async def update_channel(
        self,
        *,
        channel_id: UUID,
        name: str,
        visibility: ChannelVisibility,
        updated_at: datetime,
    ) -> None: ...

    async def set_channel_archived(
        self,
        *,
        channel_id: UUID,
        archived_at: datetime | None,
        archived_by_user_id: UUID | None,
        updated_at: datetime,
    ) -> None: ...

    async def get_membership(
        self,
        *,
        channel_id: UUID,
        user_id: UUID,
        for_update: bool = False,
    ) -> ChannelMembership | None: ...

    async def list_memberships(
        self,
        *,
        channel_id: UUID,
    ) -> list[ChannelMembership]: ...

    async def add_membership(self, membership: ChannelMembership) -> None: ...

    async def remove_membership(
        self,
        *,
        channel_id: UUID,
        user_id: UUID,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

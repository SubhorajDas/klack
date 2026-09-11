"""Channel lifecycle, visibility, membership, and authorization use cases."""

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from klack.modules.channels.application.ports import (
    ChannelConflict,
    ChannelRepository,
    WorkspaceAccessGateway,
)
from klack.modules.channels.domain.entities import (
    Channel,
    ChannelMembership,
    ChannelVisibility,
)
from klack.modules.channels.domain.errors import (
    ChannelArchived,
    ChannelMembershipNotFound,
    ChannelNameConflict,
    ChannelNotFound,
    ChannelPermissionDenied,
    InvalidChannelName,
    TargetWorkspaceMembershipNotFound,
)
from klack.modules.workspaces.domain.entities import WorkspaceMembership, WorkspaceRole

CHANNEL_NAME_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
CHANNEL_MANAGER_ROLES = frozenset({WorkspaceRole.OWNER, WorkspaceRole.ADMIN})


def utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ChannelPolicy:
    """Validated bounds and canonical syntax for channel names."""

    minimum_name_length: int = 1
    maximum_name_length: int = 80

    def __post_init__(self) -> None:
        if self.minimum_name_length < 1:
            raise ValueError("minimum_name_length must be positive")
        if self.maximum_name_length < self.minimum_name_length:
            raise ValueError("maximum_name_length must not be below minimum_name_length")


@dataclass(frozen=True, slots=True)
class ChannelView:
    """A visible channel plus the requesting actor's joined state."""

    channel: Channel
    is_member: bool


class ChannelContentAccessService:
    """Authorize access to channel content through explicit membership."""

    def __init__(
        self,
        *,
        repository: ChannelRepository,
        workspace_access: WorkspaceAccessGateway,
    ) -> None:
        self._repository = repository
        self._workspace_access = workspace_access

    async def require_access(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
        for_update: bool,
    ) -> Channel:
        """Return a channel only when the actor is an explicit channel member."""
        actor = await self._workspace_access.require_membership(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            for_update=for_update,
        )
        channel = await self._repository.get_channel(
            workspace_id=workspace_id,
            channel_id=channel_id,
            for_update=for_update,
        )
        if channel is None:
            await self._rollback_if_locked(for_update)
            raise ChannelNotFound
        membership = await self._repository.get_membership(
            channel_id=channel_id,
            user_id=actor_user_id,
            for_update=for_update,
        )
        can_view = (
            channel.visibility is ChannelVisibility.PUBLIC
            or membership is not None
            or actor.role in CHANNEL_MANAGER_ROLES
        )
        if not can_view:
            await self._rollback_if_locked(for_update)
            raise ChannelNotFound
        if membership is None:
            await self._rollback_if_locked(for_update)
            raise ChannelPermissionDenied
        return channel

    async def _rollback_if_locked(self, for_update: bool) -> None:
        if for_update:
            await self._repository.rollback()


class ChannelService:
    """Own channel transaction intent independently from HTTP and persistence."""

    def __init__(
        self,
        *,
        repository: ChannelRepository,
        workspace_access: WorkspaceAccessGateway,
        policy: ChannelPolicy | None = None,
        clock: Callable[[], datetime] = utc_now,
        uuid_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._repository = repository
        self._workspace_access = workspace_access
        self._policy = policy or ChannelPolicy()
        self._clock = clock
        self._uuid_factory = uuid_factory

    async def create_channel(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        name: str,
        visibility: ChannelVisibility,
    ) -> ChannelView:
        """Create a channel and join its creator in one transaction."""
        normalized_name = self._normalize_name(name)
        actor = await self._workspace_access.require_membership(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            for_update=True,
        )
        await self._require_manager(actor)
        now = self._clock()
        channel = Channel(
            id=self._uuid_factory(),
            workspace_id=workspace_id,
            name=normalized_name,
            visibility=visibility,
            created_by_user_id=actor_user_id,
            created_at=now,
            updated_at=now,
            archived_at=None,
            archived_by_user_id=None,
        )
        creator_membership = ChannelMembership(
            workspace_id=workspace_id,
            channel_id=channel.id,
            user_id=actor_user_id,
            added_by_user_id=actor_user_id,
            joined_at=now,
        )
        try:
            await self._repository.add_channel(channel, creator_membership)
            await self._repository.commit()
        except ChannelConflict:
            await self._repository.rollback()
            raise ChannelNameConflict from None
        return ChannelView(channel=channel, is_member=True)

    async def list_channels(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        include_archived: bool = False,
    ) -> list[ChannelView]:
        """List public, joined private, and manager-visible channels."""
        actor = await self._workspace_access.require_membership(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            for_update=False,
        )
        rows = await self._repository.list_channels(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            can_view_private=self._is_manager(actor),
            include_archived=include_archived,
        )
        return [
            ChannelView(channel=channel, is_member=membership is not None)
            for channel, membership in rows
        ]

    async def get_channel(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
    ) -> ChannelView:
        """Return one visible channel while masking private-channel existence."""
        actor = await self._workspace_access.require_membership(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            for_update=False,
        )
        channel, membership = await self._load_visible_channel(
            actor=actor,
            workspace_id=workspace_id,
            channel_id=channel_id,
            for_update=False,
        )
        return ChannelView(channel=channel, is_member=membership is not None)

    async def update_channel(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
        name: str | None,
        visibility: ChannelVisibility | None,
    ) -> ChannelView:
        """Rename a channel or change its visibility under durable authorization."""
        normalized_name = None if name is None else self._normalize_name(name)
        actor = await self._workspace_access.require_membership(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            for_update=True,
        )
        await self._require_manager(actor)
        channel = await self._locked_channel(workspace_id=workspace_id, channel_id=channel_id)
        if channel.is_archived:
            await self._repository.rollback()
            raise ChannelArchived
        membership = await self._repository.get_membership(
            channel_id=channel_id,
            user_id=actor_user_id,
        )
        next_name = channel.name if normalized_name is None else normalized_name
        next_visibility = channel.visibility if visibility is None else visibility
        if next_name == channel.name and next_visibility is channel.visibility:
            await self._repository.rollback()
            return ChannelView(channel=channel, is_member=membership is not None)
        now = self._clock()
        try:
            await self._repository.update_channel(
                channel_id=channel_id,
                name=next_name,
                visibility=next_visibility,
                updated_at=now,
            )
            await self._repository.commit()
        except ChannelConflict:
            await self._repository.rollback()
            raise ChannelNameConflict from None
        updated = replace(
            channel,
            name=next_name,
            visibility=next_visibility,
            updated_at=now,
        )
        return ChannelView(channel=updated, is_member=membership is not None)

    async def archive_channel(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
    ) -> ChannelView:
        """Soft-close a channel without deleting its memberships or future history."""
        actor = await self._workspace_access.require_membership(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            for_update=True,
        )
        await self._require_manager(actor)
        channel = await self._locked_channel(workspace_id=workspace_id, channel_id=channel_id)
        membership = await self._repository.get_membership(
            channel_id=channel_id,
            user_id=actor_user_id,
        )
        if channel.is_archived:
            await self._repository.rollback()
            return ChannelView(channel=channel, is_member=membership is not None)
        now = self._clock()
        await self._repository.set_channel_archived(
            channel_id=channel_id,
            archived_at=now,
            archived_by_user_id=actor_user_id,
            updated_at=now,
        )
        await self._repository.commit()
        archived = replace(
            channel,
            updated_at=now,
            archived_at=now,
            archived_by_user_id=actor_user_id,
        )
        return ChannelView(channel=archived, is_member=membership is not None)

    async def unarchive_channel(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
    ) -> ChannelView:
        """Reopen a soft-archived channel without changing its membership."""
        actor = await self._workspace_access.require_membership(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            for_update=True,
        )
        await self._require_manager(actor)
        channel = await self._locked_channel(workspace_id=workspace_id, channel_id=channel_id)
        membership = await self._repository.get_membership(
            channel_id=channel_id,
            user_id=actor_user_id,
        )
        if not channel.is_archived:
            await self._repository.rollback()
            return ChannelView(channel=channel, is_member=membership is not None)
        now = self._clock()
        await self._repository.set_channel_archived(
            channel_id=channel_id,
            archived_at=None,
            archived_by_user_id=None,
            updated_at=now,
        )
        await self._repository.commit()
        restored = replace(
            channel,
            updated_at=now,
            archived_at=None,
            archived_by_user_id=None,
        )
        return ChannelView(channel=restored, is_member=membership is not None)

    async def list_memberships(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
    ) -> list[ChannelMembership]:
        """List memberships for a channel visible to the actor."""
        actor = await self._workspace_access.require_membership(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            for_update=False,
        )
        await self._load_visible_channel(
            actor=actor,
            workspace_id=workspace_id,
            channel_id=channel_id,
            for_update=False,
        )
        return await self._repository.list_memberships(channel_id=channel_id)

    async def get_current_membership(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
    ) -> ChannelMembership:
        """Return the actor's current membership in one visible channel."""
        actor = await self._workspace_access.require_membership(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            for_update=False,
        )
        _channel, membership = await self._load_visible_channel(
            actor=actor,
            workspace_id=workspace_id,
            channel_id=channel_id,
            for_update=False,
        )
        if membership is None:
            raise ChannelMembershipNotFound
        return membership

    async def join_channel(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
    ) -> ChannelMembership:
        """Idempotently join a public channel or a manager-visible private channel."""
        actor = await self._workspace_access.require_membership(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            for_update=True,
        )
        channel = await self._locked_channel(workspace_id=workspace_id, channel_id=channel_id)
        membership = await self._repository.get_membership(
            channel_id=channel_id,
            user_id=actor_user_id,
            for_update=True,
        )
        if not self._can_view(channel, actor, membership):
            await self._repository.rollback()
            raise ChannelNotFound
        if membership is not None:
            await self._repository.rollback()
            return membership
        if channel.is_archived:
            await self._repository.rollback()
            raise ChannelArchived
        created = self._new_membership(
            channel=channel,
            user_id=actor_user_id,
            added_by_user_id=actor_user_id,
        )
        return await self._add_membership_idempotently(created)

    async def leave_channel(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
    ) -> None:
        """Remove the actor's explicit membership, including from archived channels."""
        actor = await self._workspace_access.require_membership(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            for_update=True,
        )
        channel = await self._locked_channel(workspace_id=workspace_id, channel_id=channel_id)
        membership = await self._repository.get_membership(
            channel_id=channel_id,
            user_id=actor_user_id,
            for_update=True,
        )
        if not self._can_view(channel, actor, membership):
            await self._repository.rollback()
            raise ChannelNotFound
        if membership is None:
            await self._repository.rollback()
            raise ChannelMembershipNotFound
        await self._repository.remove_membership(
            channel_id=channel_id,
            user_id=actor_user_id,
        )
        await self._repository.commit()

    async def add_membership(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
        target_user_id: UUID,
    ) -> ChannelMembership:
        """Idempotently add a current workspace member as an owner or administrator."""
        actor = await self._workspace_access.require_membership(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            for_update=True,
        )
        await self._require_manager(actor)
        channel = await self._locked_channel(workspace_id=workspace_id, channel_id=channel_id)
        if channel.is_archived:
            await self._repository.rollback()
            raise ChannelArchived
        target = await self._target_workspace_membership(
            actor=actor,
            workspace_id=workspace_id,
            target_user_id=target_user_id,
        )
        existing = await self._repository.get_membership(
            channel_id=channel_id,
            user_id=target.user_id,
            for_update=True,
        )
        if existing is not None:
            await self._repository.rollback()
            return existing
        created = self._new_membership(
            channel=channel,
            user_id=target_user_id,
            added_by_user_id=actor_user_id,
        )
        return await self._add_membership_idempotently(created)

    async def remove_membership(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
        target_user_id: UUID,
    ) -> None:
        """Remove a channel member according to the durable workspace hierarchy."""
        actor = await self._workspace_access.require_membership(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            for_update=True,
        )
        await self._require_manager(actor)
        await self._locked_channel(workspace_id=workspace_id, channel_id=channel_id)
        target = await self._target_workspace_membership(
            actor=actor,
            workspace_id=workspace_id,
            target_user_id=target_user_id,
        )
        if actor.role is WorkspaceRole.ADMIN and target.role is not WorkspaceRole.MEMBER:
            await self._repository.rollback()
            raise ChannelPermissionDenied
        membership = await self._repository.get_membership(
            channel_id=channel_id,
            user_id=target_user_id,
            for_update=True,
        )
        if membership is None:
            await self._repository.rollback()
            raise ChannelMembershipNotFound
        await self._repository.remove_membership(
            channel_id=channel_id,
            user_id=target_user_id,
        )
        await self._repository.commit()

    async def _load_visible_channel(
        self,
        *,
        actor: WorkspaceMembership,
        workspace_id: UUID,
        channel_id: UUID,
        for_update: bool,
    ) -> tuple[Channel, ChannelMembership | None]:
        channel = await self._repository.get_channel(
            workspace_id=workspace_id,
            channel_id=channel_id,
            for_update=for_update,
        )
        if channel is None:
            if for_update:
                await self._repository.rollback()
            raise ChannelNotFound
        membership = await self._repository.get_membership(
            channel_id=channel_id,
            user_id=actor.user_id,
            for_update=for_update,
        )
        if not self._can_view(channel, actor, membership):
            if for_update:
                await self._repository.rollback()
            raise ChannelNotFound
        return channel, membership

    async def _locked_channel(self, *, workspace_id: UUID, channel_id: UUID) -> Channel:
        channel = await self._repository.get_channel(
            workspace_id=workspace_id,
            channel_id=channel_id,
            for_update=True,
        )
        if channel is None:
            await self._repository.rollback()
            raise ChannelNotFound
        return channel

    async def _target_workspace_membership(
        self,
        *,
        actor: WorkspaceMembership,
        workspace_id: UUID,
        target_user_id: UUID,
    ) -> WorkspaceMembership:
        if actor.user_id == target_user_id:
            return actor
        target = await self._workspace_access.get_membership(
            workspace_id=workspace_id,
            user_id=target_user_id,
            for_update=True,
        )
        if target is None:
            await self._repository.rollback()
            raise TargetWorkspaceMembershipNotFound
        return target

    async def _add_membership_idempotently(
        self,
        membership: ChannelMembership,
    ) -> ChannelMembership:
        try:
            await self._repository.add_membership(membership)
            await self._repository.commit()
        except ChannelConflict:
            await self._repository.rollback()
            existing = await self._repository.get_membership(
                channel_id=membership.channel_id,
                user_id=membership.user_id,
            )
            if existing is None:
                raise
            return existing
        return membership

    def _new_membership(
        self,
        *,
        channel: Channel,
        user_id: UUID,
        added_by_user_id: UUID,
    ) -> ChannelMembership:
        return ChannelMembership(
            workspace_id=channel.workspace_id,
            channel_id=channel.id,
            user_id=user_id,
            added_by_user_id=added_by_user_id,
            joined_at=self._clock(),
        )

    async def _require_manager(self, membership: WorkspaceMembership) -> None:
        if not self._is_manager(membership):
            await self._repository.rollback()
            raise ChannelPermissionDenied

    @staticmethod
    def _is_manager(membership: WorkspaceMembership) -> bool:
        return membership.role in CHANNEL_MANAGER_ROLES

    def _can_view(
        self,
        channel: Channel,
        actor: WorkspaceMembership,
        channel_membership: ChannelMembership | None,
    ) -> bool:
        return (
            channel.visibility is ChannelVisibility.PUBLIC
            or channel_membership is not None
            or self._is_manager(actor)
        )

    def _normalize_name(self, name: str) -> str:
        normalized = name.strip().lower()
        if (
            not self._policy.minimum_name_length
            <= len(normalized)
            <= self._policy.maximum_name_length
            or CHANNEL_NAME_PATTERN.fullmatch(normalized) is None
        ):
            raise InvalidChannelName
        return normalized

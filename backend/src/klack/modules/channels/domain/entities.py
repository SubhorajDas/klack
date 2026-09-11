"""Persistence-agnostic channel and channel-membership values."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class ChannelVisibility(StrEnum):
    """Who may discover a channel inside its workspace."""

    PUBLIC = "public"
    PRIVATE = "private"


@dataclass(frozen=True, slots=True)
class Channel:
    """A named collaboration room contained by one workspace."""

    id: UUID
    workspace_id: UUID
    name: str
    visibility: ChannelVisibility
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    archived_by_user_id: UUID | None

    @property
    def is_archived(self) -> bool:
        """Return whether the channel is closed to membership changes."""
        return self.archived_at is not None


@dataclass(frozen=True, slots=True)
class ChannelMembership:
    """A workspace member's explicit participation in one channel."""

    workspace_id: UUID
    channel_id: UUID
    user_id: UUID
    added_by_user_id: UUID
    joined_at: datetime

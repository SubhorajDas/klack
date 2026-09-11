"""Public request and response contracts for channel endpoints."""

from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from klack.modules.channels.application.service import ChannelView
from klack.modules.channels.domain.entities import (
    ChannelMembership,
    ChannelVisibility,
)


class StrictRequest(BaseModel):
    """Reject unexpected fields on channel mutation requests."""

    model_config = ConfigDict(extra="forbid")


ChannelName = Annotated[str, Field(min_length=1, max_length=80)]


class CreateChannelRequest(StrictRequest):
    """Create a channel within one workspace."""

    name: ChannelName
    visibility: ChannelVisibility = ChannelVisibility.PUBLIC

    @field_validator("name", mode="before")
    @classmethod
    def trim_name_before_length_validation(cls, value: object) -> object:
        """Apply the public trim-then-bound contract before Pydantic checks length."""
        return value.strip() if isinstance(value, str) else value


class UpdateChannelRequest(StrictRequest):
    """Replace one or more mutable channel properties."""

    name: ChannelName | None = None
    visibility: ChannelVisibility | None = None

    @field_validator("name", mode="before")
    @classmethod
    def trim_name_before_length_validation(cls, value: object) -> object:
        """Apply the public trim-then-bound contract before Pydantic checks length."""
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def require_at_least_one_change(self) -> Self:
        """Reject no-op patch bodies before invoking the application service."""
        if self.name is None and self.visibility is None:
            raise ValueError("At least one channel field must be provided.")
        return self


class ChannelResponse(BaseModel):
    """Channel metadata together with the current identity's membership state."""

    id: UUID
    workspace_id: UUID
    name: str
    visibility: ChannelVisibility
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    archived_by_user_id: UUID | None
    is_member: bool

    @classmethod
    def from_view(cls, view: ChannelView) -> Self:
        channel = view.channel
        return cls(
            id=channel.id,
            workspace_id=channel.workspace_id,
            name=channel.name,
            visibility=channel.visibility,
            created_by_user_id=channel.created_by_user_id,
            created_at=channel.created_at,
            updated_at=channel.updated_at,
            archived_at=channel.archived_at,
            archived_by_user_id=channel.archived_by_user_id,
            is_member=view.is_member,
        )


class ChannelsResponse(BaseModel):
    """Channels visible to the current identity in one workspace."""

    channels: list[ChannelResponse]


class ChannelMembershipResponse(BaseModel):
    """One user's explicit membership in a channel."""

    workspace_id: UUID
    channel_id: UUID
    user_id: UUID
    added_by_user_id: UUID
    joined_at: datetime

    @classmethod
    def from_domain(cls, membership: ChannelMembership) -> Self:
        return cls(
            workspace_id=membership.workspace_id,
            channel_id=membership.channel_id,
            user_id=membership.user_id,
            added_by_user_id=membership.added_by_user_id,
            joined_at=membership.joined_at,
        )


class ChannelMembershipsResponse(BaseModel):
    """Explicit membership roster for one visible channel."""

    memberships: list[ChannelMembershipResponse]

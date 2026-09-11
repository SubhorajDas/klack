"""Versioned channel and channel-membership endpoints."""

from uuid import UUID

from fastapi import APIRouter, Response, status

from klack.modules.channels.api.dependencies import ChannelServiceDependency
from klack.modules.channels.api.schemas import (
    ChannelMembershipResponse,
    ChannelMembershipsResponse,
    ChannelResponse,
    ChannelsResponse,
    CreateChannelRequest,
    UpdateChannelRequest,
)
from klack.modules.identity.api.dependencies import (
    CurrentIdentityDependency,
    CurrentMutationIdentityDependency,
)

router = APIRouter(tags=["channels"])


@router.post(
    "/workspaces/{workspace_id}/channels",
    response_model=ChannelResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_channel(
    workspace_id: UUID,
    payload: CreateChannelRequest,
    response: Response,
    service: ChannelServiceDependency,
    identity: CurrentMutationIdentityDependency,
) -> ChannelResponse:
    """Create a channel and add its creator as the first explicit member."""
    view = await service.create_channel(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        name=payload.name,
        visibility=payload.visibility,
    )
    response.headers["Cache-Control"] = "no-store"
    return ChannelResponse.from_view(view)


@router.get(
    "/workspaces/{workspace_id}/channels",
    response_model=ChannelsResponse,
)
async def list_channels(
    workspace_id: UUID,
    response: Response,
    service: ChannelServiceDependency,
    identity: CurrentIdentityDependency,
    include_archived: bool = False,
) -> ChannelsResponse:
    """List channels visible to the current identity in one workspace."""
    views = await service.list_channels(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        include_archived=include_archived,
    )
    response.headers["Cache-Control"] = "no-store"
    return ChannelsResponse(channels=[ChannelResponse.from_view(view) for view in views])


@router.get(
    "/workspaces/{workspace_id}/channels/{channel_id}",
    response_model=ChannelResponse,
)
async def get_channel(
    workspace_id: UUID,
    channel_id: UUID,
    response: Response,
    service: ChannelServiceDependency,
    identity: CurrentIdentityDependency,
) -> ChannelResponse:
    """Return one channel visible to the current identity."""
    view = await service.get_channel(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        channel_id=channel_id,
    )
    response.headers["Cache-Control"] = "no-store"
    return ChannelResponse.from_view(view)


@router.patch(
    "/workspaces/{workspace_id}/channels/{channel_id}",
    response_model=ChannelResponse,
)
async def update_channel(
    workspace_id: UUID,
    channel_id: UUID,
    payload: UpdateChannelRequest,
    response: Response,
    service: ChannelServiceDependency,
    identity: CurrentMutationIdentityDependency,
) -> ChannelResponse:
    """Update one or more mutable channel properties."""
    view = await service.update_channel(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        name=payload.name,
        visibility=payload.visibility,
    )
    response.headers["Cache-Control"] = "no-store"
    return ChannelResponse.from_view(view)


@router.post(
    "/workspaces/{workspace_id}/channels/{channel_id}/archive",
    response_model=ChannelResponse,
)
async def archive_channel(
    workspace_id: UUID,
    channel_id: UUID,
    response: Response,
    service: ChannelServiceDependency,
    identity: CurrentMutationIdentityDependency,
) -> ChannelResponse:
    """Archive a channel without deleting its durable history."""
    view = await service.archive_channel(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        channel_id=channel_id,
    )
    response.headers["Cache-Control"] = "no-store"
    return ChannelResponse.from_view(view)


@router.post(
    "/workspaces/{workspace_id}/channels/{channel_id}/unarchive",
    response_model=ChannelResponse,
)
async def unarchive_channel(
    workspace_id: UUID,
    channel_id: UUID,
    response: Response,
    service: ChannelServiceDependency,
    identity: CurrentMutationIdentityDependency,
) -> ChannelResponse:
    """Restore an archived channel."""
    view = await service.unarchive_channel(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        channel_id=channel_id,
    )
    response.headers["Cache-Control"] = "no-store"
    return ChannelResponse.from_view(view)


@router.get(
    "/workspaces/{workspace_id}/channels/{channel_id}/memberships",
    response_model=ChannelMembershipsResponse,
)
async def list_memberships(
    workspace_id: UUID,
    channel_id: UUID,
    response: Response,
    service: ChannelServiceDependency,
    identity: CurrentIdentityDependency,
) -> ChannelMembershipsResponse:
    """List explicit members of one visible channel."""
    memberships = await service.list_memberships(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        channel_id=channel_id,
    )
    response.headers["Cache-Control"] = "no-store"
    return ChannelMembershipsResponse(
        memberships=[ChannelMembershipResponse.from_domain(item) for item in memberships],
    )


@router.get(
    "/workspaces/{workspace_id}/channels/{channel_id}/memberships/me",
    response_model=ChannelMembershipResponse,
)
async def current_membership(
    workspace_id: UUID,
    channel_id: UUID,
    response: Response,
    service: ChannelServiceDependency,
    identity: CurrentIdentityDependency,
) -> ChannelMembershipResponse:
    """Return the current identity's explicit channel membership."""
    membership = await service.get_current_membership(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        channel_id=channel_id,
    )
    response.headers["Cache-Control"] = "no-store"
    return ChannelMembershipResponse.from_domain(membership)


@router.put(
    "/workspaces/{workspace_id}/channels/{channel_id}/memberships/me",
    response_model=ChannelMembershipResponse,
)
async def join_channel(
    workspace_id: UUID,
    channel_id: UUID,
    response: Response,
    service: ChannelServiceDependency,
    identity: CurrentMutationIdentityDependency,
) -> ChannelMembershipResponse:
    """Join a public channel as the current identity."""
    membership = await service.join_channel(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        channel_id=channel_id,
    )
    response.headers["Cache-Control"] = "no-store"
    return ChannelMembershipResponse.from_domain(membership)


@router.delete(
    "/workspaces/{workspace_id}/channels/{channel_id}/memberships/me",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def leave_channel(
    workspace_id: UUID,
    channel_id: UUID,
    service: ChannelServiceDependency,
    identity: CurrentMutationIdentityDependency,
) -> Response:
    """Remove the current identity's explicit channel membership."""
    await service.leave_channel(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        channel_id=channel_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})


@router.put(
    "/workspaces/{workspace_id}/channels/{channel_id}/memberships/{user_id}",
    response_model=ChannelMembershipResponse,
)
async def add_membership(
    workspace_id: UUID,
    channel_id: UUID,
    user_id: UUID,
    response: Response,
    service: ChannelServiceDependency,
    identity: CurrentMutationIdentityDependency,
) -> ChannelMembershipResponse:
    """Add one workspace member to a channel."""
    membership = await service.add_membership(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        target_user_id=user_id,
    )
    response.headers["Cache-Control"] = "no-store"
    return ChannelMembershipResponse.from_domain(membership)


@router.delete(
    "/workspaces/{workspace_id}/channels/{channel_id}/memberships/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_membership(
    workspace_id: UUID,
    channel_id: UUID,
    user_id: UUID,
    service: ChannelServiceDependency,
    identity: CurrentMutationIdentityDependency,
) -> Response:
    """Remove one user's explicit channel membership."""
    await service.remove_membership(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        target_user_id=user_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})

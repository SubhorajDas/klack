"""Versioned channel-message endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from klack.modules.identity.api.dependencies import (
    CurrentIdentityDependency,
    CurrentMutationIdentityDependency,
)
from klack.modules.messaging.api.dependencies import MessageServiceDependency
from klack.modules.messaging.api.schemas import (
    CreateMessageRequest,
    MessageResponse,
    MessagesResponse,
    UpdateMessageRequest,
)

router = APIRouter(tags=["messages"])


@router.post(
    "/workspaces/{workspace_id}/channels/{channel_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_message(
    workspace_id: UUID,
    channel_id: UUID,
    payload: CreateMessageRequest,
    response: Response,
    service: MessageServiceDependency,
    identity: CurrentMutationIdentityDependency,
) -> MessageResponse:
    """Create a message as an explicit member of an active channel."""
    message = await service.create_message(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        body=payload.body,
    )
    response.headers["Cache-Control"] = "no-store"
    return MessageResponse.from_domain(message)


@router.get(
    "/workspaces/{workspace_id}/channels/{channel_id}/messages",
    response_model=MessagesResponse,
)
async def list_messages(
    workspace_id: UUID,
    channel_id: UUID,
    response: Response,
    service: MessageServiceDependency,
    identity: CurrentIdentityDependency,
    before: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> MessagesResponse:
    """List one reverse-chronological page of channel history."""
    page = await service.list_messages(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        before=before,
        limit=limit,
    )
    response.headers["Cache-Control"] = "no-store"
    return MessagesResponse.from_page(page)


@router.patch(
    "/workspaces/{workspace_id}/channels/{channel_id}/messages/{message_id}",
    response_model=MessageResponse,
)
async def edit_message(
    workspace_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    payload: UpdateMessageRequest,
    response: Response,
    service: MessageServiceDependency,
    identity: CurrentMutationIdentityDependency,
) -> MessageResponse:
    """Replace the body of a live message owned by the current identity."""
    message = await service.edit_message(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        message_id=message_id,
        body=payload.body,
    )
    response.headers["Cache-Control"] = "no-store"
    return MessageResponse.from_domain(message)


@router.delete(
    "/workspaces/{workspace_id}/channels/{channel_id}/messages/{message_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_message(
    workspace_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    service: MessageServiceDependency,
    identity: CurrentMutationIdentityDependency,
) -> Response:
    """Soft-delete a message owned by the current identity."""
    await service.delete_message(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        message_id=message_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})

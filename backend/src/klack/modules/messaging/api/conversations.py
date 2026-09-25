"""Direct conversation, reaction, and read cursor HTTP contracts."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from klack.api.dependencies import get_session
from klack.modules.channels.api.schemas import ChannelResponse, ChannelsResponse
from klack.modules.channels.application.service import ChannelContentAccessService
from klack.modules.channels.infrastructure.repository import SqlAlchemyChannelRepository
from klack.modules.identity.api.dependencies import (
    CurrentIdentityDependency,
    CurrentMutationIdentityDependency,
)
from klack.modules.messaging.api.schemas import MessageResponse, StrictRequest
from klack.modules.messaging.application.conversations import ConversationService
from klack.modules.messaging.application.ports import NullMessageEventWriter
from klack.modules.messaging.infrastructure.conversations import SqlAlchemyConversationRepository
from klack.modules.messaging.infrastructure.repository import SqlAlchemyMessageRepository
from klack.modules.realtime.infrastructure.repository import SqlAlchemyRealtimeEventRepository
from klack.modules.workspaces.application.service import WorkspaceAccessService
from klack.modules.workspaces.infrastructure.repository import SqlAlchemyWorkspaceRepository

router = APIRouter(tags=["conversations"])


async def service(
    request: Request, response: Response, session: Annotated[AsyncSession, Depends(get_session)]
) -> ConversationService:
    response.headers["Cache-Control"] = "no-store"
    workspaces = WorkspaceAccessService(SqlAlchemyWorkspaceRepository(session))
    return ConversationService(
        SqlAlchemyConversationRepository(session),
        SqlAlchemyMessageRepository(session),
        ChannelContentAccessService(
            repository=SqlAlchemyChannelRepository(session), workspace_access=workspaces
        ),
        workspaces,
        SqlAlchemyRealtimeEventRepository(session)
        if request.app.state.container.settings.realtime_enabled
        else NullMessageEventWriter(),
    )


Service = Annotated[ConversationService, Depends(service)]


class DirectRequest(StrictRequest):
    user_id: UUID


class ReadRequest(StrictRequest):
    message_id: UUID


class ReadResponse(BaseModel):
    message_id: UUID | None
    unread_count: int


@router.post("/workspaces/{workspace_id}/direct-messages", response_model=ChannelResponse)
async def open_direct(
    workspace_id: UUID,
    payload: DirectRequest,
    service: Service,
    identity: CurrentMutationIdentityDependency,
) -> ChannelResponse:
    return ChannelResponse.from_view(
        await service.open_direct(workspace_id, identity.user.id, payload.user_id)
    )


@router.get("/workspaces/{workspace_id}/direct-messages", response_model=ChannelsResponse)
async def list_direct(
    workspace_id: UUID, service: Service, identity: CurrentIdentityDependency
) -> ChannelsResponse:
    return ChannelsResponse(
        channels=[
            ChannelResponse.from_view(c)
            for c in await service.list_direct(workspace_id, identity.user.id)
        ]
    )


@router.put(
    "/workspaces/{workspace_id}/channels/{channel_id}/messages/{message_id}/reactions/{emoji}",
    response_model=MessageResponse,
)
async def add_reaction(
    workspace_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    emoji: str,
    service: Service,
    identity: CurrentMutationIdentityDependency,
) -> MessageResponse:
    return MessageResponse.from_domain(
        await service.react(workspace_id, channel_id, identity.user.id, message_id, emoji, True)
    )


@router.delete(
    "/workspaces/{workspace_id}/channels/{channel_id}/messages/{message_id}/reactions/{emoji}",
    response_model=MessageResponse,
)
async def remove_reaction(
    workspace_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    emoji: str,
    service: Service,
    identity: CurrentMutationIdentityDependency,
) -> MessageResponse:
    return MessageResponse.from_domain(
        await service.react(workspace_id, channel_id, identity.user.id, message_id, emoji, False)
    )


@router.get(
    "/workspaces/{workspace_id}/channels/{channel_id}/read-cursor", response_model=ReadResponse
)
async def get_read(
    workspace_id: UUID, channel_id: UUID, service: Service, identity: CurrentIdentityDependency
) -> ReadResponse:
    message_id, count = await service.read_state(workspace_id, channel_id, identity.user.id)
    return ReadResponse(message_id=message_id, unread_count=count)


@router.put(
    "/workspaces/{workspace_id}/channels/{channel_id}/read-cursor", response_model=ReadResponse
)
async def set_read(
    workspace_id: UUID,
    channel_id: UUID,
    payload: ReadRequest,
    service: Service,
    identity: CurrentMutationIdentityDependency,
) -> ReadResponse:
    message_id, count = await service.read_state(
        workspace_id, channel_id, identity.user.id, payload.message_id
    )
    return ReadResponse(message_id=message_id, unread_count=count)

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
    EmailVerificationAccessDependency,
)
from klack.modules.messaging.api.schemas import MessageResponse, StrictRequest
from klack.modules.messaging.application.conversations import ConversationService
from klack.modules.messaging.application.ports import NullMessageEventWriter
from klack.modules.messaging.infrastructure.conversations import SqlAlchemyConversationRepository
from klack.modules.messaging.infrastructure.repository import SqlAlchemyMessageRepository
from klack.modules.realtime.infrastructure.repository import SqlAlchemyRealtimeEventRepository
from klack.modules.workspaces.api.schemas import MembershipResponse, MembershipsResponse
from klack.modules.workspaces.application.service import WorkspaceAccessService
from klack.modules.workspaces.infrastructure.repository import SqlAlchemyWorkspaceRepository

router = APIRouter(tags=["conversations"])


async def service(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    email_verification: EmailVerificationAccessDependency,
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
        email_verification=email_verification,
    )


Service = Annotated[ConversationService, Depends(service)]


class DirectRequest(StrictRequest):
    user_id: UUID


class ReadRequest(StrictRequest):
    message_id: UUID


class ReadResponse(BaseModel):
    message_id: UUID | None
    unread_count: int


class AlertResponse(BaseModel):
    channel: ChannelResponse
    message: MessageResponse
    unread_count: int


class AlertsResponse(BaseModel):
    alerts: list[AlertResponse]


@router.get("/direct-messages", response_model=ChannelsResponse)
async def global_direct(service: Service, identity: CurrentIdentityDependency) -> ChannelsResponse:
    return ChannelsResponse(
        channels=[
            ChannelResponse.from_view(c) for c in await service.global_direct(identity.user.id)
        ]
    )


@router.post("/direct-messages", response_model=ChannelResponse)
async def open_global_direct(
    payload: DirectRequest, service: Service, identity: CurrentMutationIdentityDependency
) -> ChannelResponse:
    return ChannelResponse.from_view(
        await service.open_global_direct(identity.user.id, payload.user_id)
    )


@router.get("/direct-messages/{channel_id}", response_model=ChannelResponse)
async def resolve_global_direct(
    channel_id: UUID, service: Service, identity: CurrentIdentityDependency
) -> ChannelResponse:
    return ChannelResponse.from_view(await service.resolve_direct(channel_id, identity.user.id))


@router.get("/contacts", response_model=MembershipsResponse)
async def global_contacts(
    service: Service, identity: CurrentIdentityDependency
) -> MembershipsResponse:
    return MembershipsResponse(
        memberships=[
            MembershipResponse.from_domain(m) for m in await service.contacts(identity.user.id)
        ]
    )


@router.get("/alerts", response_model=AlertsResponse)
async def global_alerts(service: Service, identity: CurrentIdentityDependency) -> AlertsResponse:
    return AlertsResponse(
        alerts=[
            AlertResponse(
                channel=ChannelResponse.from_view(c),
                message=MessageResponse.from_domain(m),
                unread_count=n,
            )
            for c, m, n in await service.global_alerts(identity.user.id)
        ]
    )


class UnreadCountsResponse(BaseModel):
    total: int
    direct_messages: int
    workspaces: dict[str, int]
    channels: dict[str, int]


@router.get("/unread-counts", response_model=UnreadCountsResponse)
async def unread_counts(
    service: Service, identity: CurrentIdentityDependency
) -> UnreadCountsResponse:
    items = await service.unread_counts(identity.user.id)
    workspaces: dict[str, int] = {}
    channels: dict[str, int] = {}
    direct = 0
    for channel_id, workspace_id, is_direct, n in items:
        channels[str(channel_id)] = n
        if is_direct:
            direct += n
        else:
            key = str(workspace_id)
            workspaces[key] = workspaces.get(key, 0) + n
    return UnreadCountsResponse(
        total=sum(channels.values()),
        direct_messages=direct,
        workspaces=workspaces,
        channels=channels,
    )


@router.get("/workspaces/{workspace_id}/alerts", response_model=AlertsResponse)
async def list_alerts(
    workspace_id: UUID, service: Service, identity: CurrentIdentityDependency
) -> AlertsResponse:
    return AlertsResponse(
        alerts=[
            AlertResponse(
                channel=ChannelResponse.from_view(channel),
                message=MessageResponse.from_domain(message),
                unread_count=count,
            )
            for channel, message, count in await service.alerts(workspace_id, identity.user.id)
        ]
    )


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

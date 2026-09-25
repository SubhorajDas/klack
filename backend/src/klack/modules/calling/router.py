"""Authenticated call control, inbox, and conversation history."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from klack.api.dependencies import get_session
from klack.modules.calling.models import CallRecord
from klack.modules.calling.service import CallService, configured
from klack.modules.channels.infrastructure.models import ChannelMembershipRecord
from klack.modules.identity.api.dependencies import (
    CurrentIdentityDependency,
    CurrentMutationIdentityDependency,
)
from klack.modules.identity.infrastructure.models import UserRecord
from klack.modules.workspaces.infrastructure.models import MembershipRecord

router = APIRouter(tags=["calls"])
Session = Annotated[AsyncSession, Depends(get_session)]


class DeviceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: UUID


class StartRequest(DeviceRequest):
    request_id: UUID
    workspace_id: UUID
    channel_id: UUID


class CallResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    channel_id: UUID
    caller_id: UUID
    callee_id: UUID
    peer_name: str
    status: str
    created_at: datetime
    answered_at: datetime | None
    ended_at: datetime | None
    owned: bool


async def view(session: AsyncSession, call: CallRecord, actor: UUID, device: UUID) -> CallResponse:
    peer = await session.get(
        UserRecord, call.callee_id if actor == call.caller_id else call.caller_id
    )
    return CallResponse(
        id=call.id,
        workspace_id=call.workspace_id,
        channel_id=call.channel_id,
        caller_id=call.caller_id,
        callee_id=call.callee_id,
        peer_name=peer.email.split("@")[0] if peer else "Member",
        status=call.status,
        created_at=call.created_at,
        answered_at=call.answered_at,
        ended_at=call.ended_at,
        owned=(call.caller_device if actor == call.caller_id else call.callee_device) == device,
    )


def service(request: Request, response: Response, session: Session) -> CallService:
    response.headers["Cache-Control"] = "no-store"
    return CallService(session, request.app.state.container.settings)


Service = Annotated[CallService, Depends(service)]


class InboxResponse(BaseModel):
    enabled: bool
    calls: list[CallResponse]


@router.get("/calls", response_model=InboxResponse)
async def inbox(
    service: Service, identity: CurrentIdentityDependency, device_id: UUID
) -> InboxResponse:
    enabled = configured(service.settings)
    rows = await service.inbox(identity.user.id) if enabled else []
    return InboxResponse(
        enabled=enabled,
        calls=[await view(service.session, row, identity.user.id, device_id) for row in rows],
    )


@router.post("/calls", response_model=CallResponse, status_code=201)
async def start(
    payload: StartRequest, service: Service, identity: CurrentMutationIdentityDependency
) -> CallResponse:
    row = await service.start(
        payload.workspace_id,
        payload.channel_id,
        identity.user.id,
        identity.session.id,
        payload.device_id,
        payload.request_id,
    )
    return await view(service.session, row, identity.user.id, payload.device_id)


@router.post("/calls/{call_id}/{action}", response_model=CallResponse)
async def control(
    call_id: UUID,
    action: Literal["accept", "decline", "end", "heartbeat"],
    payload: DeviceRequest,
    service: Service,
    identity: CurrentMutationIdentityDependency,
) -> CallResponse:
    row = await service.action(
        call_id, identity.user.id, identity.session.id, payload.device_id, action
    )
    return await view(service.session, row, identity.user.id, payload.device_id)


class TokenResponse(BaseModel):
    url: str
    token: str


# Separate path shape avoids the action enum route swallowing token requests.
@router.post("/calls/{call_id}/connection/token", response_model=TokenResponse)
async def token(
    call_id: UUID,
    payload: DeviceRequest,
    service: Service,
    identity: CurrentMutationIdentityDependency,
) -> TokenResponse:
    value = await service.token(call_id, identity.user.id, identity.session.id, payload.device_id)
    return TokenResponse(url=service.settings.livekit_url, token=value)


class HistoryResponse(BaseModel):
    calls: list[CallResponse]
    next_before: UUID | None


@router.get(
    "/workspaces/{workspace_id}/channels/{channel_id}/calls", response_model=HistoryResponse
)
async def history(
    workspace_id: UUID,
    channel_id: UUID,
    device_id: UUID,
    service: Service,
    identity: CurrentIdentityDependency,
    before: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> HistoryResponse:
    session = service.session
    actor = identity.user.id
    if not await session.get(MembershipRecord, (workspace_id, actor)):
        raise HTTPException(404, "Conversation not found.")
    member = await session.get(ChannelMembershipRecord, (channel_id, actor))
    if not member or member.workspace_id != workspace_id:
        raise HTTPException(404, "Conversation not found.")
    query = select(CallRecord).where(
        CallRecord.workspace_id == workspace_id,
        CallRecord.channel_id == channel_id,
        or_(CallRecord.caller_id == actor, CallRecord.callee_id == actor),
    )
    if before:
        cursor = await session.get(CallRecord, before)
        if (
            not cursor
            or cursor.channel_id != channel_id
            or actor not in (cursor.caller_id, cursor.callee_id)
        ):
            raise HTTPException(404, "Call not found.")
        query = query.where(
            or_(
                CallRecord.created_at < cursor.created_at,
                and_(CallRecord.created_at == cursor.created_at, CallRecord.id < cursor.id),
            )
        )
    rows = list(
        await session.scalars(
            query.order_by(CallRecord.created_at.desc(), CallRecord.id.desc()).limit(limit + 1)
        )
    )
    return HistoryResponse(
        calls=[await view(session, row, actor, device_id) for row in rows[:limit]],
        next_before=rows[limit - 1].id if len(rows) > limit else None,
    )

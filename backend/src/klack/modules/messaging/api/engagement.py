"""Authorized pins, private bookmarks, mention candidates, and alert preferences."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from klack.api.dependencies import get_session
from klack.modules.channels.api.schemas import ChannelResponse
from klack.modules.channels.application.service import ChannelView
from klack.modules.channels.domain.errors import ChannelArchived
from klack.modules.channels.infrastructure.models import ChannelMembershipRecord, ChannelRecord
from klack.modules.channels.infrastructure.repository import SqlAlchemyChannelRepository
from klack.modules.identity.api.dependencies import (
    CurrentIdentityDependency,
    CurrentMutationIdentityDependency,
)
from klack.modules.identity.infrastructure.models import UserRecord
from klack.modules.messaging.api.conversations import Service
from klack.modules.messaging.api.schemas import MessageResponse, StrictRequest
from klack.modules.messaging.application.conversations import ConversationService
from klack.modules.messaging.domain.errors import MessageDeleted, MessageNotFound
from klack.modules.messaging.infrastructure.models import (
    MessageRecord,
    NotificationPauseRecord,
    NotificationPreferenceRecord,
    PinRecord,
    SavedMessageRecord,
)
from klack.modules.messaging.infrastructure.repository import SqlAlchemyMessageRepository
from klack.modules.workspaces.infrastructure.models import MembershipRecord

router = APIRouter(tags=["message organization"])
Session = Annotated[AsyncSession, Depends(get_session)]
PATH = "/workspaces/{workspace_id}/channels/{channel_id}"


class MarkResponse(BaseModel):
    pinned: list[UUID]
    saved: list[UUID]


class CollectedMessage(BaseModel):
    channel: ChannelResponse
    message: MessageResponse


class CollectionResponse(BaseModel):
    items: list[CollectedMessage]
    next_offset: int | None


def accessible(actor: UUID) -> ColumnElement[bool]:
    return (
        select(ChannelMembershipRecord.channel_id)
        .where(
            ChannelMembershipRecord.user_id == actor,
            ChannelMembershipRecord.channel_id == ChannelRecord.id,
            or_(
                ChannelRecord.direct_key.is_not(None),
                select(MembershipRecord.user_id)
                .where(
                    MembershipRecord.workspace_id == ChannelRecord.workspace_id,
                    MembershipRecord.user_id == actor,
                )
                .exists(),
            ),
        )
        .correlate(ChannelRecord)
        .exists()
    )


async def collection(
    session: AsyncSession, actor: UUID, channel_id: UUID | None, offset: int, limit: int
) -> CollectionResponse:
    mark = PinRecord if channel_id else SavedMessageRecord
    query = (
        select(ChannelRecord, MessageRecord)
        .join(MessageRecord, MessageRecord.channel_id == ChannelRecord.id)
        .join(mark, mark.message_id == MessageRecord.id)
        .where(MessageRecord.deleted_at.is_(None), accessible(actor))
        .order_by(mark.created_at.desc(), MessageRecord.id.desc())
        .offset(offset)
        .limit(limit + 1)
    )
    query = (
        query.where(ChannelRecord.id == channel_id)
        if channel_id
        else query.where(SavedMessageRecord.user_id == actor)
    )
    rows = (await session.execute(query)).all()
    messages = await SqlAlchemyMessageRepository(session)._messages([r[1] for r in rows[:limit]])
    return CollectionResponse(
        items=[
            CollectedMessage(
                channel=ChannelResponse.from_view(
                    ChannelView(
                        channel=SqlAlchemyChannelRepository._channel(row[0]), is_member=True
                    )
                ),
                message=MessageResponse.from_domain(message),
            )
            for row, message in zip(rows[:limit], messages, strict=True)
        ],
        next_offset=offset + limit if len(rows) > limit else None,
    )


@router.get("/saved-messages", response_model=CollectionResponse)
async def saved_messages(
    session: Session,
    identity: CurrentIdentityDependency,
    response: Response,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CollectionResponse:
    response.headers["Cache-Control"] = "no-store"
    return await collection(session, identity.user.id, None, offset, limit)


@router.get(PATH + "/pins", response_model=CollectionResponse)
async def pins(
    workspace_id: UUID,
    channel_id: UUID,
    service: Service,
    session: Session,
    identity: CurrentIdentityDependency,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CollectionResponse:
    await service.access.require_access(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        for_update=False,
    )
    return await collection(session, identity.user.id, channel_id, offset, limit)


@router.get(PATH + "/message-marks", response_model=MarkResponse)
async def marks(
    workspace_id: UUID,
    channel_id: UUID,
    service: Service,
    session: Session,
    identity: CurrentIdentityDependency,
) -> MarkResponse:
    await service.access.require_access(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        for_update=False,
    )
    live = select(MessageRecord.id).where(
        MessageRecord.channel_id == channel_id, MessageRecord.deleted_at.is_(None)
    )
    return MarkResponse(
        pinned=list(
            await session.scalars(
                select(PinRecord.message_id).where(PinRecord.message_id.in_(live))
            )
        ),
        saved=list(
            await session.scalars(
                select(SavedMessageRecord.message_id).where(
                    SavedMessageRecord.user_id == identity.user.id,
                    SavedMessageRecord.message_id.in_(live),
                )
            )
        ),
    )


@router.put(PATH + "/messages/{message_id}/{kind}", status_code=204)
async def add_mark(
    workspace_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    kind: Literal["pin", "save"],
    service: Service,
    session: Session,
    identity: CurrentMutationIdentityDependency,
) -> Response:
    return await change_mark(
        workspace_id, channel_id, message_id, kind, True, service, session, identity.user.id
    )


@router.delete(PATH + "/messages/{message_id}/{kind}", status_code=204)
async def remove_mark(
    workspace_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    kind: Literal["pin", "save"],
    service: Service,
    session: Session,
    identity: CurrentMutationIdentityDependency,
) -> Response:
    return await change_mark(
        workspace_id, channel_id, message_id, kind, False, service, session, identity.user.id
    )


async def change_mark(
    workspace_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    kind: str,
    add: bool,
    service: ConversationService,
    session: AsyncSession,
    actor: UUID,
) -> Response:
    channel = await service.access.require_access(
        actor_user_id=actor,
        workspace_id=workspace_id,
        channel_id=channel_id,
        for_update=True,
    )
    if kind == "pin" and channel.is_archived:
        raise ChannelArchived
    message = await service.messages.get_message(
        workspace_id=workspace_id, channel_id=channel_id, message_id=message_id, for_update=True
    )
    if message is None:
        raise MessageNotFound
    if message.is_deleted and add:
        raise MessageDeleted
    if kind == "pin":
        row = await session.get(PinRecord, message_id)
        if row is None and add:
            session.add(
                PinRecord(message_id=message_id, user_id=actor, created_at=datetime.now(UTC))
            )
    else:
        saved_row = await session.get(SavedMessageRecord, (actor, message_id))
        if saved_row is None and add:
            session.add(
                SavedMessageRecord(
                    message_id=message_id, user_id=actor, created_at=datetime.now(UTC)
                )
            )
    if not add:
        if kind == "pin" and row is not None:
            await session.delete(row)
        elif kind != "pin" and saved_row is not None:
            await session.delete(saved_row)
    await session.commit()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


class MentionCandidate(BaseModel):
    user_id: UUID
    label: str
    email: str


@router.get(PATH + "/mention-candidates", response_model=list[MentionCandidate])
async def mention_candidates(
    workspace_id: UUID,
    channel_id: UUID,
    service: Service,
    session: Session,
    identity: CurrentIdentityDependency,
) -> list[MentionCandidate]:
    channel = await service.access.require_access(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        for_update=False,
    )
    rows = (
        await session.execute(
            select(UserRecord.id, UserRecord.email)
            .join(ChannelMembershipRecord, ChannelMembershipRecord.user_id == UserRecord.id)
            .where(
                ChannelMembershipRecord.channel_id == channel_id,
                UserRecord.disabled_at.is_(None),
                *(
                    []
                    if channel.direct_key
                    else [
                        UserRecord.id.in_(
                            select(MembershipRecord.user_id).where(
                                MembershipRecord.workspace_id == workspace_id
                            )
                        )
                    ]
                ),
            )
            .order_by(UserRecord.email)
        )
    ).all()
    return [
        MentionCandidate(user_id=user, label=email.split("@", 1)[0][:100], email=email)
        for user, email in rows
    ]


class Preference(StrictRequest):
    mode: Literal["all", "mentions", "muted"] = "all"


@router.get(PATH + "/notifications", response_model=Preference)
async def notification_preference(
    workspace_id: UUID,
    channel_id: UUID,
    service: Service,
    session: Session,
    identity: CurrentIdentityDependency,
) -> Preference:
    await service.access.require_access(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        for_update=False,
    )
    row = await session.get(NotificationPreferenceRecord, (channel_id, identity.user.id))
    return Preference(mode=cast(Literal["all", "mentions", "muted"], row.mode) if row else "all")


@router.put(PATH + "/notifications", response_model=Preference)
async def set_notification_preference(
    workspace_id: UUID,
    channel_id: UUID,
    payload: Preference,
    service: Service,
    session: Session,
    identity: CurrentMutationIdentityDependency,
) -> Preference:
    await service.access.require_access(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        for_update=True,
    )
    row = await session.get(NotificationPreferenceRecord, (channel_id, identity.user.id))
    if row:
        row.mode = payload.mode
    else:
        session.add(
            NotificationPreferenceRecord(
                channel_id=channel_id, user_id=identity.user.id, mode=payload.mode
            )
        )
    await session.commit()
    return payload


class PauseResponse(BaseModel):
    paused_until: datetime | None


class PauseRequest(StrictRequest):
    minutes: Literal[0, 30, 60, 120, 480, 1440]


@router.get("/notification-pause", response_model=PauseResponse)
async def notification_pause(
    session: Session,
    identity: CurrentIdentityDependency,
    response: Response,
) -> PauseResponse:
    response.headers["Cache-Control"] = "no-store"
    row = await session.get(NotificationPauseRecord, identity.user.id)
    return PauseResponse(paused_until=row.paused_until if row else None)


@router.put("/notification-pause", response_model=PauseResponse)
async def set_notification_pause(
    payload: PauseRequest,
    session: Session,
    identity: CurrentMutationIdentityDependency,
    response: Response,
) -> PauseResponse:
    response.headers["Cache-Control"] = "no-store"
    await session.scalar(
        select(UserRecord).where(UserRecord.id == identity.user.id).with_for_update()
    )
    row = await session.get(NotificationPauseRecord, identity.user.id)
    until = datetime.now(UTC) + timedelta(minutes=payload.minutes) if payload.minutes else None
    if row:
        row.paused_until = until
    else:
        session.add(NotificationPauseRecord(user_id=identity.user.id, paused_until=until))
    await session.commit()
    return PauseResponse(paused_until=until)

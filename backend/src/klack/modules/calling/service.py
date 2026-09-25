"""Call state transitions serialized against membership and participant changes."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from livekit import api as livekit
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from klack.core.config import Settings
from klack.modules.calling.models import CallRecord, CallSeatRecord
from klack.modules.channels.infrastructure.models import ChannelMembershipRecord, ChannelRecord
from klack.modules.identity.infrastructure.models import AuthSessionRecord, UserRecord
from klack.modules.workspaces.infrastructure.models import MembershipRecord, WorkspaceRecord

LIVE = ("ringing", "active")
RING_SECONDS = 45
LEASE_SECONDS = 60


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def configured(settings: Settings) -> bool:
    return bool(
        settings.livekit_url
        and settings.livekit_api_key.get_secret_value()
        and settings.livekit_api_secret.get_secret_value()
    )


def room_name(call_id: UUID) -> str:
    return f"klack-call-{call_id}"


async def has_access(session: AsyncSession, call: CallRecord, actor: UUID) -> bool:
    if actor not in (call.caller_id, call.callee_id):
        return False
    return bool(
        await session.get(MembershipRecord, (call.workspace_id, actor))
        and await session.get(ChannelMembershipRecord, (call.channel_id, actor))
    )


async def finish(session: AsyncSession, call: CallRecord, status: str, now: datetime) -> None:
    call.status = status
    call.ended_at = now
    call.cleanup_after = now if call.answered_at else None
    await session.execute(delete(CallSeatRecord).where(CallSeatRecord.call_id == call.id))


async def expire(session: AsyncSession, call: CallRecord, now: datetime) -> None:
    if call.status not in LIVE:
        return
    if call.status == "ringing" and utc(call.created_at) + timedelta(seconds=RING_SECONDS) <= now:
        await finish(session, call, "missed", now)
        return
    sessions = [(call.caller_id, call.caller_session)]
    if call.callee_session:
        sessions.append((call.callee_id, call.callee_session))
    authorized = all(
        [await has_access(session, call, actor) for actor in (call.caller_id, call.callee_id)]
    )
    for actor, session_id in sessions:
        auth = await session.get(AuthSessionRecord, session_id)
        user = await session.get(UserRecord, actor)
        if (
            not auth
            or auth.revoked_at
            or utc(auth.expires_at) <= now
            or not user
            or user.disabled_at
        ):
            authorized = False
    if not authorized:
        await finish(session, call, "cancelled" if call.status == "ringing" else "ended", now)
    elif call.status == "active" and (
        utc(call.caller_seen) + timedelta(seconds=LEASE_SECONDS) <= now
        or not call.callee_seen
        or utc(call.callee_seen) + timedelta(seconds=LEASE_SECONDS) <= now
    ):
        await finish(session, call, "ended", now)


class CallService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def start(
        self,
        workspace: UUID,
        channel_id: UUID,
        actor: UUID,
        auth_session: UUID,
        device: UUID,
        request_id: UUID,
    ) -> CallRecord:
        if not configured(self.settings):
            raise HTTPException(503, "Voice calls are not configured yet.")
        # Same lock order as workspace/channel membership mutations.
        await self.session.scalar(
            select(WorkspaceRecord).where(WorkspaceRecord.id == workspace).with_for_update()
        )
        channel = await self.session.get(ChannelRecord, channel_id)
        if (
            not channel
            or channel.workspace_id != workspace
            or not channel.direct_key
            or not await self.session.get(MembershipRecord, (workspace, actor))
            or not await self.session.get(ChannelMembershipRecord, (channel_id, actor))
        ):
            raise HTTPException(404, "Direct conversation not found.")
        if channel.archived_at:
            raise HTTPException(409, "This conversation is archived.")
        peers = [UUID(hex=value) for value in channel.direct_key.split(":")]
        if actor not in peers:
            raise HTTPException(404, "Direct conversation not found.")
        peer = next(value for value in peers if value != actor)
        if not await self.session.get(
            MembershipRecord, (workspace, peer)
        ) or not await self.session.get(ChannelMembershipRecord, (channel_id, peer)):
            raise HTTPException(409, "This person is no longer in the conversation.")
        users = list(
            await self.session.scalars(
                select(UserRecord)
                .where(UserRecord.id.in_(peers))
                .order_by(UserRecord.id)
                .with_for_update()
            )
        )
        if len(users) != 2 or any(user.disabled_at for user in users):
            raise HTTPException(409, "This person is unavailable.")
        # Request UUID is also the call UUID, making uncertain create retries safe.
        existing = await self.session.get(CallRecord, request_id)
        if existing:
            if (
                existing.caller_id != actor
                or existing.channel_id != channel_id
                or existing.caller_device != device
            ):
                raise HTTPException(409, "Call request already used.")
            return existing
        seats = list(
            await self.session.scalars(
                select(CallSeatRecord).where(CallSeatRecord.user_id.in_(peers))
            )
        )
        now = datetime.now(UTC)
        for seat in seats:
            previous = await self.session.scalar(
                select(CallRecord).where(CallRecord.id == seat.call_id).with_for_update()
            )
            if previous:
                await expire(self.session, previous, now)
                if previous.status in LIVE:
                    raise HTTPException(409, "You or the other person are already in a call.")
        call = CallRecord(
            id=request_id,
            workspace_id=workspace,
            channel_id=channel_id,
            caller_id=actor,
            callee_id=peer,
            caller_session=auth_session,
            caller_device=device,
            status="ringing",
            created_at=now,
            caller_seen=now,
        )
        self.session.add(call)
        await self.session.flush()
        self.session.add_all([CallSeatRecord(user_id=user, call_id=call.id) for user in peers])
        await self.session.commit()
        return call

    async def load(self, call_id: UUID, actor: UUID) -> CallRecord:
        initial = await self.session.get(CallRecord, call_id)
        if not initial or actor not in (initial.caller_id, initial.callee_id):
            raise HTTPException(404, "Call not found.")
        await self.session.scalar(
            select(WorkspaceRecord)
            .where(WorkspaceRecord.id == initial.workspace_id)
            .with_for_update()
        )
        call = await self.session.scalar(
            select(CallRecord)
            .where(CallRecord.id == call_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if not call or not await has_access(self.session, call, actor):
            raise HTTPException(404, "Call not found.")
        await expire(self.session, call, datetime.now(UTC))
        return call

    async def action(
        self,
        call_id: UUID,
        actor: UUID,
        auth_session: UUID,
        device: UUID,
        action: str,
    ) -> CallRecord:
        call = await self.load(call_id, actor)
        now = datetime.now(UTC)
        if action == "accept":
            if actor != call.callee_id:
                raise HTTPException(403, "Only the recipient can accept a call.")
            if call.status == "ringing":
                call.status = "active"
                call.answered_at = now
                call.callee_device = device
                call.callee_session = auth_session
                call.caller_seen = call.callee_seen = now
            elif call.status != "active" or call.callee_device != device:
                await self.session.commit()
                raise HTTPException(409, "This call has ended or was answered on another tab.")
        elif action == "decline":
            if actor != call.callee_id:
                raise HTTPException(403, "Only the recipient can decline a call.")
            if call.status == "ringing":
                await finish(self.session, call, "declined", now)
        elif action == "end":
            if call.status in LIVE:
                await finish(
                    self.session, call, "cancelled" if call.status == "ringing" else "ended", now
                )
        elif action == "heartbeat" and call.status == "active":
            self.require_device(call, actor, device, auth_session)
            if actor == call.caller_id:
                call.caller_seen = now
            else:
                call.callee_seen = now
        await self.session.commit()
        return call

    @staticmethod
    def require_device(call: CallRecord, actor: UUID, device: UUID, auth_session: UUID) -> None:
        caller = actor == call.caller_id
        if (call.caller_device if caller else call.callee_device) != device or (
            call.caller_session if caller else call.callee_session
        ) != auth_session:
            raise HTTPException(409, "This call is open in another tab or session.")

    async def token(self, call_id: UUID, actor: UUID, auth_session: UUID, device: UUID) -> str:
        call = await self.load(call_id, actor)
        if call.status != "active":
            await self.session.commit()
            raise HTTPException(409, "This call is not active.")
        self.require_device(call, actor, device, auth_session)
        token = (
            livekit.AccessToken(
                self.settings.livekit_api_key.get_secret_value(),
                self.settings.livekit_api_secret.get_secret_value(),
            )
            .with_identity(str(actor))
            .with_ttl(timedelta(seconds=60))
            .with_grants(
                livekit.VideoGrants(
                    room_join=True,
                    room=room_name(call.id),
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=False,
                    can_publish_sources=["microphone"],
                )
            )
            .to_jwt()
        )
        await self.session.commit()
        return token

    async def inbox(self, actor: UUID) -> list[CallRecord]:
        rows = list(
            await self.session.scalars(
                select(CallRecord)
                .join(CallSeatRecord, CallSeatRecord.call_id == CallRecord.id)
                .where(CallSeatRecord.user_id == actor)
                .with_for_update(of=CallRecord)
            )
        )
        result = []
        for row in rows:
            await expire(self.session, row, datetime.now(UTC))
            if row.status in LIVE and await has_access(self.session, row, actor):
                result.append(row)
        await self.session.commit()
        return result

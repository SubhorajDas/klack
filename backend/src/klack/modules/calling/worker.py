"""Recover abandoned calls and retry media-room cleanup across API restarts."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from livekit import api as livekit
from sqlalchemy import select

from klack.core.config import Settings
from klack.core.db.session import SessionFactory
from klack.modules.calling.models import CallRecord
from klack.modules.calling.service import LIVE, expire, room_name, utc


async def sweep(factory: SessionFactory, settings: Settings) -> None:
    now = datetime.now(UTC)
    after: UUID | None = None
    while True:
        async with factory() as session:
            query = select(CallRecord).where(CallRecord.status.in_(LIVE))
            if after:
                query = query.where(CallRecord.id > after)
            calls = list(
                await session.scalars(
                    query.order_by(CallRecord.id).limit(200).with_for_update(skip_locked=True)
                )
            )
            for call in calls:
                await expire(session, call, now)
            await session.commit()
            if len(calls) < 200:
                break
            after = calls[-1].id
    async with factory() as session:
        calls = list(
            await session.scalars(
                select(CallRecord)
                .where(CallRecord.cleanup_after <= now)
                .order_by(CallRecord.cleanup_after)
                .limit(10)
                .with_for_update(skip_locked=True)
            )
        )
        if not calls:
            return
        async with livekit.LiveKitAPI(
            settings.livekit_url,
            settings.livekit_api_key.get_secret_value(),
            settings.livekit_api_secret.get_secret_value(),
        ) as client:

            async def close_room(call: CallRecord) -> None:
                try:
                    async with asyncio.timeout(3):
                        await client.room.delete_room(
                            livekit.DeleteRoomRequest(room=room_name(call.id))
                        )
                except livekit.TwirpError as exc:
                    if exc.code != "not_found":
                        call.cleanup_after = now + timedelta(seconds=30)
                        return
                except Exception:
                    call.cleanup_after = now + timedelta(seconds=30)
                    return
                # Repeat deletion while a previously issued 60-second token could still join.
                call.cleanup_after = (
                    now + timedelta(seconds=10)
                    if call.ended_at and now < utc(call.ended_at) + timedelta(seconds=90)
                    else None
                )

            await asyncio.gather(*(close_room(call) for call in calls))
        await session.commit()


async def run_call_maintenance(factory: SessionFactory, settings: Settings) -> None:
    while True:
        try:
            await sweep(factory, settings)
        except Exception:
            # Never include provider responses or credentials in logs.
            structlog.get_logger(__name__).warning("call_maintenance_retry")
        await asyncio.sleep(5)

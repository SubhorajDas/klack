"""Call races use PostgreSQL row locks, including simultaneous accept attempts."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import select
from test_messages_postgresql import _cleanup, _seed_conversation
from test_messages_postgresql import message_container as message_container
from test_messages_postgresql import migrated_message_schema as migrated_message_schema

from klack.modules.calling.models import CallSeatRecord
from klack.modules.calling.service import CallService
from klack.modules.channels.infrastructure.models import ChannelRecord
from klack.modules.identity.infrastructure.models import AuthSessionRecord

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL"),
]


async def test_simultaneous_calls_and_answers(message_container):
    container = message_container
    seeded = await _seed_conversation(container)
    config = container.settings.model_copy(
        update={
            "livekit_url": "wss://test.livekit.cloud",
            "livekit_api_key": SecretStr("key"),
            "livekit_api_secret": SecretStr("test-secret"),
        }
    )
    now = datetime.now(UTC)
    try:
        async with container.session_factory() as session:
            channel = await session.get(ChannelRecord, seeded.channel_id)
            channel.visibility = "private"
            channel.direct_key = ":".join(sorted([seeded.owner_id.hex, seeded.member_id.hex]))
            session.add_all(
                [
                    AuthSessionRecord(
                        id=uid,
                        user_id=uid,
                        csrf_token_hash="a" * 64,
                        created_at=now,
                        last_seen_at=now,
                        expires_at=now + timedelta(days=1),
                    )
                    for uid in (seeded.owner_id, seeded.member_id)
                ]
            )
            await session.commit()

        async def start(actor):
            async with container.session_factory() as session:
                try:
                    return await CallService(session, config).start(
                        seeded.workspace_id, seeded.channel_id, actor, actor, uuid4(), uuid4()
                    )
                except HTTPException as exc:
                    return exc.status_code

        results = await asyncio.gather(start(seeded.owner_id), start(seeded.member_id))
        assert results.count(409) == 1
        call = next(result for result in results if result != 409)

        async def accept():
            async with container.session_factory() as session:
                try:
                    return (
                        await CallService(session, config).action(
                            call.id, call.callee_id, call.callee_id, uuid4(), "accept"
                        )
                    ).status
                except HTTPException as exc:
                    return exc.status_code

        assert sorted(await asyncio.gather(accept(), accept()), key=str) == [409, "active"]
        async with container.session_factory() as session:
            assert (
                len(
                    list(
                        await session.scalars(
                            select(CallSeatRecord).where(CallSeatRecord.call_id == call.id)
                        )
                    )
                )
                == 2
            )
            await CallService(session, config).action(
                call.id, call.caller_id, call.caller_session, call.caller_device, "end"
            )
    finally:
        await _cleanup(container, seeded)

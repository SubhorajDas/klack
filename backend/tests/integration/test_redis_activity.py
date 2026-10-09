"""Real Redis cross-client broadcasts, multi-device leases, and crash expiry."""

import asyncio
import json
import os
from uuid import uuid4

import pytest

from klack.modules.realtime.infrastructure.activity import RedisActivity

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1" or not os.getenv("REDIS_URL"),
        reason="requires a disposable Redis test instance",
    ),
]


async def test_redis_cross_client_activity_and_multi_device_presence():
    namespace = f"klack:test:{uuid4().hex}"
    first = RedisActivity(os.environ["REDIS_URL"], namespace=namespace, lease_seconds=2)
    second = RedisActivity(os.environ["REDIS_URL"], namespace=namespace, lease_seconds=2)
    queue: asyncio.Queue[str] = asyncio.Queue()
    first.start(lambda _: None)
    second.start(queue.put_nowait)
    user, tab1, tab2 = uuid4(), uuid4(), uuid4()
    try:
        for _ in range(100):
            if first.ready and second.ready:
                break
            await asyncio.sleep(0.02)
        assert first.ready and second.ready
        for event in ("typing.changed", "read.changed"):
            await first.publish({"type": event, "channel_id": str(uuid4())})
            assert json.loads(await asyncio.wait_for(queue.get(), 2))["type"] == event
        assert await second.online([user]) == set()
        await first.lease(user, tab1, touch=True)
        assert json.loads(await asyncio.wait_for(queue.get(), 2))["type"] == "presence.changed"
        await second.lease(user, tab2, touch=True)
        await first.lease(user, tab1, touch=False)
        assert await first.online([user]) == {user}
        await second.lease(user, tab2, touch=False)
        assert await first.online([user]) == set()
        await first.lease(user, tab1, touch=True)
        # No disconnect callback: emulate a browser or API process dying.
        await asyncio.sleep(2.1)
        assert await second.online([user]) == set()
    finally:
        assert first.client is not None
        await first.client.delete(first.key(user))
        await first.stop()
        await second.stop()


async def test_redis_reconnect_restores_online_leases_before_advertising_availability():
    activity = RedisActivity(os.environ["REDIS_URL"], namespace=f"klack:test:{uuid4().hex}")
    user, connection = uuid4(), uuid4()
    restored = asyncio.Event()

    async def restore():
        assert not activity.ready
        await activity.lease(user, connection, touch=True)
        restored.set()

    activity.start(lambda _: None, restore)
    try:
        await asyncio.wait_for(restored.wait(), timeout=8)
        assert activity.ready
        assert await activity.online([user]) == {user}
        assert activity.client is not None
        await activity.client.delete(activity.key(user))
        restored.clear()
        await activity.client.connection_pool.disconnect()
        await asyncio.wait_for(restored.wait(), timeout=8)
        assert activity.ready
        assert await activity.online([user]) == {user}
    finally:
        assert activity.client is not None
        await activity.client.delete(activity.key(user))
        await activity.stop()

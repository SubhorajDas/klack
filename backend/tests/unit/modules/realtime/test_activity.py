"""Optional Redis configuration and unavailable-service behavior."""

from unittest.mock import AsyncMock
from uuid import uuid4

from redis.exceptions import ConnectionError

from klack.modules.realtime.infrastructure.activity import RedisActivity


async def test_disabled_activity_exposes_unknown_presence():
    activity = RedisActivity(None)
    activity.start(lambda _: None)
    assert await activity.online([uuid4()]) is None
    await activity.publish({"type": "read.changed"})
    await activity.lease(uuid4(), uuid4(), touch=True)
    await activity.stop()


async def test_redis_outage_does_not_break_message_operations(monkeypatch):
    activity = RedisActivity("redis://localhost:6379/0")
    assert activity.client is not None
    activity.ready = True
    monkeypatch.setattr(activity.client, "publish", AsyncMock(side_effect=ConnectionError()))
    monkeypatch.setattr(activity.client, "eval", AsyncMock(side_effect=ConnectionError()))
    await activity.publish({"type": "typing.changed"})
    await activity.lease(uuid4(), uuid4(), touch=True)
    assert await activity.online([uuid4()]) is None
    await activity.stop()

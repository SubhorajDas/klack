"""Redis broadcasts and expiring, per-connection online leases."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import suppress
from uuid import UUID

import structlog
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

logger = structlog.get_logger(__name__)

# Redis time avoids clock differences between API replicas. Each tab/device owns
# a lease; closing one connection cannot mark another connection offline.
LEASE_SCRIPT = """
local now = tonumber(redis.call('TIME')[1])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
local before = redis.call('ZCARD', KEYS[1])
if ARGV[1] == 'touch' then
  redis.call('ZADD', KEYS[1], now + tonumber(ARGV[3]), ARGV[2])
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]) * 2)
else
  redis.call('ZREM', KEYS[1], ARGV[2])
end
local after = redis.call('ZCARD', KEYS[1])
if (before == 0 and after > 0) or (before > 0 and after == 0) then
  redis.call('PUBLISH', ARGV[4], '{"type":"presence.changed"}')
end
return after
"""

ONLINE_SCRIPT = """
local now = tonumber(redis.call('TIME')[1])
local result = {}
for i, key in ipairs(KEYS) do
  redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
  result[i] = redis.call('ZCARD', key)
end
return result
"""


class RedisActivity:
    def __init__(
        self, url: str | None, *, lease_seconds: int = 75, namespace: str = "klack:realtime:v1"
    ) -> None:
        self.client = (
            Redis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=3,
                max_connections=20,
                retry=Retry(NoBackoff(), 0),
            )
            if url
            else None
        )
        self.lease_seconds = lease_seconds
        self.namespace = namespace
        self.channel = f"{namespace}:activity"
        self.ready = False
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    def start(
        self, receive: Callable[[str], None], restore: Callable[[], Awaitable[None]] | None = None
    ) -> None:
        if self.client is None:
            logger.warning("redis_activity_unconfigured", required_setting="REDIS_URL")
            return
        if self.client is not None and self._task is None:
            self._task = asyncio.create_task(self._listen(receive, restore))

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self.client is not None:
            await self.client.aclose()
        self.ready = False

    async def _listen(
        self, receive: Callable[[str], None], restore: Callable[[], Awaitable[None]] | None
    ) -> None:
        assert self.client is not None
        while not self._stopping.is_set():
            try:
                async with self.client.pubsub() as subscriber:
                    await subscriber.subscribe(self.channel)
                    if restore is not None:
                        await restore()
                    self.ready = True
                    while not self._stopping.is_set():
                        event = await subscriber.get_message(
                            ignore_subscribe_messages=True, timeout=1
                        )
                        if event is not None and event["type"] == "message":
                            receive(str(event["data"]))
            except Exception as exc:
                logger.warning("redis_activity_unavailable", exception_type=type(exc).__name__)
            finally:
                self.ready = False
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=2)

    async def publish(self, payload: dict[str, object]) -> None:
        if self.client is None or self._stopping.is_set():
            return
        try:
            await self.client.publish(self.channel, json.dumps(payload))
        except (RedisError, OSError):
            logger.warning("redis_activity_publish_failed")

    def key(self, user_id: UUID) -> str:
        return f"{self.namespace}:online:{user_id}"

    async def lease(self, user_id: UUID, connection_id: UUID, *, touch: bool) -> None:
        if self.client is None or self._stopping.is_set():
            return
        try:
            await self.client.eval(
                LEASE_SCRIPT,
                1,
                self.key(user_id),
                "touch" if touch else "remove",
                str(connection_id),
                self.lease_seconds,
                self.channel,
            )
        except (RedisError, OSError):
            logger.warning("redis_presence_update_failed")

    async def online(self, users: list[UUID]) -> set[UUID] | None:
        if self.client is None or not self.ready:
            return None
        if not users:
            return set()
        try:
            counts = await self.client.eval(
                ONLINE_SCRIPT, len(users), *[self.key(u) for u in users]
            )
            return {user for user, count in zip(users, counts, strict=True) if count > 0}
        except (RedisError, OSError):
            return None

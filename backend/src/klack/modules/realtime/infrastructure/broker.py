"""PostgreSQL LISTEN/NOTIFY fanout to process-local WebSockets."""

import asyncio
import json
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]
import structlog
from sqlalchemy.engine import make_url

from klack.core.db.session import SessionFactory
from klack.modules.channels.application.service import ChannelContentAccessService
from klack.modules.channels.domain.errors import ChannelError
from klack.modules.channels.infrastructure.repository import SqlAlchemyChannelRepository
from klack.modules.identity.infrastructure.repository import SqlAlchemyIdentityRepository
from klack.modules.messaging.api.schemas import MessageResponse
from klack.modules.messaging.infrastructure.repository import SqlAlchemyMessageRepository
from klack.modules.realtime.application.connections import (
    RealtimeConnection,
    RealtimeConnectionManager,
)
from klack.modules.realtime.infrastructure.activity import RedisActivity
from klack.modules.realtime.infrastructure.repository import (
    REALTIME_NOTIFY_CHANNEL,
    SqlAlchemyRealtimeEventRepository,
)
from klack.modules.workspaces.application.service import WorkspaceAccessService
from klack.modules.workspaces.domain.errors import WorkspaceError
from klack.modules.workspaces.infrastructure.repository import SqlAlchemyWorkspaceRepository

logger = structlog.get_logger(__name__)


def _exception_type(exc: Exception) -> str:
    exception_class = type(exc)
    return f"{exception_class.__module__}.{exception_class.__qualname__}"


class PostgresRealtimeBroker:
    """Broadcast committed database signals to authorized local subscribers."""

    def __init__(
        self,
        *,
        database_url: str,
        session_factory: SessionFactory,
        manager: RealtimeConnectionManager,
        enabled: bool,
        retry_seconds: float,
        authorization_recheck_seconds: float,
        event_retention_seconds: int,
        cleanup_interval_seconds: int,
        cleanup_batch_size: int,
        redis_url: str | None = None,
        presence_lease_seconds: int = 75,
    ) -> None:
        url = make_url(database_url).set(drivername="postgresql")
        self._dsn = url.render_as_string(hide_password=False)
        self._session_factory = session_factory
        self.manager = manager
        self.enabled = enabled
        self._retry_seconds = retry_seconds
        self._authorization_recheck_seconds = authorization_recheck_seconds
        self._event_retention = timedelta(seconds=event_retention_seconds)
        self._cleanup_interval_seconds = cleanup_interval_seconds
        self._cleanup_batch_size = cleanup_batch_size
        self._stopping = asyncio.Event()
        self._supervisor: asyncio.Task[None] | None = None
        self._authorization_sweeper: asyncio.Task[None] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._listener: asyncpg.Connection | None = None
        self._delivery_tasks: set[asyncio.Task[None]] = set()
        self.ready = False
        self.activity = RedisActivity(redis_url, lease_seconds=presence_lease_seconds)

    async def start(self) -> None:
        if self.enabled and self._supervisor is None:
            self.activity.start(self._activity_notification, self._restore_presence)
            self._supervisor = asyncio.create_task(
                self._listen_forever(),
                name="klack-realtime-listener",
            )
            self._authorization_sweeper = asyncio.create_task(
                self._sweep_authorization(),
                name="klack-realtime-authorization-sweeper",
            )
            self._cleanup_task = asyncio.create_task(
                self._cleanup_events(),
                name="klack-realtime-event-cleanup",
            )

    async def stop(self) -> None:
        self._stopping.set()
        await self.activity.stop()
        listener = self._listener
        if listener is not None and not listener.is_closed():
            await listener.close()
        if self._supervisor is not None:
            await self._supervisor
        if self._authorization_sweeper is not None:
            await self._authorization_sweeper
        if self._cleanup_task is not None:
            await self._cleanup_task
        for task in list(self._delivery_tasks):
            task.cancel()
        if self._delivery_tasks:
            await asyncio.gather(*self._delivery_tasks, return_exceptions=True)
        self.manager.close_all(code=1012)

    async def authorize_subscription(
        self,
        connection: RealtimeConnection,
        *,
        workspace_id: UUID,
        channel_id: UUID,
    ) -> bool:
        session_active, channel_access = await self._authorization(
            connection,
            workspace_id=workspace_id,
            channel_id=channel_id,
        )
        if not session_active:
            self.manager.request_close(connection, code=4401)
        return session_active and channel_access

    async def _listen_forever(self) -> None:
        while not self._stopping.is_set():
            terminated = asyncio.Event()
            try:
                listener = await asyncpg.connect(self._dsn)
                self._listener = listener
                listener.add_termination_listener(
                    lambda _connection, event=terminated: event.set(),
                )
                await listener.add_listener(REALTIME_NOTIFY_CHANNEL, self._notification)
                self.ready = True
                logger.info("realtime_listener_ready")
                stop_wait = asyncio.create_task(self._stopping.wait())
                terminated_wait = asyncio.create_task(terminated.wait())
                done, pending = await asyncio.wait(
                    {stop_wait, terminated_wait},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                del done
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            except Exception as exc:
                logger.error("realtime_listener_failed", exception_type=_exception_type(exc))
            finally:
                self.ready = False
                self.manager.close_all(code=1013)
                listener = self._listener
                self._listener = None
                if listener is not None and not listener.is_closed():
                    await listener.close()
            if not self._stopping.is_set():
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._stopping.wait(), timeout=self._retry_seconds)

    def _notification(
        self,
        _connection: asyncpg.Connection,
        _process_id: int,
        _channel: str,
        payload: str,
    ) -> None:
        try:
            event_id = UUID(payload)
        except ValueError:
            logger.warning("realtime_notification_invalid")
            return
        task = asyncio.create_task(self._deliver(event_id))
        self._delivery_tasks.add(task)
        task.add_done_callback(self._delivery_tasks.discard)

    async def publish_activity(self, payload: dict[str, object]) -> None:
        if not self.enabled:
            return
        await self.activity.publish(payload)

    def _activity_notification(self, payload: str) -> None:
        try:
            envelope = json.loads(payload)
            if not isinstance(envelope, dict):
                return
            if envelope.get("type") == "presence.changed":
                task = asyncio.create_task(self._presence_changed())
                self._delivery_tasks.add(task)
                task.add_done_callback(self._delivery_tasks.discard)
                return
            workspace_id = UUID(envelope["workspace_id"])
            channel_id = UUID(envelope["channel_id"])
            if envelope["type"] not in {"typing.changed", "read.changed"}:
                return
        except (ValueError, KeyError, TypeError):
            return
        task = asyncio.create_task(self._fanout_activity(workspace_id, channel_id, envelope))
        self._delivery_tasks.add(task)
        task.add_done_callback(self._delivery_tasks.discard)

    async def _presence_changed(self) -> None:
        # Send only a refresh signal. User identities are exposed exclusively by
        # the authenticated, contact-scoped presence snapshot endpoint.
        for connection in self.manager.connections():
            if connection.access_expires_at > datetime.now(UTC):
                self.manager.enqueue(connection, {"type": "presence.changed"})
            else:
                self.manager.request_close(connection, code=4401)

    async def touch_presence(self, connection: RealtimeConnection) -> None:
        await self.activity.lease(connection.user_id, connection.id, touch=True)

    async def _restore_presence(self) -> None:
        for connection in self.manager.connections():
            if await self._session_active(connection):
                await self.touch_presence(connection)

    async def remove_presence(self, connection: RealtimeConnection) -> None:
        await self.activity.lease(connection.user_id, connection.id, touch=False)

    async def _fanout_activity(
        self, workspace_id: UUID, channel_id: UUID, envelope: dict[str, object]
    ) -> None:
        try:
            candidates = self.manager.candidates(workspace_id=workspace_id, channel_id=channel_id)
            for connection in candidates:
                active, allowed = await self._authorization(
                    connection, workspace_id=workspace_id, channel_id=channel_id
                )
                if not active:
                    self.manager.request_close(connection, code=4401)
                elif allowed:
                    self.manager.enqueue(connection, envelope)
                else:
                    self.manager.unsubscribe(connection, channel_id=channel_id)
                    self.manager.enqueue(
                        connection,
                        {
                            "type": "subscription.revoked",
                            "channel_id": str(channel_id),
                        },
                    )
        except Exception as exc:
            logger.error("realtime_activity_delivery_failed", exception_type=_exception_type(exc))

    async def _deliver(self, event_id: UUID) -> None:
        try:
            async with self._session_factory() as session:
                event = await SqlAlchemyRealtimeEventRepository(session).get_event(event_id)
                if event is None or event.event_type != "message.changed":
                    return
                message = await SqlAlchemyMessageRepository(session).get_message(
                    workspace_id=event.workspace_id,
                    channel_id=event.channel_id,
                    message_id=event.entity_id,
                )
            if message is None:
                return
            response = MessageResponse.from_domain(message).model_dump(mode="json")
            envelope: dict[str, object] = {
                "type": "message.changed",
                "event_id": str(event.id),
                "workspace_id": str(event.workspace_id),
                "channel_id": str(event.channel_id),
                "message": response,
            }
            candidates = self.manager.candidates(
                workspace_id=event.workspace_id,
                channel_id=event.channel_id,
            )
            for connection in candidates:
                session_active, channel_access = await self._authorization(
                    connection,
                    workspace_id=event.workspace_id,
                    channel_id=event.channel_id,
                )
                if not session_active:
                    self.manager.request_close(connection, code=4401)
                elif not channel_access:
                    self.manager.unsubscribe(connection, channel_id=event.channel_id)
                    self.manager.enqueue(
                        connection,
                        {
                            "type": "subscription.revoked",
                            "channel_id": str(event.channel_id),
                        },
                    )
                else:
                    self.manager.enqueue(connection, envelope)
        except Exception as exc:
            logger.error(
                "realtime_event_delivery_failed",
                event_id=str(event_id),
                exception_type=_exception_type(exc),
            )

    async def _authorization(
        self,
        connection: RealtimeConnection,
        *,
        workspace_id: UUID,
        channel_id: UUID,
    ) -> tuple[bool, bool]:
        now = datetime.now(UTC)
        if connection.access_expires_at <= now:
            return False, False
        async with self._session_factory() as session:
            identity = await SqlAlchemyIdentityRepository(session).get_active_session(
                session_id=connection.session_id,
                user_id=connection.user_id,
                now=now,
            )
            if identity is None:
                return False, False
            access = ChannelContentAccessService(
                repository=SqlAlchemyChannelRepository(session),
                workspace_access=WorkspaceAccessService(SqlAlchemyWorkspaceRepository(session)),
            )
            try:
                await access.require_access(
                    actor_user_id=connection.user_id,
                    workspace_id=workspace_id,
                    channel_id=channel_id,
                    for_update=False,
                )
            except (ChannelError, WorkspaceError):
                return True, False
        return True, True

    async def _session_active(self, connection: RealtimeConnection) -> bool:
        now = datetime.now(UTC)
        if connection.access_expires_at <= now:
            return False
        async with self._session_factory() as session:
            identity = await SqlAlchemyIdentityRepository(session).get_active_session(
                session_id=connection.session_id,
                user_id=connection.user_id,
                now=now,
            )
        return identity is not None

    async def _sweep_authorization(self) -> None:
        while not self._stopping.is_set():
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=self._authorization_recheck_seconds,
                )
            if self._stopping.is_set():
                return
            for connection in self.manager.connections():
                if not connection.subscriptions:
                    if not await self._session_active(connection):
                        self.manager.request_close(connection, code=4401)
                    continue
                for workspace_id, channel_id in list(connection.subscriptions):
                    session_active, channel_access = await self._authorization(
                        connection,
                        workspace_id=workspace_id,
                        channel_id=channel_id,
                    )
                    if not session_active:
                        self.manager.request_close(connection, code=4401)
                        break
                    if not channel_access:
                        self.manager.unsubscribe(connection, channel_id=channel_id)
                        self.manager.enqueue(
                            connection,
                            {
                                "type": "subscription.revoked",
                                "channel_id": str(channel_id),
                            },
                        )

    async def _cleanup_events(self) -> None:
        while not self._stopping.is_set():
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=self._cleanup_interval_seconds,
                )
            if self._stopping.is_set():
                return
            try:
                async with self._session_factory() as session:
                    deleted = await SqlAlchemyRealtimeEventRepository(session).delete_events_before(
                        before=datetime.now(UTC) - self._event_retention,
                        limit=self._cleanup_batch_size,
                    )
                if deleted:
                    logger.info("realtime_events_cleaned", deleted_count=deleted)
            except Exception as exc:
                logger.error(
                    "realtime_event_cleanup_failed",
                    exception_type=_exception_type(exc),
                )

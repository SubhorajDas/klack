"""Authenticated realtime channel subscriptions."""

import asyncio
from datetime import UTC, datetime
from time import monotonic

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from klack.modules.identity.domain.errors import IdentityError
from klack.modules.identity.infrastructure.repository import SqlAlchemyIdentityRepository
from klack.modules.realtime.api.schemas import (
    PongCommand,
    SubscribeCommand,
    UnsubscribeCommand,
    inbound_command_adapter,
)
from klack.modules.realtime.application.connections import RealtimeConnection
from klack.modules.realtime.infrastructure.broker import PostgresRealtimeBroker

router = APIRouter(tags=["realtime"])
REALTIME_SUBPROTOCOL = "klack.realtime.v1"


async def _send_messages(connection: RealtimeConnection) -> None:
    while True:
        payload = await connection.outbound.get()
        if payload is None:
            await connection.websocket.close(code=connection.close_code)
            return
        await connection.websocket.send_json(payload)


@router.websocket("/realtime")
async def realtime(websocket: WebSocket) -> None:
    """Deliver committed message snapshots to authorized channel subscribers."""
    container = websocket.app.state.container
    broker: PostgresRealtimeBroker = container.realtime_broker
    settings = container.settings
    origins = websocket.headers.getlist("origin")
    offered_protocols = websocket.scope.get("subprotocols", [])
    if not broker.enabled:
        await websocket.close(code=4403)
        return
    if not broker.ready:
        await websocket.close(code=1013)
        return
    if (
        len(origins) != 1
        or origins[0] != settings.auth_trusted_origin
        or REALTIME_SUBPROTOCOL not in offered_protocols
    ):
        await websocket.close(code=4403)
        return

    raw_access = websocket.cookies.get("klack_access")
    try:
        if raw_access is None:
            raise ValueError
        claims = container.access_token_codec.decode(raw_access)
        async with container.session_factory() as session:
            identity = await SqlAlchemyIdentityRepository(session).get_active_session(
                session_id=claims.session_id,
                user_id=claims.user_id,
                now=datetime.now(UTC),
            )
        if identity is None:
            raise ValueError
    except (IdentityError, ValueError):
        await websocket.close(code=4401)
        return

    await websocket.accept(subprotocol=REALTIME_SUBPROTOCOL)
    connection = broker.manager.register(
        websocket=websocket,
        user_id=identity.user.id,
        session_id=identity.session.id,
        access_expires_at=claims.expires_at,
    )
    if connection is None:
        await websocket.close(code=1013)
        return
    sender = asyncio.create_task(_send_messages(connection))
    broker.manager.enqueue(
        connection,
        {
            "type": "hello",
            "connection_id": str(connection.id),
            "access_expires_at": claims.expires_at.isoformat(),
        },
    )
    try:
        command_window_started = monotonic()
        command_count = 0
        while True:
            if claims.expires_at <= datetime.now(UTC):
                broker.manager.request_close(connection, code=4401)
                await sender
                return
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=settings.realtime_heartbeat_seconds,
                )
            except TimeoutError:
                broker.manager.enqueue(connection, {"type": "ping"})
                continue
            now_monotonic = monotonic()
            if now_monotonic - command_window_started >= 60:
                command_window_started = now_monotonic
                command_count = 0
            command_count += 1
            if command_count > settings.realtime_commands_per_minute:
                broker.manager.request_close(connection, code=4408)
                await sender
                return
            if len(raw.encode("utf-8")) > settings.realtime_max_frame_bytes:
                broker.manager.request_close(connection, code=4408)
                await sender
                return
            try:
                command = inbound_command_adapter.validate_json(raw)
            except ValidationError:
                broker.manager.enqueue(
                    connection,
                    {"type": "error", "code": "invalid_command"},
                )
                continue
            if isinstance(command, SubscribeCommand):
                allowed = await broker.authorize_subscription(
                    connection,
                    workspace_id=command.workspace_id,
                    channel_id=command.channel_id,
                )
                if not allowed:
                    broker.manager.enqueue(
                        connection,
                        {
                            "type": "error",
                            "request_id": str(command.request_id),
                            "code": "channel_access_denied",
                        },
                    )
                    continue
                subscribed = broker.manager.subscribe(
                    connection,
                    workspace_id=command.workspace_id,
                    channel_id=command.channel_id,
                    max_subscriptions=settings.realtime_max_subscriptions_per_connection,
                )
                broker.manager.enqueue(
                    connection,
                    {
                        "type": "subscribed" if subscribed else "error",
                        "request_id": str(command.request_id),
                        "channel_id": str(command.channel_id),
                        **({} if subscribed else {"code": "subscription_limit_reached"}),
                    },
                )
            elif isinstance(command, UnsubscribeCommand):
                broker.manager.unsubscribe(connection, channel_id=command.channel_id)
                broker.manager.enqueue(
                    connection,
                    {
                        "type": "unsubscribed",
                        "request_id": str(command.request_id),
                        "channel_id": str(command.channel_id),
                    },
                )
            elif isinstance(command, PongCommand):
                continue
    except WebSocketDisconnect:
        pass
    finally:
        broker.manager.unregister(connection)
        if not sender.done():
            sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)

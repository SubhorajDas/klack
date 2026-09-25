"""Bounded local realtime connection state."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from klack.modules.realtime.application.connections import RealtimeConnectionManager


class StubWebSocket:
    pass


def register(manager: RealtimeConnectionManager, identifier: int = 1):
    connection = manager.register(
        websocket=StubWebSocket(),  # type: ignore[arg-type]
        user_id=UUID(int=identifier),
        session_id=UUID(int=identifier + 10),
        access_expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    assert connection is not None
    return connection


def test_connection_and_subscription_limits_are_bounded() -> None:
    manager = RealtimeConnectionManager(queue_size=2, max_connections=1)
    connection = register(manager)
    assert (
        manager.register(
            websocket=StubWebSocket(),  # type: ignore[arg-type]
            user_id=UUID(int=2),
            session_id=UUID(int=12),
            access_expires_at=datetime.now(UTC) + timedelta(minutes=1),
        )
        is None
    )
    assert manager.subscribe(
        connection,
        workspace_id=UUID(int=20),
        channel_id=UUID(int=30),
        max_subscriptions=1,
    )
    assert not manager.subscribe(
        connection,
        workspace_id=UUID(int=20),
        channel_id=UUID(int=31),
        max_subscriptions=1,
    )
    assert manager.candidates(workspace_id=UUID(int=20), channel_id=UUID(int=30)) == [
        connection,
    ]


def test_slow_consumer_is_replaced_with_retryable_close() -> None:
    manager = RealtimeConnectionManager(queue_size=1, max_connections=1)
    connection = register(manager)
    assert manager.enqueue(connection, {"type": "first"})
    assert not manager.enqueue(connection, {"type": "overflow"})
    assert connection.close_code == 1013
    assert connection.outbound.get_nowait() is None
    with pytest.raises(asyncio.QueueEmpty):
        connection.outbound.get_nowait()

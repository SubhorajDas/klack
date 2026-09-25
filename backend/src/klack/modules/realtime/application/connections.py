"""Bounded process-local WebSocket connection registry."""

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import WebSocket


@dataclass(slots=True)
class RealtimeConnection:
    """One authenticated socket and its authorized channel interests."""

    id: UUID
    websocket: WebSocket
    user_id: UUID
    session_id: UUID
    access_expires_at: datetime
    outbound: asyncio.Queue[dict[str, object] | None]
    subscriptions: set[tuple[UUID, UUID]] = field(default_factory=set)
    close_code: int = 1000


class RealtimeConnectionManager:
    """Own bounded local socket state; no authorization is cached here."""

    def __init__(self, *, queue_size: int, max_connections: int) -> None:
        self._queue_size = queue_size
        self._max_connections = max_connections
        self._connections: dict[UUID, RealtimeConnection] = {}

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    def register(
        self,
        *,
        websocket: WebSocket,
        user_id: UUID,
        session_id: UUID,
        access_expires_at: datetime,
    ) -> RealtimeConnection | None:
        if len(self._connections) >= self._max_connections:
            return None
        connection = RealtimeConnection(
            id=uuid4(),
            websocket=websocket,
            user_id=user_id,
            session_id=session_id,
            access_expires_at=access_expires_at,
            outbound=asyncio.Queue(maxsize=self._queue_size),
        )
        self._connections[connection.id] = connection
        return connection

    def unregister(self, connection: RealtimeConnection) -> None:
        self._connections.pop(connection.id, None)

    def subscribe(
        self,
        connection: RealtimeConnection,
        *,
        workspace_id: UUID,
        channel_id: UUID,
        max_subscriptions: int,
    ) -> bool:
        subscription = (workspace_id, channel_id)
        if subscription in connection.subscriptions:
            return True
        if len(connection.subscriptions) >= max_subscriptions:
            return False
        connection.subscriptions.add(subscription)
        return True

    @staticmethod
    def unsubscribe(connection: RealtimeConnection, *, channel_id: UUID) -> None:
        connection.subscriptions = {
            item for item in connection.subscriptions if item[1] != channel_id
        }

    def candidates(self, *, workspace_id: UUID, channel_id: UUID) -> list[RealtimeConnection]:
        subscription = (workspace_id, channel_id)
        return [
            connection
            for connection in self._connections.values()
            if subscription in connection.subscriptions
        ]

    def connections(self) -> list[RealtimeConnection]:
        """Return a stable snapshot for periodic authorization checks."""
        return list(self._connections.values())

    @staticmethod
    def enqueue(connection: RealtimeConnection, payload: dict[str, object]) -> bool:
        try:
            connection.outbound.put_nowait(payload)
        except asyncio.QueueFull:
            RealtimeConnectionManager.request_close(connection, code=1013)
            return False
        return True

    @staticmethod
    def request_close(connection: RealtimeConnection, *, code: int) -> None:
        connection.close_code = code
        while not connection.outbound.empty():
            try:
                connection.outbound.get_nowait()
            except asyncio.QueueEmpty:
                break
        with suppress(asyncio.QueueFull):
            connection.outbound.put_nowait(None)

    def close_all(self, *, code: int) -> None:
        for connection in list(self._connections.values()):
            self.request_close(connection, code=code)

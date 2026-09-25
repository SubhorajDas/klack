"""WebSocket handshake and command protocol contracts."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from starlette.websockets import WebSocketDisconnect

from klack.modules.realtime.api.router import REALTIME_SUBPROTOCOL, realtime
from klack.modules.realtime.application.connections import RealtimeConnectionManager

NOW = datetime.now(UTC)
USER_ID = UUID(int=1)
SESSION_ID = UUID(int=2)
WORKSPACE_ID = UUID(int=3)
CHANNEL_ID = UUID(int=4)
REQUEST_ID = UUID(int=5)


class FakeHeaders:
    def __init__(self, origin: str | None) -> None:
        self._origin = origin

    def getlist(self, name: str) -> list[str]:
        if name == "origin" and self._origin is not None:
            return [self._origin]
        return []


class FakeWebSocket:
    def __init__(
        self,
        container: object,
        *,
        origin: str | None = "http://test",
        protocols: list[str] | None = None,
        access_cookie: str | None = "access",
        commands: list[str] | None = None,
    ) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace(container=container))
        self.headers = FakeHeaders(origin)
        self.scope = {"subprotocols": protocols or [REALTIME_SUBPROTOCOL]}
        self.cookies = {} if access_cookie is None else {"klack_access": access_cookie}
        self.commands: asyncio.Queue[str] = asyncio.Queue()
        for command in commands or []:
            self.commands.put_nowait(command)
        self.accepted_protocol: str | None = None
        self.sent: list[dict[str, object]] = []
        self.closed: list[int] = []

    async def accept(self, *, subprotocol: str) -> None:
        self.accepted_protocol = subprotocol

    async def close(self, *, code: int) -> None:
        self.closed.append(code)

    async def send_json(self, payload: dict[str, object]) -> None:
        self.sent.append(payload)
        await asyncio.sleep(0)

    async def receive_text(self) -> str:
        await asyncio.sleep(0)
        if self.commands.empty():
            raise WebSocketDisconnect(1000)
        return self.commands.get_nowait()


class FakeAccessTokenCodec:
    def __init__(self, *, valid: bool = True) -> None:
        self.valid = valid

    def decode(self, raw: str) -> object:
        assert raw == "access"
        if not self.valid:
            raise ValueError
        return SimpleNamespace(
            user_id=USER_ID,
            session_id=SESSION_ID,
            expires_at=NOW + timedelta(minutes=10),
        )


class FakeIdentityRepository:
    active = True

    def __init__(self, session: object) -> None:
        del session

    async def get_active_session(self, **kwargs: object) -> object | None:
        del kwargs
        if not self.active:
            return None
        return SimpleNamespace(
            user=SimpleNamespace(id=USER_ID),
            session=SimpleNamespace(id=SESSION_ID),
        )


class FakeBroker:
    def __init__(self, *, enabled: bool = True, ready: bool = True, allowed: bool = True) -> None:
        self.enabled = enabled
        self.ready = ready
        self.allowed = allowed
        self.manager = RealtimeConnectionManager(queue_size=20, max_connections=10)

    async def authorize_subscription(self, connection: object, **kwargs: object) -> bool:
        del connection, kwargs
        return self.allowed


def container(broker: FakeBroker, *, valid_token: bool = True) -> object:
    @asynccontextmanager
    async def session_factory():
        yield object()

    return SimpleNamespace(
        realtime_broker=broker,
        access_token_codec=FakeAccessTokenCodec(valid=valid_token),
        session_factory=session_factory,
        settings=SimpleNamespace(
            auth_trusted_origin="http://test",
            realtime_heartbeat_seconds=30,
            realtime_max_frame_bytes=16_384,
            realtime_commands_per_minute=120,
            realtime_max_subscriptions_per_connection=2,
        ),
    )


@pytest.mark.parametrize(
    ("broker", "origin", "protocols", "expected"),
    [
        (FakeBroker(enabled=False), "http://test", [REALTIME_SUBPROTOCOL], 4403),
        (FakeBroker(ready=False), "http://test", [REALTIME_SUBPROTOCOL], 1013),
        (FakeBroker(), "http://other", [REALTIME_SUBPROTOCOL], 4403),
        (FakeBroker(), "http://test", ["other"], 4403),
    ],
)
async def test_handshake_rejects_unavailable_or_untrusted_clients(
    broker: FakeBroker,
    origin: str,
    protocols: list[str],
    expected: int,
) -> None:
    websocket = FakeWebSocket(container(broker), origin=origin, protocols=protocols)
    await realtime(websocket)  # type: ignore[arg-type]
    assert websocket.closed == [expected]
    assert websocket.accepted_protocol is None


@pytest.mark.parametrize(("cookie", "valid_token"), [(None, True), ("access", False)])
async def test_handshake_requires_a_valid_access_cookie(
    monkeypatch: pytest.MonkeyPatch,
    cookie: str | None,
    valid_token: bool,
) -> None:
    monkeypatch.setattr(
        "klack.modules.realtime.api.router.SqlAlchemyIdentityRepository",
        FakeIdentityRepository,
    )
    broker = FakeBroker()
    websocket = FakeWebSocket(
        container(broker, valid_token=valid_token),
        access_cookie=cookie,
    )
    await realtime(websocket)  # type: ignore[arg-type]
    assert websocket.closed == [4401]


async def test_commands_subscribe_unsubscribe_and_reject_invalid_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "klack.modules.realtime.api.router.SqlAlchemyIdentityRepository",
        FakeIdentityRepository,
    )
    commands = [
        "not-json",
        json.dumps(
            {
                "type": "subscribe",
                "request_id": str(REQUEST_ID),
                "workspace_id": str(WORKSPACE_ID),
                "channel_id": str(CHANNEL_ID),
            },
        ),
        '{"type":"pong"}',
        json.dumps(
            {
                "type": "unsubscribe",
                "request_id": str(REQUEST_ID),
                "channel_id": str(CHANNEL_ID),
            },
        ),
    ]
    broker = FakeBroker()
    websocket = FakeWebSocket(container(broker), commands=commands)
    await realtime(websocket)  # type: ignore[arg-type]
    assert websocket.accepted_protocol == REALTIME_SUBPROTOCOL
    assert [payload["type"] for payload in websocket.sent] == [
        "hello",
        "error",
        "subscribed",
        "unsubscribed",
    ]
    assert broker.manager.connection_count == 0


async def test_denied_subscription_uses_generic_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "klack.modules.realtime.api.router.SqlAlchemyIdentityRepository",
        FakeIdentityRepository,
    )
    command = json.dumps(
        {
            "type": "subscribe",
            "request_id": str(REQUEST_ID),
            "workspace_id": str(WORKSPACE_ID),
            "channel_id": str(CHANNEL_ID),
        },
    )
    websocket = FakeWebSocket(container(FakeBroker(allowed=False)), commands=[command])
    await realtime(websocket)  # type: ignore[arg-type]
    assert websocket.sent[-1] == {
        "type": "error",
        "request_id": str(REQUEST_ID),
        "code": "channel_access_denied",
    }

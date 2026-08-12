"""Request correlation and structured access logging middleware."""

from __future__ import annotations

from time import perf_counter
from typing import Final
from uuid import UUID, uuid4

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.contextvars import bind_contextvars, clear_contextvars, get_contextvars

REQUEST_ID_HEADER: Final[bytes] = b"x-request-id"
REQUEST_ID_RESPONSE_HEADER: Final[str] = "X-Request-ID"
HEALTH_ROUTES: Final[frozenset[str]] = frozenset({"/health/live", "/health/ready"})

logger = structlog.get_logger(__name__)


def _request_id_from_scope(scope: Scope) -> str:
    """Return a canonical caller UUID or generate a safe replacement."""
    values = [value for key, value in scope.get("headers", []) if key.lower() == REQUEST_ID_HEADER]
    if len(values) != 1 or len(values[0]) > 36:
        return str(uuid4())

    try:
        candidate = values[0].decode("ascii")
        parsed = UUID(candidate)
    except (UnicodeDecodeError, ValueError):
        return str(uuid4())

    return candidate if candidate == str(parsed) else str(uuid4())


def _route_template(scope: Scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "<unmatched>"


class RequestContextMiddleware:
    """Bind a request ID, echo it in responses, and write one access event."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id_from_scope(scope)
        state = scope.setdefault("state", {})
        state["request_id"] = request_id

        inherited_context = get_contextvars()
        bind_contextvars(request_id=request_id)
        started_at = perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_RESPONSE_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            duration_ms = round((perf_counter() - started_at) * 1_000, 3)
            route = _route_template(scope)
            log = logger.debug if route in HEALTH_ROUTES and status_code < 500 else logger.info
            try:
                log(
                    "http_request_completed",
                    http_method=scope.get("method"),
                    http_route=route,
                    http_status_code=status_code,
                    duration_ms=duration_ms,
                )
            finally:
                clear_contextvars()
                bind_contextvars(**inherited_context)

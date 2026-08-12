"""Operational endpoint and request-correlation contracts."""

import json
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from structlog.contextvars import bind_contextvars, get_contextvars, reset_contextvars

from klack.bootstrap import create_app
from klack.core.config import Settings
from klack.core.db.session import DatabaseHealthCheck, DatabaseUnavailableError


async def test_liveness_is_process_only(settings: Settings) -> None:
    calls = 0

    async def unexpected_database_check() -> None:
        nonlocal calls
        calls += 1
        raise AssertionError("liveness must not touch PostgreSQL")

    app = create_app(settings, database_health_check=unexpected_database_check)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client,
    ):
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"
    UUID(response.headers["x-request-id"])
    assert calls == 0


async def test_readiness_checks_postgresql(client: AsyncClient) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"


async def test_readiness_returns_sanitized_503(settings: Settings) -> None:
    async def unavailable_database() -> None:
        raise DatabaseUnavailableError("credential@private-db:5432")

    app = create_app(settings, database_health_check=unavailable_database)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client,
    ):
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "private-db" not in response.text


async def test_health_routes_are_absent_from_openapi(client: AsyncClient) -> None:
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    assert "/health/live" not in response.json()["paths"]
    assert "/health/ready" not in response.json()["paths"]


async def test_valid_request_id_is_preserved(client: AsyncClient) -> None:
    request_id = str(uuid4())

    response = await client.get("/health/live", headers={"X-Request-ID": request_id})

    assert response.headers.get_list("x-request-id") == [request_id]


async def test_invalid_request_id_is_replaced(client: AsyncClient) -> None:
    response = await client.get("/health/live", headers={"X-Request-ID": "not-a-uuid"})

    generated = response.headers["x-request-id"]
    UUID(generated)
    assert generated != "not-a-uuid"


async def test_duplicate_request_ids_are_replaced(client: AsyncClient) -> None:
    response = await client.get(
        "/health/live",
        headers=[("X-Request-ID", str(uuid4())), ("X-Request-ID", str(uuid4()))],
    )

    UUID(response.headers["x-request-id"])
    assert len(response.headers.get_list("x-request-id")) == 1


async def test_request_id_is_present_on_404(client: AsyncClient) -> None:
    response = await client.get("/does-not-exist")

    assert response.status_code == 404
    UUID(response.headers["x-request-id"])


async def test_request_context_restores_outer_context(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    async def inspect_context() -> dict[str, object]:
        request_context = get_contextvars()
        bind_contextvars(request_only="must-not-leak")
        return request_context

    app.add_api_route("/context", inspect_context)
    tokens = bind_contextvars(trace_id="outer-trace", request_id="outer-request")
    try:
        response = await client.get("/context")

        assert response.headers["x-request-id"] != "outer-request"
        assert response.json() == {
            "trace_id": "outer-trace",
            "request_id": response.headers["x-request-id"],
        }
        assert get_contextvars() == {
            "trace_id": "outer-trace",
            "request_id": "outer-request",
        }
    finally:
        reset_contextvars(**tokens)


async def test_unhandled_error_is_safe_and_correlated(
    settings: Settings,
    healthy_database_check: DatabaseHealthCheck,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app: FastAPI = create_app(
        settings,
        database_health_check=healthy_database_check,
    )

    async def explode() -> None:
        raise RuntimeError("database-password-should-not-leak")

    app.add_api_route("/explode", explode)
    request_id = str(uuid4())
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client,
    ):
        response = await client.get("/explode", headers={"X-Request-ID": request_id})

    assert response.status_code == 500
    assert response.headers["x-request-id"] == request_id
    assert response.json()["request_id"] == request_id
    assert "database-password" not in response.text

    captured = capsys.readouterr().out
    assert "database-password-should-not-leak" not in captured
    events = [json.loads(line) for line in captured.splitlines() if line]
    error_event = next(event for event in events if event["event"] == "unhandled_exception")
    assert error_event["request_id"] == request_id
    assert error_event["exception_type"] == "builtins.RuntimeError"
    assert error_event["traceback"][-1]["function"] == "explode"
    assert "exception" not in error_event
    assert "exc_info" not in error_event

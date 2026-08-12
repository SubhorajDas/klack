"""Database infrastructure unit contracts that do not require PostgreSQL."""

import asyncio
import importlib
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import SQLAlchemyError
from starlette.requests import Request

from klack.api.dependencies import get_session
from klack.core.config import Settings
from klack.core.db.base import NAMING_CONVENTION, Base
from klack.core.db.metadata import target_metadata
from klack.core.db.session import (
    DatabaseUnavailableError,
    create_database_health_check,
    create_engine,
    create_session_factory,
)


class FakeConnection:
    def __init__(self, error: Exception | None = None, gate: asyncio.Event | None = None) -> None:
        self.error = error
        self.gate = gate
        self.executions = 0

    async def __aenter__(self) -> "FakeConnection":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def execute(self, _statement: object) -> None:
        self.executions += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error


class FakeEngine:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def connect(self) -> FakeConnection:
        return self.connection


class FailingEngine:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    def connect(self) -> FakeConnection:
        raise self.error


class FakeSession:
    def __init__(self) -> None:
        self.closed = False
        self.rollbacks = 0

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.closed = True

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeSessionFactory:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def __call__(self) -> FakeSession:
        return self.session


def make_request_with_session(session: FakeSession) -> Request:
    container = SimpleNamespace(session_factory=FakeSessionFactory(session))
    app = SimpleNamespace(state=SimpleNamespace(container=container))
    return Request({"type": "http", "app": app})


async def test_engine_and_session_factory_use_safe_defaults(settings: Settings) -> None:
    engine = create_engine(settings)
    try:
        assert engine.pool.size() == settings.db_pool_size  # type: ignore[union-attr]
        session_factory = create_session_factory(engine)
        async with session_factory() as session:
            assert session.sync_session.autoflush is False
            assert session.sync_session.expire_on_commit is False
    finally:
        await engine.dispose()


def test_metadata_uses_stable_constraint_names() -> None:
    assert Base.metadata.naming_convention == NAMING_CONVENTION
    assert target_metadata is Base.metadata


async def test_request_session_closes_without_implicit_commit() -> None:
    session = FakeSession()
    dependency = get_session(make_request_with_session(session))

    yielded_session = await anext(dependency)
    await dependency.aclose()

    assert yielded_session is session
    assert session.closed is True
    assert session.rollbacks == 0


async def test_request_session_rolls_back_on_handler_error() -> None:
    session = FakeSession()
    dependency = get_session(make_request_with_session(session))
    await anext(dependency)

    with pytest.raises(RuntimeError, match="handler failed"):
        await dependency.athrow(RuntimeError("handler failed"))

    assert session.closed is True
    assert session.rollbacks == 1


async def test_database_health_check_executes_query() -> None:
    connection = FakeConnection()
    checker = create_database_health_check(
        FakeEngine(connection),  # type: ignore[arg-type]
        timeout_seconds=0.1,
    )

    await checker()

    assert connection.executions == 1


async def test_database_health_check_translates_sqlalchemy_errors() -> None:
    checker = create_database_health_check(
        FakeEngine(FakeConnection(error=SQLAlchemyError("private detail"))),  # type: ignore[arg-type]
        timeout_seconds=0.1,
    )

    with pytest.raises(DatabaseUnavailableError) as exc_info:
        await checker()

    assert "private detail" not in str(exc_info.value)


async def test_database_health_check_translates_connection_errors() -> None:
    checker = create_database_health_check(
        FailingEngine(OSError("private dependency detail")),  # type: ignore[arg-type]
        timeout_seconds=0.1,
    )

    with pytest.raises(DatabaseUnavailableError) as exc_info:
        await checker()

    assert "private dependency detail" not in str(exc_info.value)


async def test_database_health_check_does_not_translate_cancellation() -> None:
    checker = create_database_health_check(
        FailingEngine(asyncio.CancelledError()),  # type: ignore[arg-type]
        timeout_seconds=0.1,
    )

    with pytest.raises(asyncio.CancelledError):
        await checker()


async def test_database_health_check_has_total_timeout() -> None:
    checker = create_database_health_check(
        FakeEngine(FakeConnection(gate=asyncio.Event())),  # type: ignore[arg-type]
        timeout_seconds=0.01,
    )

    with pytest.raises(DatabaseUnavailableError):
        await checker()


def test_importing_database_module_does_not_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    monkeypatch.setattr("sqlalchemy.ext.asyncio.create_async_engine", calls.append)

    import klack.core.db.session as session_module

    try:
        importlib.reload(session_module)
        assert calls == []
    finally:
        monkeypatch.undo()
        importlib.reload(session_module)

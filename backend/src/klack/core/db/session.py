"""Async SQLAlchemy engine, session, and health-check construction."""

import asyncio
from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from klack.core.config import Settings

SessionFactory = async_sessionmaker[AsyncSession]
DatabaseHealthCheck = Callable[[], Awaitable[None]]


class DatabaseUnavailableError(RuntimeError):
    """The database did not answer a bounded readiness query."""


def create_engine(settings: Settings) -> AsyncEngine:
    """Create one bounded async engine for the current process."""
    return create_async_engine(
        settings.database_url_value(),
        echo=False,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_recycle=settings.db_pool_recycle_seconds,
        connect_args=settings.database_connect_args(),
    )


def create_session_factory(engine: AsyncEngine) -> SessionFactory:
    """Create sessions that keep loaded state usable after explicit commits."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        autoflush=False,
        expire_on_commit=False,
    )


def create_database_health_check(
    engine: AsyncEngine,
    *,
    timeout_seconds: float,
) -> DatabaseHealthCheck:
    """Return a process-local, time-bounded PostgreSQL readiness check."""

    async def check_database() -> None:
        try:
            async with asyncio.timeout(timeout_seconds):
                async with engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
        except Exception as exc:
            raise DatabaseUnavailableError from exc

    return check_database

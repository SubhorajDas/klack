"""Small typed dependency container built once per process."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from klack.core.config import Settings
from klack.core.db.session import (
    DatabaseHealthCheck,
    SessionFactory,
    create_database_health_check,
    create_engine,
    create_session_factory,
)


@dataclass(frozen=True, slots=True)
class AppContainer:
    """Long-lived technical dependencies shared by application entry points."""

    settings: Settings
    engine: AsyncEngine
    session_factory: SessionFactory
    database_health_check: DatabaseHealthCheck


def build_container(
    settings: Settings,
    *,
    database_health_check: DatabaseHealthCheck | None = None,
) -> AppContainer:
    """Construct process-level infrastructure without opening network connections."""
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    health_check = database_health_check or create_database_health_check(
        engine,
        timeout_seconds=settings.healthcheck_timeout_seconds,
    )
    return AppContainer(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        database_health_check=health_check,
    )

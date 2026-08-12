"""FastAPI dependency adapters for process-level infrastructure."""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from klack.core.db.session import SessionFactory


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield one session per request without implicitly committing business work."""
    session_factory: SessionFactory = request.app.state.container.session_factory
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise

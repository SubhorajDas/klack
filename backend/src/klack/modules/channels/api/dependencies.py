"""FastAPI dependency composition for channel use cases."""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from klack.api.dependencies import get_session
from klack.modules.channels.application.service import ChannelService
from klack.modules.channels.infrastructure.repository import SqlAlchemyChannelRepository
from klack.modules.workspaces.application.service import WorkspaceAccessService
from klack.modules.workspaces.infrastructure.repository import (
    SqlAlchemyWorkspaceRepository,
)


async def get_channel_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChannelService:
    """Compose a request-scoped channel service over the request transaction."""
    container = request.app.state.container
    return ChannelService(
        repository=SqlAlchemyChannelRepository(session),
        workspace_access=WorkspaceAccessService(SqlAlchemyWorkspaceRepository(session)),
        policy=container.channel_policy,
    )


ChannelServiceDependency = Annotated[ChannelService, Depends(get_channel_service)]

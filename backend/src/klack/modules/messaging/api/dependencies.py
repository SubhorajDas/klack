"""FastAPI dependency composition for messaging use cases."""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from klack.api.dependencies import get_session
from klack.modules.channels.application.service import ChannelContentAccessService
from klack.modules.channels.infrastructure.repository import SqlAlchemyChannelRepository
from klack.modules.messaging.application.service import MessageService
from klack.modules.messaging.infrastructure.repository import SqlAlchemyMessageRepository
from klack.modules.workspaces.application.service import WorkspaceAccessService
from klack.modules.workspaces.infrastructure.repository import SqlAlchemyWorkspaceRepository


async def get_message_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MessageService:
    """Compose a request-scoped message service over one shared transaction."""
    container = request.app.state.container
    return MessageService(
        repository=SqlAlchemyMessageRepository(session),
        channel_access=ChannelContentAccessService(
            repository=SqlAlchemyChannelRepository(session),
            workspace_access=WorkspaceAccessService(SqlAlchemyWorkspaceRepository(session)),
        ),
        policy=container.message_policy,
    )


MessageServiceDependency = Annotated[MessageService, Depends(get_message_service)]

"""FastAPI dependency composition for workspace use cases."""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from klack.api.dependencies import get_session
from klack.modules.identity.api.dependencies import EmailVerificationAccessDependency
from klack.modules.workspaces.application.service import WorkspaceService
from klack.modules.workspaces.infrastructure.repository import (
    SqlAlchemyWorkspaceRepository,
)


async def get_workspace_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    email_verification: EmailVerificationAccessDependency,
) -> WorkspaceService:
    """Compose a request-scoped workspace service over the request transaction."""
    container = request.app.state.container
    return WorkspaceService(
        repository=SqlAlchemyWorkspaceRepository(session),
        invitation_tokens=container.workspace_invitation_tokens,
        policy=container.workspace_policy,
        email_verification=email_verification,
    )


WorkspaceServiceDependency = Annotated[WorkspaceService, Depends(get_workspace_service)]

"""FastAPI dependency composition for identity use cases."""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from klack.api.dependencies import get_session
from klack.modules.identity.api.browser_security import (
    csrf_presentation,
    require_exact_origin,
)
from klack.modules.identity.application.service import IdentityService
from klack.modules.identity.domain.entities import AuthenticatedIdentity
from klack.modules.identity.infrastructure.repository import SqlAlchemyIdentityRepository


async def get_identity_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IdentityService:
    """Compose a request-scoped identity service over the request transaction."""
    container = request.app.state.container
    return IdentityService(
        repository=SqlAlchemyIdentityRepository(session),
        passwords=container.password_manager,
        access_tokens=container.access_token_codec,
        session_tokens=container.session_token_manager,
        action_tokens=container.action_token_manager,
        policy=container.identity_policy,
    )


IdentityServiceDependency = Annotated[IdentityService, Depends(get_identity_service)]


async def get_current_identity(
    request: Request,
    service: IdentityServiceDependency,
) -> AuthenticatedIdentity:
    """Resolve the current access cookie and durable session."""
    return await service.authenticate_access(request.cookies.get("klack_access"))


CurrentIdentityDependency = Annotated[
    AuthenticatedIdentity,
    Depends(get_current_identity),
]


async def get_current_mutating_identity(
    request: Request,
    service: IdentityServiceDependency,
    identity: CurrentIdentityDependency,
) -> AuthenticatedIdentity:
    """Authenticate and enforce browser mutation protections for one request."""
    require_exact_origin(request, request.app.state.container.settings)
    service.require_csrf(
        identity=identity,
        csrf_token=csrf_presentation(request),
    )
    return identity


CurrentMutationIdentityDependency = Annotated[
    AuthenticatedIdentity,
    Depends(get_current_mutating_identity),
]

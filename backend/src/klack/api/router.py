"""Root router for versioned application endpoints."""

from fastapi import APIRouter

from klack.modules.calling.router import router as calling_router
from klack.modules.channels.api.router import router as channels_router
from klack.modules.identity.api.router import router as identity_router
from klack.modules.messaging.api.conversations import router as conversations_router
from klack.modules.messaging.api.router import router as messaging_router
from klack.modules.realtime.api.router import router as realtime_router
from klack.modules.workspaces.api.router import router as workspaces_router


def create_api_router(*, prefix: str) -> APIRouter:
    """Create the versioned router composed by future feature modules."""
    router = APIRouter(prefix=prefix)
    router.include_router(identity_router)
    router.include_router(workspaces_router)
    router.include_router(channels_router)
    router.include_router(messaging_router)
    router.include_router(conversations_router)
    router.include_router(realtime_router)
    router.include_router(calling_router)
    return router

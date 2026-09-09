"""Root router for versioned application endpoints."""

from fastapi import APIRouter

from klack.modules.identity.api.router import router as identity_router


def create_api_router(*, prefix: str) -> APIRouter:
    """Create the versioned router composed by future feature modules."""
    router = APIRouter(prefix=prefix)
    router.include_router(identity_router)
    return router

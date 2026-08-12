"""Root router for versioned application endpoints."""

from fastapi import APIRouter


def create_api_router(*, prefix: str) -> APIRouter:
    """Create the versioned router composed by future feature modules."""
    return APIRouter(prefix=prefix)

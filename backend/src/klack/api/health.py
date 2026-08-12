"""Unauthenticated operational health endpoints."""

import structlog
from fastapi import APIRouter, Request, status
from starlette.responses import JSONResponse

from klack.core.db.session import DatabaseUnavailableError

router = APIRouter(prefix="/health", tags=["operations"])
logger = structlog.get_logger(__name__)

NO_STORE_HEADERS = {"Cache-Control": "no-store"}


@router.get("/live", include_in_schema=False)
async def liveness() -> JSONResponse:
    """Confirm that the process and event loop can answer requests."""
    return JSONResponse(content={"status": "ok"}, headers=NO_STORE_HEADERS)


@router.get("/ready", include_in_schema=False)
async def readiness(request: Request) -> JSONResponse:
    """Confirm that PostgreSQL can serve application traffic within a short deadline."""
    try:
        await request.app.state.container.database_health_check()
    except DatabaseUnavailableError:
        logger.warning("database_readiness_failed")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unavailable"},
            headers=NO_STORE_HEADERS,
        )

    return JSONResponse(content={"status": "ok"}, headers=NO_STORE_HEADERS)

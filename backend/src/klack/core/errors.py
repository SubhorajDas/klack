"""Safe boundary handling for unexpected application errors."""

from pathlib import Path
from traceback import extract_tb
from typing import Final, TypedDict
from uuid import uuid4

import structlog
from fastapi import Request
from starlette.responses import JSONResponse

REQUEST_ID_RESPONSE_HEADER: Final[str] = "X-Request-ID"

logger = structlog.get_logger(__name__)


class SafeFrame(TypedDict):
    """Non-sensitive traceback location metadata."""

    file: str
    line: int
    function: str


def _safe_traceback(exc: Exception) -> list[SafeFrame]:
    """Describe stack locations without exception messages, source text, or local values."""
    return [
        {
            "file": Path(frame.filename).name,
            "line": frame.lineno or 0,
            "function": frame.name,
        }
        for frame in extract_tb(exc.__traceback__)
    ]


def _exception_type(exc: Exception) -> str:
    """Identify an exception without invoking its potentially sensitive string rendering."""
    exception_class = type(exc)
    return f"{exception_class.__module__}.{exception_class.__qualname__}"


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return a non-sensitive problem response and correlate the server-side traceback."""
    request_id = getattr(request.state, "request_id", str(uuid4()))
    logger.error(
        "unhandled_exception",
        request_id=request_id,
        exception_type=_exception_type(exc),
        traceback=_safe_traceback(exc),
    )

    return JSONResponse(
        status_code=500,
        media_type="application/problem+json",
        headers={REQUEST_ID_RESPONSE_HEADER: request_id},
        content={
            "type": "about:blank",
            "title": "Internal Server Error",
            "status": 500,
            "detail": "An unexpected error occurred.",
            "code": "internal_error",
            "request_id": request_id,
        },
    )

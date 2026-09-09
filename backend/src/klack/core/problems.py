"""RFC 9457-style problem response helpers for expected API failures."""

from collections import defaultdict
from http import HTTPStatus
from typing import Any
from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse

from klack.core.errors import REQUEST_ID_RESPONSE_HEADER


def problem_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    detail: str,
    field_errors: dict[str, list[str]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Create a correlated, non-cacheable Problem Details response."""
    request_id = getattr(request.state, "request_id", str(uuid4()))
    content: dict[str, Any] = {
        "type": "about:blank",
        "title": HTTPStatus(status_code).phrase,
        "status": status_code,
        "detail": detail,
        "code": code,
        "request_id": request_id,
    }
    if field_errors:
        content["field_errors"] = field_errors
    response_headers = {
        "Cache-Control": "no-store",
        REQUEST_ID_RESPONSE_HEADER: request_id,
    }
    if headers:
        response_headers.update(headers)
    return JSONResponse(
        status_code=status_code,
        media_type="application/problem+json",
        headers=response_headers,
        content=content,
    )


async def request_validation_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Translate request validation into stable, input-safe field errors."""
    if not isinstance(exc, RequestValidationError):
        raise exc
    field_errors: defaultdict[str, list[str]] = defaultdict(list)
    for error in exc.errors():
        location = tuple(str(part) for part in error.get("loc", ()) if part != "body")
        field = ".".join(location) or "request"
        message = str(error.get("msg", "Invalid value."))
        field_errors[field].append(message)
    return problem_response(
        request,
        status_code=422,
        code="validation_error",
        detail="The request contains invalid fields.",
        field_errors=dict(field_errors),
    )

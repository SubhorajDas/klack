"""Problem Details mappings for expected identity failures."""

from dataclasses import dataclass

from fastapi import Request
from starlette.responses import JSONResponse

from klack.core.problems import problem_response
from klack.modules.identity.api.browser_security import clear_auth_cookies
from klack.modules.identity.domain.errors import (
    AuthenticationRateLimited,
    AuthenticationRequired,
    CsrfValidationFailed,
    EmailAlreadyRegistered,
    IdentityError,
    InvalidCredentials,
    InvalidEmailActionToken,
    InvalidEmailAddress,
    InvalidPassword,
    OriginNotAllowed,
    RefreshRateLimited,
    RefreshTokenReuseDetected,
    SessionExpired,
    SessionNotFound,
)


@dataclass(frozen=True, slots=True)
class ErrorContract:
    status: int
    code: str
    detail: str


ERROR_CONTRACTS: dict[type[IdentityError], ErrorContract] = {
    InvalidEmailAddress: ErrorContract(422, "validation_error", "The email address is invalid."),
    InvalidPassword: ErrorContract(422, "validation_error", "The password is invalid."),
    EmailAlreadyRegistered: ErrorContract(
        409,
        "email_already_registered",
        "An account already uses this email address.",
    ),
    InvalidCredentials: ErrorContract(401, "invalid_credentials", "The credentials are invalid."),
    AuthenticationRequired: ErrorContract(
        401,
        "authentication_required",
        "Authentication is required.",
    ),
    SessionExpired: ErrorContract(401, "session_expired", "The session is no longer valid."),
    RefreshTokenReuseDetected: ErrorContract(
        401,
        "refresh_reuse_detected",
        "The session was revoked because a refresh token was reused.",
    ),
    RefreshRateLimited: ErrorContract(
        429,
        "refresh_rate_limited",
        "The session was refreshed too recently.",
    ),
    AuthenticationRateLimited: ErrorContract(
        429,
        "authentication_rate_limited",
        "Too many authentication attempts were made.",
    ),
    InvalidEmailActionToken: ErrorContract(
        400,
        "invalid_email_action_token",
        "The email action is invalid or expired.",
    ),
    CsrfValidationFailed: ErrorContract(
        403,
        "csrf_validation_failed",
        "The CSRF validation failed.",
    ),
    OriginNotAllowed: ErrorContract(
        403,
        "origin_not_allowed",
        "The request origin is not allowed.",
    ),
    SessionNotFound: ErrorContract(404, "session_not_found", "The session was not found."),
}


async def identity_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return the stable public contract for one expected identity failure."""
    if not isinstance(exc, IdentityError):
        raise exc
    contract = ERROR_CONTRACTS.get(type(exc))
    if contract is None:
        contract = ErrorContract(400, "identity_error", "The identity operation failed.")
    response = problem_response(
        request,
        status_code=contract.status,
        code=contract.code,
        detail=contract.detail,
    )
    if isinstance(exc, (SessionExpired, RefreshTokenReuseDetected)):
        clear_auth_cookies(response, request.app.state.container.settings)
    if isinstance(exc, (RefreshRateLimited, AuthenticationRateLimited)):
        response.headers["Retry-After"] = str(exc.retry_after_seconds)
    return response

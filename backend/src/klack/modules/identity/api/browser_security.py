"""Same-origin, CSRF, cookie, and client-metadata browser adapters."""

import hmac
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Final

from fastapi import Request, Response

from klack.core.config import Settings
from klack.modules.identity.application.service import AuthenticationResult
from klack.modules.identity.domain.entities import SessionMetadata
from klack.modules.identity.domain.errors import CsrfValidationFailed, OriginNotAllowed

ACCESS_COOKIE: Final[str] = "klack_access"
REFRESH_COOKIE: Final[str] = "klack_refresh"
CSRF_COOKIE: Final[str] = "klack_csrf"
CSRF_HEADER: Final[str] = "X-CSRF-Token"


def require_exact_origin(request: Request, settings: Settings) -> None:
    """Require one exact configured Origin on every unsafe identity request."""
    origins = request.headers.getlist("origin")
    if len(origins) != 1 or origins[0] != settings.auth_trusted_origin:
        raise OriginNotAllowed


def csrf_presentation(request: Request, *, required: bool = True) -> str | None:
    """Validate double-submit equality before the session digest is checked."""
    header = request.headers.get(CSRF_HEADER)
    cookie = request.cookies.get(CSRF_COOKIE)
    valid = False
    if header is not None and cookie is not None:
        try:
            header_bytes = header.encode("ascii")
            cookie_bytes = cookie.encode("ascii")
        except UnicodeEncodeError:
            pass
        else:
            valid = (
                len(header_bytes) <= 256
                and len(cookie_bytes) <= 256
                and hmac.compare_digest(header_bytes, cookie_bytes)
            )
    if not valid:
        if required:
            raise CsrfValidationFailed
        return None
    return header


def session_metadata(request: Request) -> SessionMetadata:
    """Capture bounded direct-peer and user-agent hints without treating them as identity."""
    client_host = request.client.host if request.client is not None else None
    normalized_ip: str | None = None
    if client_host is not None:
        try:
            normalized_ip = str(ip_address(client_host))
        except ValueError:
            normalized_ip = None
    user_agent = request.headers.get("user-agent")
    if user_agent is not None:
        user_agent = user_agent.strip()[:512] or None
    return SessionMetadata(ip_address=normalized_ip, user_agent=user_agent)


def set_auth_cookies(
    response: Response,
    result: AuthenticationResult,
    settings: Settings,
) -> None:
    """Write host-only browser credentials with explicit paths and expiry bounds."""
    now = datetime.now(UTC)
    access_max_age = max(0, int((result.access_expires_at - now).total_seconds()))
    refresh_max_age = max(0, int((result.refresh_expires_at - now).total_seconds()))
    response.set_cookie(
        ACCESS_COOKIE,
        result.access_token,
        max_age=access_max_age,
        expires=result.access_expires_at,
        path=settings.api_v1_prefix,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        result.refresh_token,
        max_age=refresh_max_age,
        expires=result.refresh_expires_at,
        path=f"{settings.api_v1_prefix}/auth",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        CSRF_COOKIE,
        result.csrf_token,
        max_age=refresh_max_age,
        expires=result.refresh_expires_at,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=False,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    """Expire every authentication cookie using the same attributes used at issuance."""
    response.delete_cookie(
        ACCESS_COOKIE,
        path=settings.api_v1_prefix,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        REFRESH_COOKIE,
        path=f"{settings.api_v1_prefix}/auth",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        CSRF_COOKIE,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=False,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"

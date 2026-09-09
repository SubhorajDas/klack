"""Versioned registration, authentication, and session endpoints."""

from uuid import UUID

from fastapi import APIRouter, Request, Response, status

from klack.modules.identity.api.browser_security import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    clear_auth_cookies,
    csrf_presentation,
    require_exact_origin,
    session_metadata,
    set_auth_cookies,
)
from klack.modules.identity.api.dependencies import (
    CurrentIdentityDependency,
    IdentityServiceDependency,
)
from klack.modules.identity.api.schemas import (
    AuthenticationResponse,
    EmailVerificationCompleteRequest,
    LoginRequest,
    PasswordRecoveryCompleteRequest,
    PasswordRecoveryRequest,
    RegisterRequest,
    SessionResponse,
    SessionsResponse,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post(
    "/register",
    response_model=AuthenticationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    service: IdentityServiceDependency,
) -> AuthenticationResponse:
    """Create an identity and sign the browser into its first session."""
    settings = request.app.state.container.settings
    require_exact_origin(request, settings)
    result = await service.register(
        email=payload.email,
        password=payload.password.get_secret_value(),
        metadata=session_metadata(request),
    )
    set_auth_cookies(response, result, settings)
    return AuthenticationResponse.from_result(result)


@router.post("/login", response_model=AuthenticationResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    service: IdentityServiceDependency,
) -> AuthenticationResponse:
    """Create an independent browser session for valid credentials."""
    settings = request.app.state.container.settings
    require_exact_origin(request, settings)
    result = await service.login(
        email=payload.email,
        password=payload.password.get_secret_value(),
        metadata=session_metadata(request),
    )
    set_auth_cookies(response, result, settings)
    return AuthenticationResponse.from_result(result)


@router.post(
    "/email-verification/request",
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_email_verification(
    request: Request,
    service: IdentityServiceDependency,
    identity: CurrentIdentityDependency,
) -> Response:
    """Queue a replacement verification email for the current identity."""
    settings = request.app.state.container.settings
    require_exact_origin(request, settings)
    await service.request_email_verification(
        identity=identity,
        csrf_token=csrf_presentation(request),
        metadata=session_metadata(request),
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post(
    "/email-verification/complete",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def complete_email_verification(
    payload: EmailVerificationCompleteRequest,
    request: Request,
    service: IdentityServiceDependency,
) -> Response:
    """Mark a mailbox verified through a single-use emailed token."""
    require_exact_origin(request, request.app.state.container.settings)
    await service.complete_email_verification(
        raw_token=payload.token.get_secret_value(),
        metadata=session_metadata(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/password-recovery/request",
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_password_recovery(
    payload: PasswordRecoveryRequest,
    request: Request,
    service: IdentityServiceDependency,
) -> Response:
    """Queue a generic recovery email without exposing account existence."""
    require_exact_origin(request, request.app.state.container.settings)
    await service.request_password_recovery(
        email=payload.email,
        metadata=session_metadata(request),
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post(
    "/password-recovery/complete",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def complete_password_recovery(
    payload: PasswordRecoveryCompleteRequest,
    request: Request,
    service: IdentityServiceDependency,
) -> Response:
    """Replace a password through a single-use recovery token."""
    settings = request.app.state.container.settings
    require_exact_origin(request, settings)
    await service.complete_password_recovery(
        raw_token=payload.token.get_secret_value(),
        new_password=payload.new_password.get_secret_value(),
        metadata=session_metadata(request),
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookies(response, settings)
    return response


@router.post("/refresh", response_model=AuthenticationResponse)
async def refresh(
    request: Request,
    response: Response,
    service: IdentityServiceDependency,
) -> AuthenticationResponse:
    """Rotate the current opaque refresh token and replace both auth cookies."""
    settings = request.app.state.container.settings
    require_exact_origin(request, settings)
    result = await service.refresh(
        raw_refresh_token=request.cookies.get(REFRESH_COOKIE),
        csrf_token=csrf_presentation(request, required=False),
        metadata=session_metadata(request),
    )
    set_auth_cookies(response, result, settings)
    return AuthenticationResponse.from_result(result)


@router.get("/me", response_model=UserResponse)
async def current_user(
    response: Response,
    identity: CurrentIdentityDependency,
) -> UserResponse:
    """Return the user bound to the current active access session."""
    response.headers["Cache-Control"] = "no-store"
    return UserResponse.from_domain(identity.user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    service: IdentityServiceDependency,
) -> Response:
    """Idempotently revoke and clear the current browser session."""
    settings = request.app.state.container.settings
    require_exact_origin(request, settings)
    csrf_token = csrf_presentation(request, required=False)
    await service.logout(
        raw_access_token=request.cookies.get(ACCESS_COOKIE),
        raw_refresh_token=request.cookies.get(REFRESH_COOKIE),
        csrf_token=csrf_token,
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookies(response, settings)
    return response


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    request: Request,
    service: IdentityServiceDependency,
    identity: CurrentIdentityDependency,
) -> Response:
    """Revoke every active session owned by the current user."""
    settings = request.app.state.container.settings
    require_exact_origin(request, settings)
    await service.logout_all(
        identity=identity,
        csrf_token=csrf_presentation(request),
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookies(response, settings)
    return response


@router.get("/sessions", response_model=SessionsResponse)
async def list_sessions(
    response: Response,
    service: IdentityServiceDependency,
    identity: CurrentIdentityDependency,
) -> SessionsResponse:
    """List independently revocable active browser sessions."""
    sessions = await service.list_sessions(identity)
    response.headers["Cache-Control"] = "no-store"
    return SessionsResponse(
        sessions=[
            SessionResponse.from_domain(
                session,
                current_session_id=identity.session.id,
            )
            for session in sessions
        ],
    )


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session_id: UUID,
    request: Request,
    service: IdentityServiceDependency,
    identity: CurrentIdentityDependency,
) -> Response:
    """Revoke one session owned by the current user."""
    settings = request.app.state.container.settings
    require_exact_origin(request, settings)
    revoked_current = await service.revoke_session(
        identity=identity,
        session_id=session_id,
        csrf_token=csrf_presentation(request),
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.headers["Cache-Control"] = "no-store"
    if revoked_current:
        clear_auth_cookies(response, settings)
    return response

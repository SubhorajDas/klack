"""HTTP contracts for registration, authentication cookies, CSRF, and sessions."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from klack.modules.identity.api.dependencies import get_identity_service
from klack.modules.identity.application.service import AuthenticationResult
from klack.modules.identity.domain.entities import AuthenticatedIdentity, AuthSession, User
from klack.modules.identity.domain.errors import (
    AuthenticationRateLimited,
    AuthenticationRequired,
    CsrfValidationFailed,
    InvalidCredentials,
    InvalidEmailActionToken,
    RefreshRateLimited,
    RefreshTokenReuseDetected,
    SessionExpired,
)

NOW = datetime.now(UTC)
USER_ID = UUID("11111111-1111-4111-8111-111111111111")
CURRENT_SESSION_ID = UUID("22222222-2222-4222-8222-222222222222")
OTHER_SESSION_ID = UUID("33333333-3333-4333-8333-333333333333")
ORIGIN_HEADERS = {"Origin": "http://test"}


def make_user() -> User:
    return User(
        id=USER_ID,
        email="person@example.com",
        email_verified_at=None,
        created_at=NOW,
        disabled_at=None,
    )


def make_session(session_id: UUID = CURRENT_SESSION_ID) -> AuthSession:
    return AuthSession(
        id=session_id,
        user_id=USER_ID,
        csrf_token_hash="stored-hash",
        created_at=NOW,
        last_seen_at=NOW,
        expires_at=NOW + timedelta(days=30),
        revoked_at=None,
        revocation_reason=None,
        created_ip="192.0.2.1",
        last_ip="192.0.2.1",
        user_agent="Test Browser",
    )


def make_result() -> AuthenticationResult:
    identity = AuthenticatedIdentity(user=make_user(), session=make_session())
    return AuthenticationResult(
        identity=identity,
        access_token="valid-access",
        access_expires_at=NOW + timedelta(minutes=15),
        refresh_token=f"{uuid4()}.{'r' * 43}",
        refresh_expires_at=NOW + timedelta(days=30),
        csrf_token="csrf-value",
    )


class FakeIdentityService:
    def __init__(self) -> None:
        self.result = make_result()
        self.register_calls: list[dict[str, object]] = []
        self.login_calls: list[dict[str, object]] = []
        self.refresh_calls: list[dict[str, object]] = []
        self.logout_calls: list[dict[str, object]] = []
        self.logout_all_calls = 0
        self.revoke_calls: list[UUID] = []
        self.verification_request_calls: list[dict[str, object]] = []
        self.verification_complete_calls: list[dict[str, object]] = []
        self.recovery_request_calls: list[dict[str, object]] = []
        self.recovery_complete_calls: list[dict[str, object]] = []
        self.login_error: Exception | None = None
        self.refresh_error: Exception | None = None
        self.action_error: Exception | None = None

    async def register(self, **kwargs: object) -> AuthenticationResult:
        self.register_calls.append(kwargs)
        return self.result

    async def login(self, **kwargs: object) -> AuthenticationResult:
        self.login_calls.append(kwargs)
        if self.login_error is not None:
            raise self.login_error
        return self.result

    async def request_email_verification(self, **kwargs: object) -> None:
        self.verification_request_calls.append(kwargs)

    async def complete_email_verification(self, **kwargs: object) -> None:
        self.verification_complete_calls.append(kwargs)
        if self.action_error is not None:
            raise self.action_error

    async def request_password_recovery(self, **kwargs: object) -> None:
        self.recovery_request_calls.append(kwargs)

    async def complete_password_recovery(self, **kwargs: object) -> None:
        self.recovery_complete_calls.append(kwargs)
        if self.action_error is not None:
            raise self.action_error

    async def refresh(self, **kwargs: object) -> AuthenticationResult:
        self.refresh_calls.append(kwargs)
        if self.refresh_error is not None:
            raise self.refresh_error
        if kwargs.get("csrf_token") != "csrf-value":
            raise CsrfValidationFailed
        return self.result

    async def authenticate_access(self, raw_access_token: str | None) -> AuthenticatedIdentity:
        if raw_access_token != "valid-access":
            raise AuthenticationRequired
        return self.result.identity

    async def logout(self, **kwargs: object) -> None:
        self.logout_calls.append(kwargs)

    async def logout_all(self, **_kwargs: object) -> None:
        self.logout_all_calls += 1

    async def list_sessions(self, _identity: AuthenticatedIdentity) -> list[AuthSession]:
        return [make_session(), make_session(OTHER_SESSION_ID)]

    async def revoke_session(self, *, session_id: UUID, **_kwargs: object) -> bool:
        self.revoke_calls.append(session_id)
        return session_id == CURRENT_SESSION_ID


@dataclass(frozen=True)
class AuthApiHarness:
    client: AsyncClient
    service: FakeIdentityService


@pytest.fixture
def auth_api(app: FastAPI, client: AsyncClient) -> AuthApiHarness:
    service = FakeIdentityService()
    app.dependency_overrides[get_identity_service] = lambda: service
    return AuthApiHarness(client=client, service=service)


async def register_browser(harness: AuthApiHarness):
    return await harness.client.post(
        "/api/v1/auth/register",
        headers={**ORIGIN_HEADERS, "User-Agent": "Browser/1.0"},
        json={"email": "Person@Example.com", "password": "a secure passphrase"},
    )


async def test_registration_sets_safe_host_only_cookies_and_returns_no_tokens(
    auth_api: AuthApiHarness,
) -> None:
    response = await register_browser(auth_api)

    assert response.status_code == 201
    assert response.json() == {
        "user": {
            "id": str(USER_ID),
            "email": "person@example.com",
            "email_verified": False,
            "created_at": NOW.isoformat().replace("+00:00", "Z"),
        },
        "session": {
            "id": str(CURRENT_SESSION_ID),
            "current": True,
            "created_at": NOW.isoformat().replace("+00:00", "Z"),
            "last_seen_at": NOW.isoformat().replace("+00:00", "Z"),
            "expires_at": (NOW + timedelta(days=30)).isoformat().replace("+00:00", "Z"),
            "user_agent": "Test Browser",
        },
    }
    assert "token" not in response.text
    cookies = response.headers.get_list("set-cookie")
    access_cookie = next(item for item in cookies if item.startswith("klack_access="))
    refresh_cookie = next(item for item in cookies if item.startswith("klack_refresh="))
    csrf_cookie = next(item for item in cookies if item.startswith("klack_csrf="))
    assert "HttpOnly" in access_cookie and "Path=/api/v1" in access_cookie
    assert "HttpOnly" in refresh_cookie and "Path=/api/v1/auth" in refresh_cookie
    assert "HttpOnly" not in csrf_cookie and "Path=/" in csrf_cookie
    assert all("SameSite=lax" in item for item in cookies)
    assert all("Domain=" not in item for item in cookies)
    assert response.headers["cache-control"] == "no-store"
    metadata = auth_api.service.register_calls[0]["metadata"]
    assert metadata.user_agent == "Browser/1.0"  # type: ignore[union-attr]


async def test_unsafe_auth_routes_require_exact_origin(auth_api: AuthApiHarness) -> None:
    response = await auth_api.client.post(
        "/api/v1/auth/login",
        json={"email": "person@example.com", "password": "password"},
    )

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "origin_not_allowed"
    assert response.json()["request_id"] == response.headers["x-request-id"]
    assert auth_api.service.login_calls == []


async def test_validation_errors_are_problem_details_and_hide_password(
    auth_api: AuthApiHarness,
) -> None:
    response = await auth_api.client.post(
        "/api/v1/auth/register",
        headers=ORIGIN_HEADERS,
        json={"email": "bad", "password": "tiny", "unexpected": "secret-value"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert set(response.json()["field_errors"]) == {"email", "password", "unexpected"}
    assert "secret-value" not in response.text
    assert "tiny" not in response.text


async def test_login_returns_generic_credential_problem(auth_api: AuthApiHarness) -> None:
    auth_api.service.login_error = InvalidCredentials()
    response = await auth_api.client.post(
        "/api/v1/auth/login",
        headers=ORIGIN_HEADERS,
        json={"email": "missing@example.com", "password": "wrong"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"
    assert response.json()["detail"] == "The credentials are invalid."
    assert response.headers["cache-control"] == "no-store"


async def test_refresh_requires_double_submit_and_clears_expired_session(
    auth_api: AuthApiHarness,
) -> None:
    assert (await register_browser(auth_api)).status_code == 201

    mismatch = await auth_api.client.post(
        "/api/v1/auth/refresh",
        headers={**ORIGIN_HEADERS, "X-CSRF-Token": "wrong"},
    )
    assert mismatch.status_code == 403
    assert mismatch.json()["code"] == "csrf_validation_failed"
    assert auth_api.service.refresh_calls[0]["csrf_token"] is None

    refreshed = await auth_api.client.post(
        "/api/v1/auth/refresh",
        headers={**ORIGIN_HEADERS, "X-CSRF-Token": "csrf-value"},
    )
    assert refreshed.status_code == 200
    assert auth_api.service.refresh_calls[1]["csrf_token"] == "csrf-value"

    auth_api.service.refresh_error = RefreshRateLimited(37)
    limited = await auth_api.client.post(
        "/api/v1/auth/refresh",
        headers={**ORIGIN_HEADERS, "X-CSRF-Token": "csrf-value"},
    )
    assert limited.status_code == 429
    assert limited.json()["code"] == "refresh_rate_limited"
    assert limited.headers["retry-after"] == "37"

    auth_api.service.refresh_error = RefreshTokenReuseDetected()
    replayed = await auth_api.client.post(
        "/api/v1/auth/refresh",
        headers=ORIGIN_HEADERS,
    )
    assert replayed.status_code == 401
    assert replayed.json()["code"] == "refresh_reuse_detected"

    auth_api.service.refresh_error = SessionExpired()
    expired = await auth_api.client.post(
        "/api/v1/auth/refresh",
        headers={**ORIGIN_HEADERS, "X-CSRF-Token": "csrf-value"},
    )
    assert expired.status_code == 401
    assert expired.json()["code"] == "session_expired"
    assert any("Max-Age=0" in item for item in expired.headers.get_list("set-cookie"))


async def test_me_and_session_listing_use_access_cookie(auth_api: AuthApiHarness) -> None:
    await register_browser(auth_api)

    me = await auth_api.client.get("/api/v1/auth/me")
    sessions = await auth_api.client.get("/api/v1/auth/sessions")

    assert me.status_code == 200
    assert me.json()["email"] == "person@example.com"
    assert me.headers["cache-control"] == "no-store"
    assert sessions.status_code == 200
    assert [item["current"] for item in sessions.json()["sessions"]] == [True, False]
    assert sessions.headers["cache-control"] == "no-store"


async def test_missing_access_cookie_returns_authentication_problem(
    auth_api: AuthApiHarness,
) -> None:
    response = await auth_api.client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


async def test_logout_is_idempotent_and_clears_cookies(auth_api: AuthApiHarness) -> None:
    await register_browser(auth_api)
    response = await auth_api.client.post(
        "/api/v1/auth/logout",
        headers={**ORIGIN_HEADERS, "X-CSRF-Token": "csrf-value"},
    )

    assert response.status_code == 204
    assert auth_api.service.logout_calls[0]["raw_access_token"] == "valid-access"
    assert len(response.headers.get_list("set-cookie")) == 3
    assert all("Max-Age=0" in item for item in response.headers.get_list("set-cookie"))

    again = await auth_api.client.post("/api/v1/auth/logout", headers=ORIGIN_HEADERS)
    assert again.status_code == 204
    assert auth_api.service.logout_calls[-1]["raw_access_token"] is None


async def test_logout_all_and_session_revocation_clear_current_login(
    auth_api: AuthApiHarness,
) -> None:
    await register_browser(auth_api)
    all_response = await auth_api.client.post(
        "/api/v1/auth/logout-all",
        headers={**ORIGIN_HEADERS, "X-CSRF-Token": "csrf-value"},
    )
    assert all_response.status_code == 204
    assert auth_api.service.logout_all_calls == 1
    assert len(all_response.headers.get_list("set-cookie")) == 3

    await register_browser(auth_api)
    other = await auth_api.client.delete(
        f"/api/v1/auth/sessions/{OTHER_SESSION_ID}",
        headers={**ORIGIN_HEADERS, "X-CSRF-Token": "csrf-value"},
    )
    assert other.status_code == 204
    assert other.headers.get_list("set-cookie") == []

    current = await auth_api.client.delete(
        f"/api/v1/auth/sessions/{CURRENT_SESSION_ID}",
        headers={**ORIGIN_HEADERS, "X-CSRF-Token": "csrf-value"},
    )
    assert current.status_code == 204
    assert auth_api.service.revoke_calls == [OTHER_SESSION_ID, CURRENT_SESSION_ID]
    assert len(current.headers.get_list("set-cookie")) == 3


async def test_email_verification_request_and_complete_contracts(
    auth_api: AuthApiHarness,
) -> None:
    await register_browser(auth_api)
    requested = await auth_api.client.post(
        "/api/v1/auth/email-verification/request",
        headers={**ORIGIN_HEADERS, "X-CSRF-Token": "csrf-value"},
    )
    assert requested.status_code == 202
    assert requested.content == b""
    assert auth_api.service.verification_request_calls[0]["csrf_token"] == "csrf-value"

    completed = await auth_api.client.post(
        "/api/v1/auth/email-verification/complete",
        headers=ORIGIN_HEADERS,
        json={"token": f"{uuid4()}.{'v' * 43}"},
    )
    assert completed.status_code == 204
    assert "token" not in completed.text
    assert auth_api.service.verification_complete_calls[0]["raw_token"].endswith(
        "v" * 43,
    )

    auth_api.service.action_error = InvalidEmailActionToken()
    invalid = await auth_api.client.post(
        "/api/v1/auth/email-verification/complete",
        headers=ORIGIN_HEADERS,
        json={"token": f"{uuid4()}.{'x' * 43}"},
    )
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "invalid_email_action_token"


async def test_password_recovery_is_generic_and_completion_clears_auth_cookies(
    auth_api: AuthApiHarness,
) -> None:
    requested = await auth_api.client.post(
        "/api/v1/auth/password-recovery/request",
        headers=ORIGIN_HEADERS,
        json={"email": "missing@example.com"},
    )
    assert requested.status_code == 202
    assert requested.content == b""
    assert auth_api.service.recovery_request_calls[0]["email"] == "missing@example.com"

    await register_browser(auth_api)
    completed = await auth_api.client.post(
        "/api/v1/auth/password-recovery/complete",
        headers=ORIGIN_HEADERS,
        json={
            "token": f"{uuid4()}.{'r' * 43}",
            "new_password": "a new secure password",
        },
    )
    assert completed.status_code == 204
    assert auth_api.service.recovery_complete_calls[0]["new_password"] == ("a new secure password")
    assert len(completed.headers.get_list("set-cookie")) == 3
    assert all("Max-Age=0" in item for item in completed.headers.get_list("set-cookie"))


async def test_shared_authentication_rate_limit_has_retry_contract(
    auth_api: AuthApiHarness,
) -> None:
    auth_api.service.login_error = AuthenticationRateLimited(42)
    response = await auth_api.client.post(
        "/api/v1/auth/login",
        headers=ORIGIN_HEADERS,
        json={"email": "person@example.com", "password": "wrong password"},
    )
    assert response.status_code == 429
    assert response.json()["code"] == "authentication_rate_limited"
    assert response.headers["retry-after"] == "42"

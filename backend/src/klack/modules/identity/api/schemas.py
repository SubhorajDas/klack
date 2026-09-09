"""Public request and response contracts for identity endpoints."""

from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from klack.modules.identity.application.service import AuthenticationResult
from klack.modules.identity.domain.email import normalize_email
from klack.modules.identity.domain.entities import AuthSession, User

PasswordInput = Annotated[SecretStr, Field(min_length=12, max_length=128)]
EmailInput = Annotated[str, Field(min_length=3, max_length=320)]


class StrictRequest(BaseModel):
    """Reject unexpected input fields on security-sensitive requests."""

    model_config = ConfigDict(extra="forbid")


class RegisterRequest(StrictRequest):
    """Create a password identity and initial authenticated session."""

    email: EmailInput
    password: PasswordInput

    @field_validator("email")
    @classmethod
    def normalize_email_value(cls, value: str) -> str:
        return normalize_email(value)


class LoginRequest(StrictRequest):
    """Authenticate one email/password pair."""

    email: EmailInput
    password: Annotated[SecretStr, Field(min_length=1, max_length=128)]


class EmailVerificationCompleteRequest(StrictRequest):
    """Consume one emailed verification token."""

    token: Annotated[SecretStr, Field(min_length=1, max_length=256)]


class PasswordRecoveryRequest(StrictRequest):
    """Request a generic password-recovery email."""

    email: EmailInput


class PasswordRecoveryCompleteRequest(StrictRequest):
    """Consume one recovery token and replace the password."""

    token: Annotated[SecretStr, Field(min_length=1, max_length=256)]
    new_password: PasswordInput


class UserResponse(BaseModel):
    """Public current-user fields; credential data is never represented."""

    id: UUID
    email: str
    email_verified: bool
    created_at: datetime

    @classmethod
    def from_domain(cls, user: User) -> Self:
        return cls(
            id=user.id,
            email=user.email,
            email_verified=user.email_verified,
            created_at=user.created_at,
        )


class SessionResponse(BaseModel):
    """Device/session metadata visible only to its owner."""

    id: UUID
    current: bool
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    user_agent: str | None

    @classmethod
    def from_domain(cls, session: AuthSession, *, current_session_id: UUID) -> Self:
        return cls(
            id=session.id,
            current=session.id == current_session_id,
            created_at=session.created_at,
            last_seen_at=session.last_seen_at,
            expires_at=session.expires_at,
            user_agent=session.user_agent,
        )


class AuthenticationResponse(BaseModel):
    """Identity/session state returned after registration, login, or refresh."""

    user: UserResponse
    session: SessionResponse

    @classmethod
    def from_result(cls, result: AuthenticationResult) -> Self:
        return cls(
            user=UserResponse.from_domain(result.identity.user),
            session=SessionResponse.from_domain(
                result.identity.session,
                current_session_id=result.identity.session.id,
            ),
        )


class SessionsResponse(BaseModel):
    """Active sessions owned by the current identity."""

    sessions: list[SessionResponse]

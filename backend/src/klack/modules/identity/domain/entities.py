"""Persistence-agnostic identity and session values."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class EmailActionPurpose(StrEnum):
    """Single-use account actions delivered to a verified mailbox."""

    VERIFY_EMAIL = "verify_email"
    RECOVER_PASSWORD = "recover_password"


@dataclass(frozen=True, slots=True)
class User:
    """A registered Klack identity."""

    id: UUID
    email: str
    email_verified_at: datetime | None
    created_at: datetime
    disabled_at: datetime | None

    @property
    def email_verified(self) -> bool:
        """Whether the address has completed verification."""
        return self.email_verified_at is not None


@dataclass(frozen=True, slots=True)
class PasswordCredential:
    """The password credential owned by one user."""

    user_id: UUID
    password_hash: str
    password_changed_at: datetime


@dataclass(frozen=True, slots=True)
class AuthSession:
    """One independently revocable browser login and refresh-token family."""

    id: UUID
    user_id: UUID
    csrf_token_hash: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    revocation_reason: str | None
    created_ip: str | None
    last_ip: str | None
    user_agent: str | None

    def is_active(self, now: datetime) -> bool:
        """Return whether the session can still authenticate at the given time."""
        return self.revoked_at is None and self.expires_at > now


@dataclass(frozen=True, slots=True)
class RefreshToken:
    """Stored metadata for one opaque token in a rotation history."""

    id: UUID
    session_id: UUID
    token_hash: str
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None
    revoked_at: datetime | None
    replaced_by_token_id: UUID | None


@dataclass(frozen=True, slots=True)
class EmailActionToken:
    """Stored HMAC-only state for one email verification or recovery action."""

    id: UUID
    user_id: UUID
    purpose: EmailActionPurpose
    token_hash: str
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None
    revoked_at: datetime | None

    def is_active(self, now: datetime) -> bool:
        """Return whether this action can be consumed."""
        return self.used_at is None and self.revoked_at is None and self.expires_at > now


@dataclass(frozen=True, slots=True)
class EmailOutboxMessage:
    """An encrypted email action queued atomically with its token digest."""

    id: UUID
    action_token_id: UUID
    recipient: str
    purpose: EmailActionPurpose
    encrypted_payload: str
    created_at: datetime
    available_at: datetime


@dataclass(frozen=True, slots=True)
class LoginRecord:
    """A user and their password credential loaded for login."""

    user: User
    credential: PasswordCredential


@dataclass(frozen=True, slots=True)
class AuthenticatedIdentity:
    """An active user/session pair loaded from durable state."""

    user: User
    session: AuthSession


@dataclass(frozen=True, slots=True)
class RefreshContext:
    """The durable token, session, and user locked for token rotation."""

    token: RefreshToken
    session: AuthSession
    user: User


@dataclass(frozen=True, slots=True)
class EmailActionContext:
    """A user and single-use email action loaded in lock order."""

    token: EmailActionToken
    user: User


@dataclass(frozen=True, slots=True)
class SessionMetadata:
    """Non-authoritative client metadata retained for session management."""

    ip_address: str | None
    user_agent: str | None

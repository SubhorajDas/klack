"""Persistence port used by identity application services."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from klack.modules.identity.domain.entities import (
    AuthenticatedIdentity,
    AuthSession,
    EmailActionContext,
    EmailActionPurpose,
    EmailActionToken,
    EmailOutboxMessage,
    LoginRecord,
    PasswordCredential,
    RefreshContext,
    RefreshToken,
    User,
)


class IdentityConflict(Exception):
    """A database uniqueness invariant rejected identity state."""


@dataclass(frozen=True, slots=True)
class RateLimitRequest:
    """One shared fixed-window bucket to consume atomically."""

    scope: str
    subject_hash: str
    limit: int


class IdentityRepository(Protocol):
    """Transactions and identity persistence required by the application layer."""

    async def email_exists(self, email: str) -> bool: ...

    async def get_user_by_email(
        self,
        email: str,
        *,
        for_update: bool = False,
    ) -> User | None: ...

    async def get_login(
        self,
        email: str,
        *,
        for_update: bool = False,
    ) -> LoginRecord | None: ...

    async def consume_rate_limits(
        self,
        requests: list[RateLimitRequest],
        *,
        now: datetime,
        window: timedelta,
        block_for: timedelta,
    ) -> int | None: ...

    async def add_registration(
        self,
        user: User,
        credential: PasswordCredential,
        session: AuthSession,
        refresh_token: RefreshToken,
        email_action: EmailActionToken,
        outbox_message: EmailOutboxMessage,
    ) -> None: ...

    async def add_session(self, session: AuthSession, refresh_token: RefreshToken) -> None: ...

    async def enforce_active_session_cap(
        self,
        *,
        user_id: UUID,
        now: datetime,
        retain_active: int,
        reason: str,
    ) -> int: ...

    async def update_password_hash(
        self,
        *,
        user_id: UUID,
        password_hash: str,
        changed_at: datetime,
    ) -> None: ...

    async def get_active_session(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        now: datetime,
    ) -> AuthenticatedIdentity | None: ...

    async def get_refresh_context(
        self,
        token_id: UUID,
        *,
        for_update: bool,
    ) -> RefreshContext | None: ...

    async def rotate_refresh(
        self,
        *,
        previous_token_id: UUID,
        replacement: RefreshToken,
        used_at: datetime,
        last_ip: str | None,
    ) -> None: ...

    async def replace_email_action(
        self,
        *,
        token: EmailActionToken,
        outbox_message: EmailOutboxMessage,
        replaced_at: datetime,
    ) -> None: ...

    async def get_email_action_context(
        self,
        token_id: UUID,
        *,
        purpose: EmailActionPurpose,
        for_update: bool,
    ) -> EmailActionContext | None: ...

    async def mark_email_action_used(self, token_id: UUID, *, used_at: datetime) -> None: ...

    async def mark_email_verified(self, user_id: UUID, *, verified_at: datetime) -> None: ...

    async def revoke_email_actions(
        self,
        *,
        user_id: UUID,
        purpose: EmailActionPurpose,
        revoked_at: datetime,
        exclude_token_id: UUID | None = None,
    ) -> None: ...

    async def list_active_sessions(
        self,
        *,
        user_id: UUID,
        now: datetime,
    ) -> list[AuthSession]: ...

    async def revoke_session(
        self,
        *,
        session_id: UUID,
        user_id: UUID | None,
        revoked_at: datetime,
        reason: str,
    ) -> bool: ...

    async def revoke_all_sessions(
        self,
        *,
        user_id: UUID,
        revoked_at: datetime,
        reason: str,
    ) -> int: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

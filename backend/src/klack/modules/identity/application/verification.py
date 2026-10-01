"""Durable email-verification authorization exposed to membership use cases."""

from typing import Protocol
from uuid import UUID

from klack.modules.identity.domain.entities import User
from klack.modules.identity.domain.errors import EmailVerificationRequired


class VerificationRepository(Protocol):
    async def get_user_by_id(self, user_id: UUID) -> User | None: ...


class EmailVerificationGateway(Protocol):
    async def require_verified(self, user_id: UUID) -> None: ...


class EmailVerificationAccessService:
    """Check current database state, rather than verification claims in a token."""

    def __init__(self, repository: VerificationRepository, *, required: bool) -> None:
        self._repository = repository
        self._required = required

    async def require_verified(self, user_id: UUID) -> None:
        if not self._required:
            return
        user = await self._repository.get_user_by_id(user_id)
        if user is None or user.disabled_at is not None or not user.email_verified:
            raise EmailVerificationRequired

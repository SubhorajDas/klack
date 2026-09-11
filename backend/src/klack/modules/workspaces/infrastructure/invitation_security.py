"""Opaque, HMAC-protected workspace invitation tokens."""

import base64
import hmac
import re
import secrets
from collections.abc import Callable
from hashlib import sha256
from uuid import UUID, uuid4

from klack.modules.workspaces.application.ports import (
    IssuedInvitationToken,
    PresentedInvitationToken,
)
from klack.modules.workspaces.domain.errors import InvalidInvitationToken

INVITATION_TOKEN_SECRET_BYTES = 32
MAX_INVITATION_TOKEN_LENGTH = 256
_INVITATION_TOKEN_NAMESPACE = b"workspace-invitation-token:v1:"
_URLSAFE_SECRET = re.compile(r"[A-Za-z0-9_-]+", flags=re.ASCII)

SecretFactory = Callable[[int], str]
UuidFactory = Callable[[], UUID]


class InvitationTokenManager:
    """Issue and verify selector-bearing invitation credentials without storing plaintext."""

    def __init__(
        self,
        *,
        secret: str,
        uuid_factory: UuidFactory = uuid4,
        secret_factory: SecretFactory = secrets.token_urlsafe,
    ) -> None:
        if len(secret) < 32:
            msg = "invitation token signing secret must contain at least 32 characters"
            raise ValueError(msg)
        self._key = secret.encode()
        self._uuid_factory = uuid_factory
        self._secret_factory = secret_factory

    def issue(self) -> IssuedInvitationToken:
        """Create a high-entropy bearer token and its persistence-safe HMAC digest."""
        token_id = self._uuid_factory()
        token_secret = self._secret_factory(INVITATION_TOKEN_SECRET_BYTES)
        if not self._valid_token_secret(token_secret):
            msg = "invitation token secret factory returned an invalid secret"
            raise ValueError(msg)
        raw = f"{token_id}.{token_secret}"
        if len(raw) > MAX_INVITATION_TOKEN_LENGTH:
            msg = "invitation token secret factory returned an oversized secret"
            raise ValueError(msg)
        return IssuedInvitationToken(
            token_id=token_id,
            raw=raw,
            digest=self._digest(raw),
        )

    def present(self, raw_token: str | None) -> PresentedInvitationToken:
        """Parse a bounded credential and recompute its persistence-safe digest."""
        if raw_token is None or not raw_token or len(raw_token) > MAX_INVITATION_TOKEN_LENGTH:
            raise InvalidInvitationToken
        try:
            selector, token_secret = raw_token.split(".", maxsplit=1)
            token_id = UUID(selector)
        except (AttributeError, ValueError) as exc:
            raise InvalidInvitationToken from exc
        if not self._valid_token_secret(token_secret):
            raise InvalidInvitationToken
        return PresentedInvitationToken(
            token_id=token_id,
            digest=self._digest(raw_token),
        )

    @staticmethod
    def matches(actual_digest: str, expected_digest: str) -> bool:
        """Compare invitation-token HMACs in constant time."""
        return hmac.compare_digest(actual_digest, expected_digest)

    def _digest(self, raw_token: str) -> str:
        return hmac.new(
            self._key,
            _INVITATION_TOKEN_NAMESPACE + raw_token.encode(),
            sha256,
        ).hexdigest()

    @staticmethod
    def _valid_token_secret(value: str) -> bool:
        if _URLSAFE_SECRET.fullmatch(value) is None:
            return False
        padding = "=" * (-len(value) % 4)
        try:
            decoded = base64.b64decode(
                (value + padding).encode("ascii"),
                altchars=b"-_",
                validate=True,
            )
        except (UnicodeEncodeError, ValueError):
            return False
        canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
        return len(decoded) >= INVITATION_TOKEN_SECRET_BYTES and canonical == value

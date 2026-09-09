"""Opaque email-action tokens, encrypted outbox payloads, and HMAC rate-limit keys."""

import base64
import hmac
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from klack.modules.identity.domain.entities import EmailActionPurpose
from klack.modules.identity.domain.errors import InvalidEmailActionToken

ACTION_SECRET_BYTES = 32
SecretFactory = Callable[[int], str]
NonceFactory = Callable[[int], bytes]
UuidFactory = Callable[[], UUID]


@dataclass(frozen=True, slots=True)
class IssuedActionToken:
    """A raw email bearer token and the digest safe for persistence."""

    token_id: UUID
    raw: str
    digest: str


@dataclass(frozen=True, slots=True)
class PresentedActionToken:
    """A parsed action selector and recomputed digest."""

    token_id: UUID
    digest: str


class ActionTokenManager:
    """Issue HMAC-only action tokens and encrypt their queued email presentation."""

    def __init__(
        self,
        *,
        secret: str,
        uuid_factory: UuidFactory = uuid4,
        secret_factory: SecretFactory = secrets.token_urlsafe,
        nonce_factory: NonceFactory = secrets.token_bytes,
    ) -> None:
        self._key = secret.encode()
        self._encryption_key = sha256(b"identity-email-outbox:" + self._key).digest()
        self._uuid_factory = uuid_factory
        self._secret_factory = secret_factory
        self._nonce_factory = nonce_factory

    def issue(self, purpose: EmailActionPurpose) -> IssuedActionToken:
        """Create a selector-bearing high-entropy action token."""
        token_id = self._uuid_factory()
        raw = f"{token_id}.{self._secret_factory(ACTION_SECRET_BYTES)}"
        return IssuedActionToken(
            token_id=token_id,
            raw=raw,
            digest=self._digest(f"action:{purpose}", raw),
        )

    def present(self, raw: str | None, purpose: EmailActionPurpose) -> PresentedActionToken:
        """Parse one bounded action token and recompute its purpose-bound digest."""
        if raw is None or len(raw) > 256:
            raise InvalidEmailActionToken
        try:
            selector, secret = raw.split(".", maxsplit=1)
            token_id = UUID(selector)
        except (ValueError, AttributeError) as exc:
            raise InvalidEmailActionToken from exc
        if len(secret) < 32:
            raise InvalidEmailActionToken
        return PresentedActionToken(
            token_id=token_id,
            digest=self._digest(f"action:{purpose}", raw),
        )

    @staticmethod
    def matches(actual_digest: str, expected_digest: str) -> bool:
        """Compare action-token HMACs in constant time."""
        return hmac.compare_digest(actual_digest, expected_digest)

    def rate_limit_key(self, scope: str, subject: str) -> str:
        """Return a non-reversible shared-throttle subject key."""
        return self._digest(f"rate:{scope}", subject)

    def seal_email_action(
        self,
        *,
        raw_token: str,
        recipient: str,
        purpose: EmailActionPurpose,
    ) -> str:
        """Encrypt an action token for durable transactional email delivery."""
        nonce = self._nonce_factory(12)
        payload = json.dumps({"token": raw_token}, separators=(",", ":")).encode()
        ciphertext = AESGCM(self._encryption_key).encrypt(
            nonce,
            payload,
            self._associated_data(recipient, purpose),
        )
        return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def open_email_action(
        self,
        *,
        encrypted_payload: str,
        recipient: str,
        purpose: EmailActionPurpose,
    ) -> str:
        """Authenticate and decrypt one queued email action token."""
        sealed = base64.urlsafe_b64decode(encrypted_payload.encode("ascii"))
        if len(sealed) < 29:
            msg = "invalid encrypted email payload"
            raise ValueError(msg)
        nonce, ciphertext = sealed[:12], sealed[12:]
        plaintext = AESGCM(self._encryption_key).decrypt(
            nonce,
            ciphertext,
            self._associated_data(recipient, purpose),
        )
        parsed = json.loads(plaintext)
        token = parsed.get("token")
        if not isinstance(token, str):
            msg = "invalid encrypted email payload"
            raise ValueError(msg)
        return token

    def _digest(self, namespace: str, value: str) -> str:
        return hmac.new(
            self._key,
            f"{namespace}:{value}".encode(),
            sha256,
        ).hexdigest()

    @staticmethod
    def _associated_data(recipient: str, purpose: EmailActionPurpose) -> bytes:
        return f"{purpose}:{recipient}".encode()

"""Password hashing and signed/opaque token primitives."""

import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Final, cast
from uuid import UUID, uuid4

import jwt
from anyio import CapacityLimiter, to_thread
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from jwt import InvalidTokenError

from klack.modules.identity.domain.errors import AuthenticationRequired, SessionExpired

JWT_ALGORITHM: Final[str] = "HS256"
JWT_CLOCK_SKEW_SECONDS: Final[int] = 5
REFRESH_SECRET_BYTES: Final[int] = 32

Clock = Callable[[], datetime]
UuidFactory = Callable[[], UUID]
SecretFactory = Callable[[int], str]


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""
    return datetime.now(UTC)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        msg = "clock must return an aware datetime"
        raise ValueError(msg)
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class PasswordVerification:
    """The safe outcome of an Argon2 verification attempt."""

    valid: bool
    needs_rehash: bool = False


class PasswordManager:
    """Hash passwords with explicit Argon2id parameters and safe verification failures."""

    def __init__(
        self,
        hasher: PasswordHasher | None = None,
        *,
        max_concurrency: int = 2,
    ) -> None:
        self._hasher = hasher or PasswordHasher(
            time_cost=3,
            memory_cost=65_536,
            parallelism=4,
            hash_len=32,
            salt_len=16,
        )
        self._dummy_hash: str | None = None
        self._max_concurrency = max_concurrency
        self._limiter: CapacityLimiter | None = None

    def hash(self, password: str) -> str:
        """Create a salted Argon2id password hash."""
        return self._hasher.hash(password)

    def verify(self, password: str, password_hash: str) -> PasswordVerification:
        """Verify without exposing malformed hashes or mismatch details."""
        try:
            valid = self._hasher.verify(password_hash, password)
        except (InvalidHashError, VerificationError):
            return PasswordVerification(valid=False)
        return PasswordVerification(
            valid=valid,
            needs_rehash=valid and self._hasher.check_needs_rehash(password_hash),
        )

    def verify_dummy(self, password: str) -> None:
        """Spend one normal hash verification when an email is unknown."""
        if self._dummy_hash is None:
            self._dummy_hash = self.hash(secrets.token_urlsafe(32))
        self.verify(password, self._dummy_hash)

    async def hash_async(self, password: str) -> str:
        """Hash outside the event loop behind a process-local memory/CPU bound."""
        return await to_thread.run_sync(
            self.hash,
            password,
            limiter=self._capacity_limiter(),
        )

    async def verify_async(self, password: str, password_hash: str) -> PasswordVerification:
        """Verify outside the event loop behind the same bounded limiter."""
        return await to_thread.run_sync(
            self.verify,
            password,
            password_hash,
            limiter=self._capacity_limiter(),
        )

    async def verify_dummy_async(self, password: str) -> None:
        """Run unknown-account timing work outside the event loop."""
        await to_thread.run_sync(
            self.verify_dummy,
            password,
            limiter=self._capacity_limiter(),
        )

    def _capacity_limiter(self) -> CapacityLimiter:
        if self._limiter is None:
            self._limiter = CapacityLimiter(self._max_concurrency)
        return self._limiter


@dataclass(frozen=True, slots=True)
class AccessClaims:
    """Validated claims required from a Klack access JWT."""

    user_id: UUID
    session_id: UUID
    token_id: UUID
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedAccessToken:
    """A serialized access JWT and its expiry."""

    raw: str
    expires_at: datetime


class AccessTokenCodec:
    """Issue and strictly validate short-lived HS256 access tokens."""

    def __init__(
        self,
        *,
        secret: str,
        issuer: str,
        audience: str,
        ttl: timedelta,
        clock: Clock = utc_now,
        uuid_factory: UuidFactory = uuid4,
    ) -> None:
        self._secret = secret
        self._issuer = issuer
        self._audience = audience
        self._ttl = ttl
        self._clock = clock
        self._uuid_factory = uuid_factory

    def issue(self, *, user_id: UUID, session_id: UUID) -> IssuedAccessToken:
        """Sign a token bound to both the user and durable session."""
        now = _aware_utc(self._clock())
        expires_at = now + self._ttl
        payload: dict[str, object] = {
            "sub": str(user_id),
            "sid": str(session_id),
            "jti": str(self._uuid_factory()),
            "iss": self._issuer,
            "aud": self._audience,
            "type": "access",
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        }
        raw = jwt.encode(payload, self._secret, algorithm=JWT_ALGORITHM)
        return IssuedAccessToken(raw=raw, expires_at=expires_at)

    def decode(self, raw: str) -> AccessClaims:
        """Validate signature, namespace, required claims, timestamps, and UUID claims."""
        try:
            decoded = jwt.decode(
                raw,
                self._secret,
                algorithms=[JWT_ALGORITHM],
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "require": ["sub", "sid", "jti", "iss", "aud", "type", "iat", "nbf", "exp"],
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
            payload = cast(dict[str, object], decoded)
            if payload.get("type") != "access":
                raise AuthenticationRequired
            issued_at = self._integer_claim(payload, "iat")
            not_before = self._integer_claim(payload, "nbf")
            expires_at = self._integer_claim(payload, "exp")
            now = int(_aware_utc(self._clock()).timestamp())
            if (
                issued_at > now + JWT_CLOCK_SKEW_SECONDS
                or not_before > now + JWT_CLOCK_SKEW_SECONDS
                or expires_at <= now
                or expires_at <= issued_at
            ):
                raise AuthenticationRequired
            return AccessClaims(
                user_id=UUID(self._string_claim(payload, "sub")),
                session_id=UUID(self._string_claim(payload, "sid")),
                token_id=UUID(self._string_claim(payload, "jti")),
                issued_at=datetime.fromtimestamp(issued_at, UTC),
                expires_at=datetime.fromtimestamp(expires_at, UTC),
            )
        except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
            raise AuthenticationRequired from exc

    @staticmethod
    def _integer_claim(payload: dict[str, object], name: str) -> int:
        value = payload[name]
        if not isinstance(value, int) or isinstance(value, bool):
            raise AuthenticationRequired
        return value

    @staticmethod
    def _string_claim(payload: dict[str, object], name: str) -> str:
        value = payload[name]
        if not isinstance(value, str):
            raise AuthenticationRequired
        return value


@dataclass(frozen=True, slots=True)
class IssuedOpaqueToken:
    """An opaque token presentation plus its safe durable digest."""

    token_id: UUID
    raw: str
    digest: str


@dataclass(frozen=True, slots=True)
class PresentedOpaqueToken:
    """A parsed token selector and recomputed digest."""

    token_id: UUID
    digest: str


@dataclass(frozen=True, slots=True)
class IssuedCsrfToken:
    """A CSRF cookie/header value plus its session-bound digest."""

    raw: str
    digest: str


class SessionTokenManager:
    """Create and HMAC opaque refresh and CSRF tokens with domain separation."""

    def __init__(
        self,
        *,
        secret: str,
        uuid_factory: UuidFactory = uuid4,
        secret_factory: SecretFactory = secrets.token_urlsafe,
    ) -> None:
        self._key = secret.encode()
        self._uuid_factory = uuid_factory
        self._secret_factory = secret_factory

    def issue_refresh(self) -> IssuedOpaqueToken:
        """Return a high-entropy refresh token whose plaintext is never persisted."""
        token_id = self._uuid_factory()
        raw = f"{token_id}.{self._secret_factory(REFRESH_SECRET_BYTES)}"
        return IssuedOpaqueToken(
            token_id=token_id,
            raw=raw,
            digest=self._digest("refresh", raw),
        )

    def present_refresh(self, raw: str) -> PresentedOpaqueToken:
        """Parse a bounded refresh presentation and recompute its HMAC digest."""
        if len(raw) > 256:
            raise SessionExpired
        try:
            selector, secret = raw.split(".", maxsplit=1)
            token_id = UUID(selector)
        except (ValueError, AttributeError) as exc:
            raise SessionExpired from exc
        if len(secret) < 32:
            raise SessionExpired
        return PresentedOpaqueToken(token_id=token_id, digest=self._digest("refresh", raw))

    def issue_csrf(self) -> IssuedCsrfToken:
        """Create a session-bound double-submit value."""
        raw = self._secret_factory(REFRESH_SECRET_BYTES)
        return IssuedCsrfToken(raw=raw, digest=self._digest("csrf", raw))

    def csrf_matches(self, raw: str | None, expected_digest: str) -> bool:
        """Compare a presented CSRF token with the session digest in constant time."""
        if raw is None or len(raw) > 256:
            return False
        actual = self._digest("csrf", raw)
        return hmac.compare_digest(actual, expected_digest)

    def refresh_matches(self, actual_digest: str, expected_digest: str) -> bool:
        """Compare refresh HMACs in constant time."""
        return hmac.compare_digest(actual_digest, expected_digest)

    def _digest(self, namespace: str, raw: str) -> str:
        return hmac.new(self._key, f"{namespace}:{raw}".encode(), sha256).hexdigest()

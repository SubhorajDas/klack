"""Identity normalization, password, JWT, and opaque-token security contracts."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from argon2 import PasswordHasher
from cryptography.exceptions import InvalidTag

from klack.modules.identity.domain.email import normalize_email
from klack.modules.identity.domain.entities import EmailActionPurpose
from klack.modules.identity.domain.errors import (
    AuthenticationRequired,
    InvalidEmailActionToken,
    InvalidEmailAddress,
    SessionExpired,
)
from klack.modules.identity.infrastructure.action_security import ActionTokenManager
from klack.modules.identity.infrastructure.security import (
    JWT_ALGORITHM,
    AccessTokenCodec,
    PasswordManager,
    SessionTokenManager,
)

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
JWT_SECRET = "jwt-secret-at-least-thirty-two-characters"
REFRESH_SECRET = "refresh-secret-at-least-thirty-two-characters"
USER_ID = UUID("11111111-1111-4111-8111-111111111111")
SESSION_ID = UUID("22222222-2222-4222-8222-222222222222")
TOKEN_ID = UUID("33333333-3333-4333-8333-333333333333")


def test_email_normalization_is_case_insensitive_and_supports_idna() -> None:
    assert normalize_email("  Person@BÜCHER.example ") == "person@xn--bcher-kva.example"


@pytest.mark.parametrize("value", ["", "not-an-email", "a@", "@example.com"])
def test_invalid_email_is_rejected(value: str) -> None:
    with pytest.raises(InvalidEmailAddress):
        normalize_email(value)


def test_argon2id_hash_verify_rehash_and_dummy_paths() -> None:
    old_hasher = PasswordHasher(time_cost=1, memory_cost=8_192, parallelism=1)
    manager = PasswordManager(
        PasswordHasher(time_cost=2, memory_cost=8_192, parallelism=1),
    )
    password_hash = old_hasher.hash("correct horse battery staple")

    verification = manager.verify("correct horse battery staple", password_hash)

    assert verification.valid is True
    assert verification.needs_rehash is True
    assert manager.verify("wrong password", password_hash).valid is False
    assert manager.verify("password", "not-an-argon-hash").valid is False
    assert manager.hash("a new password").startswith("$argon2id$")
    manager.verify_dummy("unknown-account-password")
    manager.verify_dummy("unknown-account-password")


def make_codec(*, now: datetime = NOW, secret: str = JWT_SECRET) -> AccessTokenCodec:
    return AccessTokenCodec(
        secret=secret,
        issuer="klack-api",
        audience="klack-web",
        ttl=timedelta(minutes=15),
        clock=lambda: now,
        uuid_factory=lambda: TOKEN_ID,
    )


def test_access_token_round_trip_has_required_session_claims() -> None:
    codec = make_codec()

    issued = codec.issue(user_id=USER_ID, session_id=SESSION_ID)
    claims = codec.decode(issued.raw)
    payload = jwt.decode(
        issued.raw,
        JWT_SECRET,
        algorithms=[JWT_ALGORITHM],
        audience="klack-web",
        issuer="klack-api",
        options={"verify_exp": False},
    )

    assert claims.user_id == USER_ID
    assert claims.session_id == SESSION_ID
    assert claims.token_id == TOKEN_ID
    assert claims.issued_at == NOW
    assert claims.expires_at == NOW + timedelta(minutes=15)
    assert payload["type"] == "access"
    assert payload["sid"] == str(SESSION_ID)
    assert payload["jti"] == str(TOKEN_ID)


def test_access_token_rejects_naive_clock_and_expiry() -> None:
    naive = make_codec(now=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="aware"):
        naive.issue(user_id=USER_ID, session_id=SESSION_ID)

    issued = make_codec().issue(user_id=USER_ID, session_id=SESSION_ID)
    expired_codec = make_codec(now=NOW + timedelta(minutes=16))
    with pytest.raises(AuthenticationRequired):
        expired_codec.decode(issued.raw)


def signed_payload(**overrides: object) -> str:
    payload: dict[str, object] = {
        "sub": str(USER_ID),
        "sid": str(SESSION_ID),
        "jti": str(TOKEN_ID),
        "iss": "klack-api",
        "aud": "klack-web",
        "type": "access",
        "iat": int(NOW.timestamp()),
        "nbf": int(NOW.timestamp()),
        "exp": int((NOW + timedelta(minutes=15)).timestamp()),
    }
    payload.update(overrides)
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


@pytest.mark.parametrize(
    "raw",
    [
        "not-a-jwt",
        signed_payload(type="refresh"),
        signed_payload(sub="not-a-uuid"),
        signed_payload(iat=True),
        signed_payload(iat=int((NOW + timedelta(minutes=1)).timestamp())),
        signed_payload(nbf=int((NOW + timedelta(minutes=1)).timestamp())),
        signed_payload(exp=int((NOW - timedelta(seconds=1)).timestamp())),
        signed_payload(exp=int(NOW.timestamp())),
        jwt.encode(
            {
                "sub": str(USER_ID),
                "sid": str(SESSION_ID),
                "jti": str(TOKEN_ID),
                "iss": "wrong-issuer",
                "aud": "klack-web",
                "type": "access",
                "iat": int(NOW.timestamp()),
                "nbf": int(NOW.timestamp()),
                "exp": int((NOW + timedelta(minutes=15)).timestamp()),
            },
            JWT_SECRET,
            algorithm=JWT_ALGORITHM,
        ),
        jwt.encode(
            {
                "sub": str(USER_ID),
                "sid": str(SESSION_ID),
                "jti": str(TOKEN_ID),
                "iss": "klack-api",
                "aud": "wrong-audience",
                "type": "access",
                "iat": int(NOW.timestamp()),
                "nbf": int(NOW.timestamp()),
                "exp": int((NOW + timedelta(minutes=15)).timestamp()),
            },
            JWT_SECRET,
            algorithm=JWT_ALGORITHM,
        ),
    ],
)
def test_access_token_rejects_invalid_claims(raw: str) -> None:
    with pytest.raises(AuthenticationRequired):
        make_codec().decode(raw)


def test_access_token_rejects_wrong_signature_and_missing_claim() -> None:
    wrong_key = make_codec(secret="different-jwt-secret-at-least-thirty-two")
    valid = make_codec().issue(user_id=USER_ID, session_id=SESSION_ID)
    with pytest.raises(AuthenticationRequired):
        wrong_key.decode(valid.raw)

    payload = jwt.decode(valid.raw, options={"verify_signature": False})
    del payload["sid"]
    missing = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    with pytest.raises(AuthenticationRequired):
        make_codec().decode(missing)


def test_refresh_and_csrf_tokens_are_hmac_protected() -> None:
    manager = SessionTokenManager(secret=REFRESH_SECRET)

    refresh = manager.issue_refresh()
    presentation = manager.present_refresh(refresh.raw)
    csrf = manager.issue_csrf()

    assert presentation.token_id == refresh.token_id
    assert presentation.digest == refresh.digest
    assert refresh.raw not in refresh.digest
    assert manager.refresh_matches(presentation.digest, refresh.digest)
    assert manager.csrf_matches(csrf.raw, csrf.digest)
    assert not manager.csrf_matches("tampered", csrf.digest)
    assert not manager.csrf_matches(None, csrf.digest)
    assert not manager.csrf_matches("x" * 257, csrf.digest)


@pytest.mark.parametrize(
    "raw",
    ["", "not-a-token", f"{uuid4()}.short", "x" * 257],
)
def test_invalid_refresh_presentations_are_rejected(raw: str) -> None:
    manager = SessionTokenManager(secret=REFRESH_SECRET)
    with pytest.raises(SessionExpired):
        manager.present_refresh(raw)


def test_email_action_tokens_are_purpose_bound_and_outbox_encrypted() -> None:
    manager = ActionTokenManager(
        secret="action-secret-at-least-thirty-two-characters",
        uuid_factory=lambda: TOKEN_ID,
        secret_factory=lambda _size: "s" * 43,
        nonce_factory=lambda size: b"n" * size,
    )
    issued = manager.issue(EmailActionPurpose.VERIFY_EMAIL)
    presented = manager.present(issued.raw, EmailActionPurpose.VERIFY_EMAIL)
    wrong_purpose = manager.present(issued.raw, EmailActionPurpose.RECOVER_PASSWORD)
    sealed = manager.seal_email_action(
        raw_token=issued.raw,
        recipient="person@example.com",
        purpose=EmailActionPurpose.VERIFY_EMAIL,
    )

    assert issued.raw not in issued.digest
    assert issued.raw not in sealed
    assert presented.token_id == TOKEN_ID
    assert manager.matches(presented.digest, issued.digest)
    assert not manager.matches(wrong_purpose.digest, issued.digest)
    assert (
        manager.open_email_action(
            encrypted_payload=sealed,
            recipient="person@example.com",
            purpose=EmailActionPurpose.VERIFY_EMAIL,
        )
        == issued.raw
    )
    with pytest.raises(InvalidTag):
        manager.open_email_action(
            encrypted_payload=sealed,
            recipient="attacker@example.com",
            purpose=EmailActionPurpose.VERIFY_EMAIL,
        )


def test_action_presentations_and_rate_keys_are_safely_bounded() -> None:
    manager = ActionTokenManager(secret="action-secret-at-least-thirty-two-characters")

    for raw in (None, "", "not-a-token", f"{uuid4()}.short", "x" * 257):
        with pytest.raises(InvalidEmailActionToken):
            manager.present(raw, EmailActionPurpose.VERIFY_EMAIL)

    subject_key = manager.rate_limit_key("login:subject", "person@example.com")
    ip_key = manager.rate_limit_key("login:ip", "192.0.2.1")
    assert subject_key != ip_key
    assert "person@example.com" not in subject_key
    with pytest.raises(ValueError, match="invalid encrypted"):
        manager.open_email_action(
            encrypted_payload="YQ==",
            recipient="person@example.com",
            purpose=EmailActionPurpose.VERIFY_EMAIL,
        )

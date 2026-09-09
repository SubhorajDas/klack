"""Identity application transaction and session-lifecycle contracts."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from argon2 import PasswordHasher

from klack.modules.identity.application.ports import IdentityConflict, RateLimitRequest
from klack.modules.identity.application.service import IdentityPolicy, IdentityService
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
    SessionMetadata,
    User,
)
from klack.modules.identity.domain.errors import (
    AuthenticationRateLimited,
    AuthenticationRequired,
    CsrfValidationFailed,
    EmailAlreadyRegistered,
    InvalidCredentials,
    InvalidEmailActionToken,
    InvalidEmailAddress,
    InvalidPassword,
    RefreshRateLimited,
    RefreshTokenReuseDetected,
    SessionExpired,
    SessionNotFound,
)
from klack.modules.identity.infrastructure.action_security import ActionTokenManager
from klack.modules.identity.infrastructure.security import (
    AccessTokenCodec,
    PasswordManager,
    SessionTokenManager,
)

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
METADATA = SessionMetadata(ip_address="192.0.2.10", user_agent="Test Browser")
PASSWORD = "correct horse battery staple"


class MutableClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class MemoryIdentityRepository:
    """Behavioral fake retaining the same transaction-visible identity values."""

    def __init__(self) -> None:
        self.users: dict[UUID, User] = {}
        self.credentials: dict[UUID, PasswordCredential] = {}
        self.sessions: dict[UUID, AuthSession] = {}
        self.refresh_tokens: dict[UUID, RefreshToken] = {}
        self.email_actions: dict[UUID, EmailActionToken] = {}
        self.outbox: dict[UUID, EmailOutboxMessage] = {}
        self.rate_limit_requests: list[list[RateLimitRequest]] = []
        self.rate_limit_retry: int | None = None
        self.commit_count = 0
        self.rollback_count = 0
        self.conflict_on_commit = False
        self.registration_pending = False
        self.refresh_for_update: list[bool] = []
        self.password_updates: list[UUID] = []
        self.login_for_update_count = 0
        self.login_hash_on_first_lock: str | None = None

    async def email_exists(self, email: str) -> bool:
        return any(user.email == email for user in self.users.values())

    async def get_user_by_email(
        self,
        email: str,
        *,
        for_update: bool = False,
    ) -> User | None:
        del for_update
        return next((item for item in self.users.values() if item.email == email), None)

    async def get_login(
        self,
        email: str,
        *,
        for_update: bool = False,
    ) -> LoginRecord | None:
        user = next((item for item in self.users.values() if item.email == email), None)
        if user is None:
            return None
        credential = self.credentials.get(user.id)
        if credential is None:
            return None
        if for_update:
            self.login_for_update_count += 1
            if self.login_hash_on_first_lock is not None:
                credential = replace(
                    credential,
                    password_hash=self.login_hash_on_first_lock,
                )
                self.credentials[user.id] = credential
                self.login_hash_on_first_lock = None
        return LoginRecord(user=user, credential=credential)

    async def consume_rate_limits(
        self,
        requests: list[RateLimitRequest],
        *,
        now: datetime,
        window: timedelta,
        block_for: timedelta,
    ) -> int | None:
        del now, window, block_for
        self.rate_limit_requests.append(requests)
        return self.rate_limit_retry

    async def add_registration(
        self,
        user: User,
        credential: PasswordCredential,
        session: AuthSession,
        refresh_token: RefreshToken,
        email_action: EmailActionToken,
        outbox_message: EmailOutboxMessage,
    ) -> None:
        self.users[user.id] = user
        self.credentials[user.id] = credential
        self.email_actions[email_action.id] = email_action
        self.outbox[outbox_message.id] = outbox_message
        self.registration_pending = True
        await self.add_session(session, refresh_token)

    async def add_session(self, session: AuthSession, refresh_token: RefreshToken) -> None:
        self.sessions[session.id] = session
        self.refresh_tokens[refresh_token.id] = refresh_token

    async def enforce_active_session_cap(
        self,
        *,
        user_id: UUID,
        now: datetime,
        retain_active: int,
        reason: str,
    ) -> int:
        active = sorted(
            (
                session
                for session in self.sessions.values()
                if session.user_id == user_id and session.is_active(now)
            ),
            key=lambda item: (item.created_at, item.id),
        )
        expired = active[: max(0, len(active) - retain_active)]
        for session in expired:
            await self.revoke_session(
                session_id=session.id,
                user_id=user_id,
                revoked_at=now,
                reason=reason,
            )
        return len(expired)

    async def update_password_hash(
        self,
        *,
        user_id: UUID,
        password_hash: str,
        changed_at: datetime,
    ) -> None:
        credential = self.credentials[user_id]
        self.credentials[user_id] = replace(
            credential,
            password_hash=password_hash,
            password_changed_at=changed_at,
        )
        self.password_updates.append(user_id)

    async def get_active_session(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        now: datetime,
    ) -> AuthenticatedIdentity | None:
        session = self.sessions.get(session_id)
        user = self.users.get(user_id)
        if (
            session is None
            or user is None
            or session.user_id != user_id
            or not session.is_active(now)
            or user.disabled_at is not None
        ):
            return None
        return AuthenticatedIdentity(user=user, session=session)

    async def get_refresh_context(
        self,
        token_id: UUID,
        *,
        for_update: bool,
    ) -> RefreshContext | None:
        self.refresh_for_update.append(for_update)
        token = self.refresh_tokens.get(token_id)
        if token is None:
            return None
        session = self.sessions.get(token.session_id)
        if session is None:
            return None
        user = self.users.get(session.user_id)
        if user is None:
            return None
        return RefreshContext(token=token, session=session, user=user)

    async def rotate_refresh(
        self,
        *,
        previous_token_id: UUID,
        replacement: RefreshToken,
        used_at: datetime,
        last_ip: str | None,
    ) -> None:
        previous = self.refresh_tokens[previous_token_id]
        self.refresh_tokens[previous_token_id] = replace(
            previous,
            used_at=used_at,
            replaced_by_token_id=replacement.id,
        )
        self.refresh_tokens[replacement.id] = replacement
        session = self.sessions[replacement.session_id]
        self.sessions[replacement.session_id] = replace(
            session,
            last_seen_at=used_at,
            last_ip=last_ip,
        )

    async def replace_email_action(
        self,
        *,
        token: EmailActionToken,
        outbox_message: EmailOutboxMessage,
        replaced_at: datetime,
    ) -> None:
        await self.revoke_email_actions(
            user_id=token.user_id,
            purpose=token.purpose,
            revoked_at=replaced_at,
        )
        self.email_actions[token.id] = token
        self.outbox[outbox_message.id] = outbox_message

    async def get_email_action_context(
        self,
        token_id: UUID,
        *,
        purpose: EmailActionPurpose,
        for_update: bool,
    ) -> EmailActionContext | None:
        del for_update
        token = self.email_actions.get(token_id)
        if token is None or token.purpose is not purpose:
            return None
        user = self.users.get(token.user_id)
        if user is None:
            return None
        return EmailActionContext(token=token, user=user)

    async def mark_email_action_used(self, token_id: UUID, *, used_at: datetime) -> None:
        self.email_actions[token_id] = replace(self.email_actions[token_id], used_at=used_at)

    async def mark_email_verified(self, user_id: UUID, *, verified_at: datetime) -> None:
        self.users[user_id] = replace(self.users[user_id], email_verified_at=verified_at)

    async def revoke_email_actions(
        self,
        *,
        user_id: UUID,
        purpose: EmailActionPurpose,
        revoked_at: datetime,
        exclude_token_id: UUID | None = None,
    ) -> None:
        for token_id, token in list(self.email_actions.items()):
            if (
                token.user_id == user_id
                and token.purpose is purpose
                and token_id != exclude_token_id
                and token.used_at is None
                and token.revoked_at is None
            ):
                self.email_actions[token_id] = replace(token, revoked_at=revoked_at)

    async def list_active_sessions(
        self,
        *,
        user_id: UUID,
        now: datetime,
    ) -> list[AuthSession]:
        return sorted(
            (
                session
                for session in self.sessions.values()
                if session.user_id == user_id and session.is_active(now)
            ),
            key=lambda item: item.created_at,
            reverse=True,
        )

    async def revoke_session(
        self,
        *,
        session_id: UUID,
        user_id: UUID | None,
        revoked_at: datetime,
        reason: str,
    ) -> bool:
        session = self.sessions.get(session_id)
        if session is None or (user_id is not None and session.user_id != user_id):
            return False
        if session.revoked_at is None:
            self.sessions[session_id] = replace(
                session,
                revoked_at=revoked_at,
                revocation_reason=reason,
            )
            for token_id, token in list(self.refresh_tokens.items()):
                if (
                    token.session_id == session_id
                    and token.used_at is None
                    and token.revoked_at is None
                ):
                    self.refresh_tokens[token_id] = replace(token, revoked_at=revoked_at)
        return True

    async def revoke_all_sessions(
        self,
        *,
        user_id: UUID,
        revoked_at: datetime,
        reason: str,
    ) -> int:
        count = 0
        for session_id, session in list(self.sessions.items()):
            if session.user_id == user_id and session.revoked_at is None:
                await self.revoke_session(
                    session_id=session_id,
                    user_id=user_id,
                    revoked_at=revoked_at,
                    reason=reason,
                )
                count += 1
        return count

    async def commit(self) -> None:
        if self.conflict_on_commit and self.registration_pending:
            self.conflict_on_commit = False
            self.registration_pending = False
            raise IdentityConflict
        self.registration_pending = False
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1


class ServiceHarness:
    def __init__(
        self,
        *,
        refresh_ttl: timedelta = timedelta(days=30),
        refresh_min_interval: timedelta = timedelta(minutes=5),
        max_refresh_token_rows_per_session: int = 10_000,
        max_active_sessions: int = 10,
    ) -> None:
        self.clock = MutableClock()
        self.repository = MemoryIdentityRepository()
        self.passwords = PasswordManager(
            PasswordHasher(time_cost=2, memory_cost=8_192, parallelism=1),
        )
        self.access_tokens = AccessTokenCodec(
            secret="test-jwt-secret-at-least-thirty-two-bytes",
            issuer="klack-api",
            audience="klack-web",
            ttl=timedelta(minutes=15),
            clock=self.clock,
        )
        self.session_tokens = SessionTokenManager(
            secret="test-refresh-secret-at-least-thirty-two-bytes",
        )
        self.action_tokens = ActionTokenManager(
            secret="test-action-secret-at-least-thirty-two-bytes",
        )
        self.service = IdentityService(
            repository=self.repository,
            passwords=self.passwords,
            access_tokens=self.access_tokens,
            session_tokens=self.session_tokens,
            action_tokens=self.action_tokens,
            policy=IdentityPolicy(
                refresh_ttl=refresh_ttl,
                refresh_min_interval=refresh_min_interval,
                max_refresh_token_rows_per_session=max_refresh_token_rows_per_session,
                max_active_sessions=max_active_sessions,
            ),
            clock=self.clock,
        )

    async def register(self, *, email: str = "Person@Example.COM"):
        return await self.service.register(
            email=email,
            password=PASSWORD,
            metadata=METADATA,
        )


@pytest.fixture
def harness() -> ServiceHarness:
    return ServiceHarness()


async def test_registration_normalizes_hashes_and_creates_session(
    harness: ServiceHarness,
) -> None:
    result = await harness.register()

    user = result.identity.user
    session = result.identity.session
    credential = harness.repository.credentials[user.id]
    presented = harness.session_tokens.present_refresh(result.refresh_token)

    assert user.email == "person@example.com"
    assert user.email_verified is False
    assert credential.password_hash != PASSWORD
    assert harness.passwords.verify(PASSWORD, credential.password_hash).valid
    assert session.created_ip == METADATA.ip_address
    assert session.user_agent == METADATA.user_agent
    assert session.expires_at == NOW + timedelta(days=30)
    assert harness.repository.refresh_tokens[presented.token_id].token_hash == presented.digest
    assert harness.access_tokens.decode(result.access_token).session_id == session.id
    assert result.csrf_token not in session.csrf_token_hash
    assert harness.repository.commit_count == 2


@pytest.mark.parametrize("password", ["short", "x" * 129])
async def test_registration_rejects_invalid_password_lengths(
    harness: ServiceHarness,
    password: str,
) -> None:
    with pytest.raises(InvalidPassword):
        await harness.service.register(
            email="person@example.com",
            password=password,
            metadata=METADATA,
        )
    assert harness.repository.users == {}


async def test_registration_rejects_invalid_and_duplicate_email(
    harness: ServiceHarness,
) -> None:
    with pytest.raises(InvalidEmailAddress):
        await harness.service.register(email="invalid", password=PASSWORD, metadata=METADATA)

    await harness.register()
    with pytest.raises(EmailAlreadyRegistered):
        await harness.register(email="PERSON@example.com")


async def test_registration_translates_database_uniqueness_race(
    harness: ServiceHarness,
) -> None:
    harness.repository.conflict_on_commit = True
    with pytest.raises(EmailAlreadyRegistered):
        await harness.register()


async def test_login_uses_generic_failures_for_unknown_malformed_wrong_and_disabled(
    harness: ServiceHarness,
) -> None:
    with pytest.raises(InvalidCredentials):
        await harness.service.login(
            email="missing@example.com", password=PASSWORD, metadata=METADATA
        )
    with pytest.raises(InvalidCredentials):
        await harness.service.login(email="invalid", password=PASSWORD, metadata=METADATA)

    registered = await harness.register()
    with pytest.raises(InvalidCredentials):
        await harness.service.login(
            email=registered.identity.user.email,
            password="wrong password",
            metadata=METADATA,
        )

    user = registered.identity.user
    harness.repository.users[user.id] = replace(user, disabled_at=NOW)
    with pytest.raises(InvalidCredentials):
        await harness.service.login(email=user.email, password=PASSWORD, metadata=METADATA)


async def test_login_creates_independent_session_and_upgrades_hash(
    harness: ServiceHarness,
) -> None:
    registered = await harness.register()
    user_id = registered.identity.user.id
    old_hasher = PasswordHasher(time_cost=1, memory_cost=8_192, parallelism=1)
    harness.repository.credentials[user_id] = replace(
        harness.repository.credentials[user_id],
        password_hash=old_hasher.hash(PASSWORD),
    )

    logged_in = await harness.service.login(
        email="PERSON@example.com",
        password=PASSWORD,
        metadata=SessionMetadata(ip_address="198.51.100.4", user_agent="Other Browser"),
    )

    assert logged_in.identity.session.id != registered.identity.session.id
    assert logged_in.identity.session.last_ip == "198.51.100.4"
    assert user_id in harness.repository.password_updates
    assert harness.repository.commit_count == 4


async def test_login_retries_one_concurrent_password_rehash(
    harness: ServiceHarness,
) -> None:
    registered = await harness.register()
    user_id = registered.identity.user.id
    old_hasher = PasswordHasher(time_cost=1, memory_cost=8_192, parallelism=1)
    harness.repository.credentials[user_id] = replace(
        harness.repository.credentials[user_id],
        password_hash=old_hasher.hash(PASSWORD),
    )
    harness.repository.login_hash_on_first_lock = harness.passwords.hash(PASSWORD)

    logged_in = await harness.service.login(
        email=registered.identity.user.email,
        password=PASSWORD,
        metadata=METADATA,
    )

    assert logged_in.identity.user.id == user_id
    assert harness.repository.login_for_update_count == 2


async def test_login_rejects_password_changed_during_verification(
    harness: ServiceHarness,
) -> None:
    registered = await harness.register()
    harness.repository.login_hash_on_first_lock = harness.passwords.hash(
        "a newly changed password",
    )

    with pytest.raises(InvalidCredentials):
        await harness.service.login(
            email=registered.identity.user.email,
            password=PASSWORD,
            metadata=METADATA,
        )

    assert harness.repository.login_for_update_count == 1
    assert len(harness.repository.sessions) == 1


async def test_access_authentication_checks_durable_session_state(
    harness: ServiceHarness,
) -> None:
    registered = await harness.register()

    identity = await harness.service.authenticate_access(registered.access_token)
    assert identity.user.id == registered.identity.user.id

    with pytest.raises(AuthenticationRequired):
        await harness.service.authenticate_access(None)
    with pytest.raises(AuthenticationRequired):
        await harness.service.authenticate_access("malformed")

    await harness.repository.revoke_session(
        session_id=identity.session.id,
        user_id=identity.user.id,
        revoked_at=NOW,
        reason="test",
    )
    with pytest.raises(AuthenticationRequired):
        await harness.service.authenticate_access(registered.access_token)


async def test_refresh_enforces_session_rotation_cooldown(
    harness: ServiceHarness,
) -> None:
    registered = await harness.register()

    with pytest.raises(RefreshRateLimited) as exc_info:
        await harness.service.refresh(
            raw_refresh_token=registered.refresh_token,
            csrf_token=registered.csrf_token,
            metadata=METADATA,
        )

    assert exc_info.value.retry_after_seconds == 300
    assert len(harness.repository.refresh_tokens) == 1
    assert harness.repository.commit_count == 2


async def test_refresh_cooldown_caps_legacy_session_history() -> None:
    harness = ServiceHarness(
        refresh_ttl=timedelta(days=90),
        refresh_min_interval=timedelta(seconds=30),
        max_refresh_token_rows_per_session=10_000,
    )
    registered = await harness.register()

    with pytest.raises(RefreshRateLimited) as exc_info:
        await harness.service.refresh(
            raw_refresh_token=registered.refresh_token,
            csrf_token=registered.csrf_token,
            metadata=METADATA,
        )

    assert exc_info.value.retry_after_seconds == 778


async def test_refresh_rotates_and_replay_revokes_entire_session(
    harness: ServiceHarness,
) -> None:
    registered = await harness.register()
    original = harness.session_tokens.present_refresh(registered.refresh_token)
    harness.clock.value += timedelta(minutes=5)

    refreshed = await harness.service.refresh(
        raw_refresh_token=registered.refresh_token,
        csrf_token=registered.csrf_token,
        metadata=SessionMetadata(ip_address="203.0.113.8", user_agent="Test Browser"),
    )

    old_record = harness.repository.refresh_tokens[original.token_id]
    successor = harness.session_tokens.present_refresh(refreshed.refresh_token)
    assert old_record.used_at == harness.clock.value
    assert old_record.replaced_by_token_id == successor.token_id
    assert refreshed.identity.session.last_ip == "203.0.113.8"
    assert refreshed.csrf_token == registered.csrf_token
    assert harness.repository.refresh_for_update[-1] is True

    with pytest.raises(RefreshTokenReuseDetected):
        await harness.service.refresh(
            raw_refresh_token=registered.refresh_token,
            csrf_token=None,
            metadata=METADATA,
        )
    assert harness.repository.sessions[registered.identity.session.id].revocation_reason == (
        "refresh_reuse"
    )
    with pytest.raises(AuthenticationRequired):
        await harness.service.authenticate_access(refreshed.access_token)


async def test_refresh_rejects_missing_tampered_and_bad_csrf(
    harness: ServiceHarness,
) -> None:
    registered = await harness.register()
    with pytest.raises(SessionExpired):
        await harness.service.refresh(
            raw_refresh_token=None,
            csrf_token=registered.csrf_token,
            metadata=METADATA,
        )
    with pytest.raises(SessionExpired):
        await harness.service.refresh(
            raw_refresh_token=f"{uuid4()}.{'x' * 43}",
            csrf_token=registered.csrf_token,
            metadata=METADATA,
        )
    selector, _secret = registered.refresh_token.split(".", maxsplit=1)
    with pytest.raises(SessionExpired):
        await harness.service.refresh(
            raw_refresh_token=f"{selector}.{'z' * 43}",
            csrf_token=registered.csrf_token,
            metadata=METADATA,
        )
    with pytest.raises(CsrfValidationFailed):
        await harness.service.refresh(
            raw_refresh_token=registered.refresh_token,
            csrf_token="wrong-csrf",
            metadata=METADATA,
        )


@pytest.mark.parametrize("state", ["token_revoked", "token_expired", "session_expired", "disabled"])
async def test_refresh_rejects_inactive_state(
    harness: ServiceHarness,
    state: str,
) -> None:
    registered = await harness.register()
    presented = harness.session_tokens.present_refresh(registered.refresh_token)
    token = harness.repository.refresh_tokens[presented.token_id]
    session = registered.identity.session
    user = registered.identity.user
    if state == "token_revoked":
        harness.repository.refresh_tokens[token.id] = replace(token, revoked_at=NOW)
    elif state == "token_expired":
        harness.repository.refresh_tokens[token.id] = replace(token, expires_at=NOW)
    elif state == "session_expired":
        harness.repository.sessions[session.id] = replace(session, expires_at=NOW)
    else:
        harness.repository.users[user.id] = replace(user, disabled_at=NOW)

    with pytest.raises(SessionExpired):
        await harness.service.refresh(
            raw_refresh_token=registered.refresh_token,
            csrf_token=registered.csrf_token,
            metadata=METADATA,
        )


async def test_logout_is_idempotent_and_can_fall_back_to_refresh(
    harness: ServiceHarness,
) -> None:
    registered = await harness.register()

    await harness.service.logout(
        raw_access_token="bad-access",
        raw_refresh_token=registered.refresh_token,
        csrf_token=registered.csrf_token,
    )
    assert harness.repository.sessions[registered.identity.session.id].revocation_reason == "logout"

    await harness.service.logout(
        raw_access_token=registered.access_token,
        raw_refresh_token=registered.refresh_token,
        csrf_token=None,
    )
    assert harness.repository.rollback_count == 1


async def test_logout_can_revoke_from_consumed_refresh_history(
    harness: ServiceHarness,
) -> None:
    registered = await harness.register()
    harness.clock.value += timedelta(minutes=5)
    await harness.service.refresh(
        raw_refresh_token=registered.refresh_token,
        csrf_token=registered.csrf_token,
        metadata=METADATA,
    )

    await harness.service.logout(
        raw_access_token="expired-access",
        raw_refresh_token=registered.refresh_token,
        csrf_token=registered.csrf_token,
    )

    assert harness.repository.sessions[registered.identity.session.id].revocation_reason == "logout"


async def test_logout_requires_csrf_for_valid_session(harness: ServiceHarness) -> None:
    registered = await harness.register()
    with pytest.raises(CsrfValidationFailed):
        await harness.service.logout(
            raw_access_token=registered.access_token,
            raw_refresh_token=registered.refresh_token,
            csrf_token="wrong",
        )


async def test_session_listing_revoke_and_logout_all(harness: ServiceHarness) -> None:
    first = await harness.register()
    second = await harness.service.login(
        email=first.identity.user.email,
        password=PASSWORD,
        metadata=METADATA,
    )

    sessions = await harness.service.list_sessions(first.identity)
    assert {session.id for session in sessions} == {
        first.identity.session.id,
        second.identity.session.id,
    }

    with pytest.raises(SessionNotFound):
        await harness.service.revoke_session(
            identity=first.identity,
            session_id=uuid4(),
            csrf_token=first.csrf_token,
        )
    assert (
        await harness.service.revoke_session(
            identity=first.identity,
            session_id=second.identity.session.id,
            csrf_token=first.csrf_token,
        )
        is False
    )
    assert (
        await harness.service.revoke_session(
            identity=first.identity,
            session_id=first.identity.session.id,
            csrf_token=first.csrf_token,
        )
        is True
    )

    third = await harness.service.login(
        email=first.identity.user.email,
        password=PASSWORD,
        metadata=METADATA,
    )
    await harness.service.logout_all(
        identity=third.identity,
        csrf_token=third.csrf_token,
    )
    assert await harness.service.list_sessions(third.identity) == []


def raw_email_action(
    harness: ServiceHarness,
    purpose: EmailActionPurpose,
) -> str:
    outbox = [item for item in harness.repository.outbox.values() if item.purpose is purpose][-1]
    return harness.action_tokens.open_email_action(
        encrypted_payload=outbox.encrypted_payload,
        recipient=outbox.recipient,
        purpose=outbox.purpose,
    )


async def test_shared_authentication_throttle_consumes_subject_and_ip_buckets(
    harness: ServiceHarness,
) -> None:
    harness.repository.rate_limit_retry = 37

    with pytest.raises(AuthenticationRateLimited) as exc_info:
        await harness.register()

    assert exc_info.value.retry_after_seconds == 37
    assert harness.repository.users == {}
    assert harness.repository.commit_count == 1
    requests = harness.repository.rate_limit_requests[-1]
    assert {item.scope for item in requests} == {"registration:subject", "registration:ip"}
    assert all("person@example.com" not in item.subject_hash for item in requests)


async def test_login_enforces_active_session_cap() -> None:
    harness = ServiceHarness(max_active_sessions=2)
    registered = await harness.register()
    first_login = await harness.service.login(
        email=registered.identity.user.email,
        password=PASSWORD,
        metadata=METADATA,
    )
    second_login = await harness.service.login(
        email=registered.identity.user.email,
        password=PASSWORD,
        metadata=METADATA,
    )

    active = await harness.repository.list_active_sessions(
        user_id=registered.identity.user.id,
        now=NOW,
    )
    assert len(active) == 2
    assert second_login.identity.session.id in {item.id for item in active}
    revoked = [item for item in harness.repository.sessions.values() if item.revoked_at is not None]
    assert len(revoked) == 1
    assert revoked[0].revocation_reason == "session_limit"
    assert first_login.identity.session.id in {item.id for item in active} or (
        registered.identity.session.id in {item.id for item in active}
    )


async def test_email_verification_replacement_and_single_use(harness: ServiceHarness) -> None:
    registered = await harness.register()
    original = raw_email_action(harness, EmailActionPurpose.VERIFY_EMAIL)
    original_id = harness.action_tokens.present(
        original,
        EmailActionPurpose.VERIFY_EMAIL,
    ).token_id

    await harness.service.request_email_verification(
        identity=registered.identity,
        csrf_token=registered.csrf_token,
        metadata=METADATA,
    )
    replacement = raw_email_action(harness, EmailActionPurpose.VERIFY_EMAIL)
    assert replacement != original
    assert harness.repository.email_actions[original_id].revoked_at == NOW

    await harness.service.complete_email_verification(
        raw_token=replacement,
        metadata=METADATA,
    )
    user = harness.repository.users[registered.identity.user.id]
    assert user.email_verified_at == NOW

    with pytest.raises(InvalidEmailActionToken):
        await harness.service.complete_email_verification(
            raw_token=replacement,
            metadata=METADATA,
        )


async def test_password_recovery_is_generic_and_revokes_all_sessions(
    harness: ServiceHarness,
) -> None:
    await harness.service.request_password_recovery(
        email="missing@example.com",
        metadata=METADATA,
    )
    assert harness.repository.outbox == {}

    registered = await harness.register()
    await harness.service.login(
        email=registered.identity.user.email,
        password=PASSWORD,
        metadata=METADATA,
    )
    await harness.service.request_password_recovery(
        email=registered.identity.user.email,
        metadata=METADATA,
    )
    recovery = raw_email_action(harness, EmailActionPurpose.RECOVER_PASSWORD)
    assert recovery not in "".join(
        item.encrypted_payload for item in harness.repository.outbox.values()
    )

    new_password = "a completely new correct horse password"
    await harness.service.complete_password_recovery(
        raw_token=recovery,
        new_password=new_password,
        metadata=METADATA,
    )

    user = harness.repository.users[registered.identity.user.id]
    assert user.email_verified
    assert all(
        item.revocation_reason == "password_recovery"
        for item in harness.repository.sessions.values()
    )
    assert harness.passwords.verify(
        new_password,
        harness.repository.credentials[user.id].password_hash,
    ).valid

"""FK-backed unit tests for identity repository ordering and state transitions.

SQLite is used only for fast ORM/unit-of-work checks. PostgreSQL integration tests cover the
actual row-locking and migration contracts.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from klack.core.db.base import Base
from klack.modules.identity.application.ports import (
    IdentityConflict,
    RateLimitRequest,
)
from klack.modules.identity.domain.entities import (
    AuthSession,
    EmailActionPurpose,
    EmailActionToken,
    EmailOutboxMessage,
    PasswordCredential,
    RefreshToken,
    User,
)
from klack.modules.identity.infrastructure.models import EmailActionTokenRecord
from klack.modules.identity.infrastructure.repository import SqlAlchemyIdentityRepository

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


@pytest.fixture
async def repository() -> AsyncIterator[SqlAlchemyIdentityRepository]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def configure_sqlite(dbapi_connection: object, _connection_record: object) -> None:
        dbapi_connection.create_function("char_length", 1, len)  # type: ignore[attr-defined]
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield SqlAlchemyIdentityRepository(session)
    await engine.dispose()


def make_user(*, email: str = "person@example.com", user_id: UUID | None = None) -> User:
    return User(
        id=user_id or uuid4(),
        email=email,
        email_verified_at=None,
        created_at=NOW,
        disabled_at=None,
    )


def make_session(user_id: UUID, *, session_id: UUID | None = None, offset: int = 0) -> AuthSession:
    created_at = NOW + timedelta(minutes=offset)
    return AuthSession(
        id=session_id or uuid4(),
        user_id=user_id,
        csrf_token_hash="c" * 64,
        created_at=created_at,
        last_seen_at=created_at,
        expires_at=created_at + timedelta(days=30),
        revoked_at=None,
        revocation_reason=None,
        created_ip="192.0.2.1",
        last_ip="192.0.2.1",
        user_agent="Test Browser",
    )


def make_refresh(
    session_id: UUID,
    *,
    token_id: UUID | None = None,
    token_hash: str | None = None,
    offset: int = 0,
) -> RefreshToken:
    created_at = NOW + timedelta(minutes=offset)
    return RefreshToken(
        id=token_id or uuid4(),
        session_id=session_id,
        token_hash=token_hash or uuid4().hex + uuid4().hex,
        created_at=created_at,
        expires_at=created_at + timedelta(days=30),
        used_at=None,
        revoked_at=None,
        replaced_by_token_id=None,
    )


def make_email_delivery(user: User) -> tuple[EmailActionToken, EmailOutboxMessage]:
    token = EmailActionToken(
        id=uuid4(),
        user_id=user.id,
        purpose=EmailActionPurpose.VERIFY_EMAIL,
        token_hash=uuid4().hex + uuid4().hex,
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
        used_at=None,
        revoked_at=None,
    )
    return token, EmailOutboxMessage(
        id=uuid4(),
        action_token_id=token.id,
        recipient=user.email,
        purpose=token.purpose,
        encrypted_payload="encrypted",
        created_at=NOW,
        available_at=NOW,
    )


async def add_registration(
    repository: SqlAlchemyIdentityRepository,
    *,
    email: str = "person@example.com",
) -> tuple[User, AuthSession, RefreshToken]:
    user = make_user(email=email)
    session = make_session(user.id)
    refresh = make_refresh(session.id)
    email_action, outbox = make_email_delivery(user)
    await repository.add_registration(
        user,
        PasswordCredential(
            user_id=user.id,
            password_hash="$argon2id$test",
            password_changed_at=NOW,
        ),
        session,
        refresh,
        email_action,
        outbox,
    )
    await repository.commit()
    return user, session, refresh


async def test_registration_flushes_fk_dependencies_and_loads_login(
    repository: SqlAlchemyIdentityRepository,
) -> None:
    user, session, _refresh = await add_registration(repository)

    assert await repository.email_exists(user.email)
    assert not await repository.email_exists("missing@example.com")
    login = await repository.get_login(user.email)
    assert login is not None
    assert login.user.id == user.id
    assert login.credential.password_hash == "$argon2id$test"
    identity = await repository.get_active_session(
        session_id=session.id,
        user_id=user.id,
        now=NOW,
    )
    assert identity is not None
    assert identity.user.email == user.email
    assert identity.session.user_agent == "Test Browser"
    assert (
        await repository.get_active_session(
            session_id=uuid4(),
            user_id=user.id,
            now=NOW,
        )
        is None
    )


async def test_duplicate_email_flush_is_translated_and_rolled_back(
    repository: SqlAlchemyIdentityRepository,
) -> None:
    await add_registration(repository)
    duplicate_user = make_user(email="person@example.com")
    duplicate_session = make_session(duplicate_user.id)
    email_action, outbox = make_email_delivery(duplicate_user)

    with pytest.raises(IdentityConflict):
        await repository.add_registration(
            duplicate_user,
            PasswordCredential(
                user_id=duplicate_user.id,
                password_hash="$argon2id$duplicate",
                password_changed_at=NOW,
            ),
            duplicate_session,
            make_refresh(duplicate_session.id),
            email_action,
            outbox,
        )
    assert await repository.email_exists("person@example.com")


async def test_login_hash_update_and_disabled_user_filter(
    repository: SqlAlchemyIdentityRepository,
) -> None:
    user, session, _refresh = await add_registration(repository)
    await repository.update_password_hash(
        user_id=user.id,
        password_hash="$argon2id$updated",
        changed_at=NOW + timedelta(minutes=1),
    )
    await repository.commit()
    login = await repository.get_login(user.email)
    assert login is not None
    assert login.credential.password_hash == "$argon2id$updated"

    raw_session: AsyncSession = repository._session
    user_record = await raw_session.get(
        __import__(
            "klack.modules.identity.infrastructure.models",
            fromlist=["UserRecord"],
        ).UserRecord,
        user.id,
    )
    user_record.disabled_at = NOW
    await repository.commit()
    assert (
        await repository.get_active_session(
            session_id=session.id,
            user_id=user.id,
            now=NOW,
        )
        is None
    )


async def test_add_session_and_refresh_rotation_preserve_fk_order(
    repository: SqlAlchemyIdentityRepository,
) -> None:
    user, _first_session, _first_refresh = await add_registration(repository)
    session = make_session(user.id, offset=1)
    original = make_refresh(session.id, offset=1)
    await repository.add_session(session, original)
    await repository.commit()

    context = await repository.get_refresh_context(original.id, for_update=True)
    assert context is not None
    assert context.user.id == user.id
    assert context.token.id == original.id
    assert await repository.get_refresh_context(uuid4(), for_update=False) is None

    replacement = make_refresh(session.id, offset=2)
    used_at = NOW + timedelta(minutes=2)
    await repository.rotate_refresh(
        previous_token_id=original.id,
        replacement=replacement,
        used_at=used_at,
        last_ip="198.51.100.9",
    )
    await repository.commit()

    old_context = await repository.get_refresh_context(original.id, for_update=False)
    new_context = await repository.get_refresh_context(replacement.id, for_update=False)
    assert old_context is not None and new_context is not None
    assert old_context.token.used_at is not None
    assert old_context.token.replaced_by_token_id == replacement.id
    assert new_context.session.last_ip == "198.51.100.9"


async def test_rotation_rejects_disappeared_state(
    repository: SqlAlchemyIdentityRepository,
) -> None:
    replacement = make_refresh(uuid4())
    with pytest.raises(RuntimeError, match="disappeared"):
        await repository.rotate_refresh(
            previous_token_id=uuid4(),
            replacement=replacement,
            used_at=NOW,
            last_ip=None,
        )


async def test_session_listing_individual_revoke_and_revoke_all(
    repository: SqlAlchemyIdentityRepository,
) -> None:
    user, first, _refresh = await add_registration(repository)
    second = make_session(user.id, offset=1)
    second_refresh = make_refresh(second.id, offset=1)
    await repository.add_session(second, second_refresh)
    await repository.commit()

    sessions = await repository.list_active_sessions(user_id=user.id, now=NOW)
    assert [item.id for item in sessions] == [second.id, first.id]
    assert not await repository.revoke_session(
        session_id=uuid4(),
        user_id=user.id,
        revoked_at=NOW,
        reason="missing",
    )
    assert not await repository.revoke_session(
        session_id=second.id,
        user_id=uuid4(),
        revoked_at=NOW,
        reason="wrong_owner",
    )
    assert await repository.revoke_session(
        session_id=second.id,
        user_id=user.id,
        revoked_at=NOW,
        reason="user_revoked",
    )
    assert await repository.revoke_session(
        session_id=second.id,
        user_id=user.id,
        revoked_at=NOW,
        reason="user_revoked",
    )
    await repository.commit()

    second_context = await repository.get_refresh_context(second_refresh.id, for_update=False)
    assert second_context is not None
    assert second_context.session.revocation_reason == "user_revoked"
    assert second_context.token.revoked_at is not None

    assert (
        await repository.revoke_all_sessions(
            user_id=user.id,
            revoked_at=NOW,
            reason="logout_all",
        )
        == 1
    )
    await repository.commit()
    assert await repository.list_active_sessions(user_id=user.id, now=NOW) == []


async def test_commit_translates_deferred_unique_conflict_and_explicit_rollback(
    repository: SqlAlchemyIdentityRepository,
) -> None:
    user, _session, existing = await add_registration(repository)
    second = make_session(user.id, offset=1)
    await repository.add_session(
        second,
        make_refresh(second.id, token_hash=existing.token_hash, offset=1),
    )
    with pytest.raises(IdentityConflict):
        await repository.commit()

    await repository.rollback()
    assert await repository.list_active_sessions(user_id=user.id, now=NOW) != []


async def test_shared_rate_limits_block_both_durable_buckets(
    repository: SqlAlchemyIdentityRepository,
) -> None:
    rate_now = NOW.replace(tzinfo=None)  # SQLite does not preserve timezone metadata.
    requests = [
        RateLimitRequest(scope="login:subject", subject_hash="s" * 64, limit=2),
        RateLimitRequest(scope="login:ip", subject_hash="i" * 64, limit=2),
    ]
    for _attempt in range(2):
        assert (
            await repository.consume_rate_limits(
                requests,
                now=rate_now,
                window=timedelta(minutes=1),
                block_for=timedelta(minutes=1),
            )
            is None
        )
        await repository.commit()

    assert (
        await repository.consume_rate_limits(
            requests,
            now=rate_now,
            window=timedelta(minutes=1),
            block_for=timedelta(minutes=1),
        )
        == 60
    )
    await repository.commit()
    assert (
        await repository.consume_rate_limits(
            requests,
            now=rate_now + timedelta(seconds=1),
            window=timedelta(minutes=1),
            block_for=timedelta(minutes=1),
        )
        == 59
    )


async def test_active_session_cap_revokes_oldest_refresh_family(
    repository: SqlAlchemyIdentityRepository,
) -> None:
    user, first, first_refresh = await add_registration(repository)
    second = make_session(user.id, offset=1)
    second_refresh = make_refresh(second.id, offset=1)
    third = make_session(user.id, offset=2)
    third_refresh = make_refresh(third.id, offset=2)
    await repository.add_session(second, second_refresh)
    await repository.add_session(third, third_refresh)
    await repository.commit()

    assert (
        await repository.enforce_active_session_cap(
            user_id=user.id,
            now=NOW,
            retain_active=2,
            reason="session_limit",
        )
        == 1
    )
    await repository.commit()

    active = await repository.list_active_sessions(user_id=user.id, now=NOW)
    assert {item.id for item in active} == {second.id, third.id}
    first_context = await repository.get_refresh_context(first_refresh.id, for_update=False)
    assert first_context is not None
    assert first_context.session.id == first.id
    assert first_context.session.revocation_reason == "session_limit"
    assert first_context.token.revoked_at is not None


async def test_email_action_replacement_use_and_verification_are_durable(
    repository: SqlAlchemyIdentityRepository,
) -> None:
    user, _session, _refresh = await add_registration(repository)
    raw_session = repository._session
    original = await raw_session.scalar(select(EmailActionTokenRecord))
    assert original is not None
    replacement, outbox = make_email_delivery(user)

    await repository.replace_email_action(
        token=replacement,
        outbox_message=outbox,
        replaced_at=NOW + timedelta(minutes=1),
    )
    await repository.commit()
    original_context = await repository.get_email_action_context(
        original.id,
        purpose=EmailActionPurpose.VERIFY_EMAIL,
        for_update=False,
    )
    replacement_context = await repository.get_email_action_context(
        replacement.id,
        purpose=EmailActionPurpose.VERIFY_EMAIL,
        for_update=True,
    )
    assert original_context is not None
    assert original_context.token.revoked_at is not None
    assert replacement_context is not None

    used_at = NOW + timedelta(minutes=2)
    await repository.mark_email_verified(user.id, verified_at=used_at)
    await repository.mark_email_action_used(replacement.id, used_at=used_at)
    await repository.revoke_email_actions(
        user_id=user.id,
        purpose=EmailActionPurpose.VERIFY_EMAIL,
        revoked_at=used_at,
        exclude_token_id=replacement.id,
    )
    await repository.commit()
    context = await repository.get_email_action_context(
        replacement.id,
        purpose=EmailActionPurpose.VERIFY_EMAIL,
        for_update=False,
    )
    assert context is not None
    assert context.user.email_verified_at is not None
    assert context.token.used_at is not None

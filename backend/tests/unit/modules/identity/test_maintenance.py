"""Transactional email leasing, retry, and global cleanup contracts."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from klack.core.db.base import Base
from klack.modules.identity.domain.entities import EmailActionPurpose
from klack.modules.identity.infrastructure.action_security import ActionTokenManager
from klack.modules.identity.infrastructure.maintenance import (
    IdentityMaintenanceRepository,
    IdentityMaintenanceService,
)
from klack.modules.identity.infrastructure.models import (
    AuthRateLimitRecord,
    AuthSessionRecord,
    EmailActionTokenRecord,
    EmailOutboxRecord,
    RefreshTokenRecord,
    UserRecord,
)

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
USER_ID = UUID("11111111-1111-4111-8111-111111111111")


@dataclass
class MutableClock:
    value: datetime = NOW

    def __call__(self) -> datetime:
        return self.value


class CapturingSender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []
        self.failure: Exception | None = None

    async def send(self, *, recipient: str, subject: str, text_body: str) -> None:
        if self.failure is not None:
            raise self.failure
        self.messages.append((recipient, subject, text_body))


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
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
    async with factory() as request_session:
        yield request_session
    await engine.dispose()


async def add_user(session: AsyncSession) -> None:
    session.add(
        UserRecord(
            id=USER_ID,
            email="person@example.com",
            email_verified_at=None,
            created_at=NOW - timedelta(days=40),
            disabled_at=None,
        ),
    )
    await session.commit()


async def queue_action(
    session: AsyncSession,
    manager: ActionTokenManager,
    *,
    purpose: EmailActionPurpose = EmailActionPurpose.VERIFY_EMAIL,
    created_at: datetime = NOW,
    expires_at: datetime | None = None,
    sent_at: datetime | None = None,
) -> tuple[str, UUID]:
    issued = manager.issue(purpose)
    action = EmailActionTokenRecord(
        id=issued.token_id,
        user_id=USER_ID,
        purpose=purpose,
        token_hash=issued.digest,
        created_at=created_at,
        expires_at=expires_at or created_at + timedelta(days=1),
        used_at=None,
        revoked_at=None,
    )
    session.add(action)
    await session.flush()
    message_id = uuid4()
    session.add(
        EmailOutboxRecord(
            id=message_id,
            action_token_id=action.id,
            recipient="person@example.com",
            purpose=purpose,
            encrypted_payload=manager.seal_email_action(
                raw_token=issued.raw,
                recipient="person@example.com",
                purpose=purpose,
            ),
            created_at=created_at,
            available_at=created_at,
            attempts=0,
            sent_at=sent_at,
            lease_id=None,
            leased_until=None,
            last_failure_code=None,
        ),
    )
    await session.commit()
    return issued.raw, message_id


async def test_email_delivery_claims_once_and_marks_sent(session: AsyncSession) -> None:
    await add_user(session)
    manager = ActionTokenManager(secret="action-secret-at-least-thirty-two-characters")
    raw_token, message_id = await queue_action(session, manager)
    sender = CapturingSender()
    service = IdentityMaintenanceService(
        repository=IdentityMaintenanceRepository(session),
        action_tokens=manager,
        sender=sender,
        public_web_origin="https://app.example",
        clock=lambda: NOW,
    )

    assert await service.deliver_batch(batch_size=10, lease_for=timedelta(minutes=5)) == (1, 0)
    assert await service.deliver_batch(batch_size=10, lease_for=timedelta(minutes=5)) == (0, 0)

    assert sender.messages[0][0] == "person@example.com"
    assert sender.messages[0][1] == "Verify your Klack email"
    assert raw_token in sender.messages[0][2]
    record = await session.get(EmailOutboxRecord, message_id)
    assert record is not None
    assert record.sent_at is not None
    assert record.sent_at.replace(tzinfo=UTC) == NOW
    assert record.lease_id is None


async def test_email_delivery_failure_releases_lease_with_backoff(
    session: AsyncSession,
) -> None:
    await add_user(session)
    manager = ActionTokenManager(secret="action-secret-at-least-thirty-two-characters")
    _raw_token, message_id = await queue_action(
        session,
        manager,
        purpose=EmailActionPurpose.RECOVER_PASSWORD,
    )
    sender = CapturingSender()
    sender.failure = RuntimeError("provider details must not be persisted")
    clock = MutableClock()
    service = IdentityMaintenanceService(
        repository=IdentityMaintenanceRepository(session),
        action_tokens=manager,
        sender=sender,
        public_web_origin="https://app.example",
        clock=clock,
    )

    assert await service.deliver_batch(batch_size=10, lease_for=timedelta(minutes=5)) == (0, 1)
    record = await session.get(EmailOutboxRecord, message_id)
    assert record is not None
    assert record.attempts == 1
    assert record.available_at.replace(tzinfo=UTC) == NOW + timedelta(seconds=30)
    assert record.last_failure_code == "RuntimeError"
    assert record.lease_id is None
    assert "provider details" not in record.last_failure_code

    assert await service.deliver_batch(batch_size=10, lease_for=timedelta(minutes=5)) == (0, 0)
    sender.failure = None
    clock.value += timedelta(seconds=31)
    assert await service.deliver_batch(batch_size=10, lease_for=timedelta(minutes=5)) == (1, 0)
    assert "recover-password" in sender.messages[0][2]


async def test_cleanup_drains_expired_global_state_in_bounded_batches(
    session: AsyncSession,
) -> None:
    await add_user(session)
    manager = ActionTokenManager(secret="action-secret-at-least-thirty-two-characters")
    await queue_action(
        session,
        manager,
        created_at=NOW - timedelta(days=10),
        expires_at=NOW - timedelta(days=9),
        sent_at=NOW - timedelta(days=9),
    )
    await queue_action(session, manager)

    old_session_id = uuid4()
    session.add(
        AuthSessionRecord(
            id=old_session_id,
            user_id=USER_ID,
            csrf_token_hash="c" * 64,
            created_at=NOW - timedelta(days=40),
            last_seen_at=NOW - timedelta(days=40),
            expires_at=NOW - timedelta(days=10),
            revoked_at=None,
            revocation_reason=None,
            created_ip=None,
            last_ip=None,
            user_agent=None,
        ),
    )
    await session.flush()
    session.add(
        RefreshTokenRecord(
            id=uuid4(),
            session_id=old_session_id,
            token_hash="r" * 64,
            created_at=NOW - timedelta(days=40),
            expires_at=NOW - timedelta(days=10),
            used_at=None,
            revoked_at=None,
            replaced_by_token_id=None,
        ),
    )
    session.add(
        AuthRateLimitRecord(
            scope="login:ip",
            subject_hash="h" * 64,
            window_started_at=NOW - timedelta(days=10),
            attempts=1,
            blocked_until=None,
            updated_at=NOW - timedelta(days=10),
        ),
    )
    await session.commit()

    service = IdentityMaintenanceService(
        repository=IdentityMaintenanceRepository(session),
        action_tokens=manager,
        sender=CapturingSender(),
        public_web_origin="https://app.example",
        clock=lambda: NOW,
    )
    counts = await service.cleanup(retention=timedelta(days=7), batch_size=1)

    assert counts.outbox_messages == 1
    assert counts.email_actions == 1
    assert counts.sessions == 1
    assert counts.rate_limits == 1
    assert await session.scalar(select(func.count()).select_from(RefreshTokenRecord)) == 0
    assert await session.scalar(select(func.count()).select_from(EmailActionTokenRecord)) == 1
    assert await session.scalar(select(func.count()).select_from(EmailOutboxRecord)) == 1

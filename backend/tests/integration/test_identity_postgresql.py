"""PostgreSQL identity lifecycle and refresh-rotation integration checks."""

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from klack.core.config import AppEnvironment, Settings
from klack.core.container import AppContainer, build_container
from klack.modules.identity.application.ports import RateLimitRequest
from klack.modules.identity.application.service import AuthenticationResult, IdentityService
from klack.modules.identity.domain.entities import SessionMetadata
from klack.modules.identity.domain.errors import (
    AuthenticationRequired,
    RefreshTokenReuseDetected,
)
from klack.modules.identity.infrastructure.maintenance import (
    IdentityMaintenanceRepository,
    IdentityMaintenanceService,
)
from klack.modules.identity.infrastructure.models import (
    AuthRateLimitRecord,
    AuthSessionRecord,
    RefreshTokenRecord,
    UserRecord,
)
from klack.modules.identity.infrastructure.repository import SqlAlchemyIdentityRepository

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 with a migrated PostgreSQL test database",
    ),
]

PASSWORD = "correct horse battery staple"
METADATA = SessionMetadata(ip_address="192.0.2.10", user_agent="PostgreSQL integration test")


@dataclass
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value


def _settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env=AppEnvironment.TEST,
        auth_jwt_secret=SecretStr("integration-jwt-secret-at-least-thirty-two-bytes"),
        auth_refresh_secret=SecretStr("integration-refresh-secret-at-least-thirty-two-bytes"),
        auth_action_secret=SecretStr("integration-action-secret-at-least-thirty-two-bytes"),
        auth_refresh_min_interval_seconds=300,
        auth_trusted_origin="http://test",
        auth_public_web_origin="http://test",
        auth_cookie_secure=False,
    )


@pytest.fixture(scope="module")
def migrated_schema() -> None:
    """Bring the explicitly configured test database to the repository head."""
    backend_root = Path(__file__).parents[2]
    command.upgrade(Config(backend_root / "alembic.ini"), "head")


@pytest.fixture
async def identity_container(migrated_schema: None) -> AsyncIterator[AppContainer]:
    """Build the real process dependencies after Alembic has created the schema."""
    del migrated_schema
    container = build_container(_settings())
    try:
        yield container
    finally:
        await container.engine.dispose()


def _service(
    container: AppContainer,
    session: AsyncSession,
    clock: MutableClock,
) -> IdentityService:
    return IdentityService(
        repository=SqlAlchemyIdentityRepository(session),
        passwords=container.password_manager,
        access_tokens=container.access_token_codec,
        session_tokens=container.session_token_manager,
        action_tokens=container.action_token_manager,
        policy=container.identity_policy,
        clock=clock,
    )


async def _register(
    container: AppContainer,
    clock: MutableClock,
) -> AuthenticationResult:
    async with container.session_factory() as session:
        return await _service(container, session, clock).register(
            email=f"identity-integration+{uuid4().hex}@example.com",
            password=PASSWORD,
            metadata=METADATA,
        )


async def _refresh(
    container: AppContainer,
    clock: MutableClock,
    *,
    refresh_token: str,
    csrf_token: str,
    ip_address: str,
) -> AuthenticationResult:
    """Run one refresh through its own request-scoped database session."""
    async with container.session_factory() as session:
        return await _service(container, session, clock).refresh(
            raw_refresh_token=refresh_token,
            csrf_token=csrf_token,
            metadata=SessionMetadata(ip_address=ip_address, user_agent=METADATA.user_agent),
        )


async def _delete_user(container: AppContainer, user_id: UUID) -> None:
    async with container.session_factory() as session:
        await session.execute(delete(UserRecord).where(UserRecord.id == user_id))
        await session.commit()


async def test_registration_rotation_and_consumed_token_replay_revoke_family(
    identity_container: AppContainer,
) -> None:
    clock = MutableClock(datetime.now(UTC))
    registered = await _register(identity_container, clock)
    clock.value += timedelta(minutes=5)
    user_id = registered.identity.user.id
    session_id = registered.identity.session.id
    original = identity_container.session_token_manager.present_refresh(
        registered.refresh_token,
    )

    try:
        rotated = await _refresh(
            identity_container,
            clock,
            refresh_token=registered.refresh_token,
            csrf_token=registered.csrf_token,
            ip_address="198.51.100.20",
        )
        successor = identity_container.session_token_manager.present_refresh(
            rotated.refresh_token,
        )

        async with identity_container.session_factory() as replay_session:
            with pytest.raises(RefreshTokenReuseDetected):
                await _service(identity_container, replay_session, clock).refresh(
                    raw_refresh_token=registered.refresh_token,
                    csrf_token=registered.csrf_token,
                    metadata=METADATA,
                )

        async with identity_container.session_factory() as request_session:
            with pytest.raises(AuthenticationRequired):
                await _service(
                    identity_container,
                    request_session,
                    clock,
                ).authenticate_access(rotated.access_token)

        async with identity_container.session_factory() as assertion_session:
            durable_session = await assertion_session.get(AuthSessionRecord, session_id)
            original_record = await assertion_session.get(RefreshTokenRecord, original.token_id)
            successor_record = await assertion_session.get(
                RefreshTokenRecord,
                successor.token_id,
            )

        assert registered.identity.user.email.startswith("identity-integration+")
        assert durable_session is not None
        assert durable_session.revocation_reason == "refresh_reuse"
        assert durable_session.revoked_at is not None
        assert original_record is not None
        assert original_record.used_at is not None
        assert original_record.replaced_by_token_id == successor.token_id
        assert successor_record is not None
        assert successor_record.revoked_at is not None
    finally:
        await _delete_user(identity_container, user_id)


async def test_concurrent_refreshes_use_separate_sessions_and_revoke_on_replay(
    identity_container: AppContainer,
) -> None:
    clock = MutableClock(datetime.now(UTC))
    registered = await _register(identity_container, clock)
    clock.value += timedelta(minutes=5)
    user_id = registered.identity.user.id
    session_id = registered.identity.session.id

    try:
        outcomes = await asyncio.gather(
            _refresh(
                identity_container,
                clock,
                refresh_token=registered.refresh_token,
                csrf_token=registered.csrf_token,
                ip_address="198.51.100.30",
            ),
            _refresh(
                identity_container,
                clock,
                refresh_token=registered.refresh_token,
                csrf_token=registered.csrf_token,
                ip_address="198.51.100.31",
            ),
            return_exceptions=True,
        )
        successes = [item for item in outcomes if isinstance(item, AuthenticationResult)]
        replay_failures = [item for item in outcomes if isinstance(item, RefreshTokenReuseDetected)]
        unexpected = [
            item
            for item in outcomes
            if not isinstance(item, (AuthenticationResult, RefreshTokenReuseDetected))
        ]

        assert unexpected == []
        assert len(successes) == 1
        assert len(replay_failures) == 1

        winner = successes[0]
        winner_refresh = identity_container.session_token_manager.present_refresh(
            winner.refresh_token,
        )
        async with identity_container.session_factory() as assertion_session:
            durable_session = await assertion_session.get(AuthSessionRecord, session_id)
            winner_record = await assertion_session.get(
                RefreshTokenRecord,
                winner_refresh.token_id,
            )

        assert durable_session is not None
        assert durable_session.revocation_reason == "refresh_reuse"
        assert durable_session.revoked_at is not None
        assert winner_record is not None
        assert winner_record.revoked_at is not None

        async with identity_container.session_factory() as request_session:
            with pytest.raises(AuthenticationRequired):
                await _service(
                    identity_container,
                    request_session,
                    clock,
                ).authenticate_access(winner.access_token)
    finally:
        await _delete_user(identity_container, user_id)


class CapturingSender:
    def __init__(self) -> None:
        self.recipients: list[str] = []

    async def send(self, *, recipient: str, subject: str, text_body: str) -> None:
        del subject, text_body
        self.recipients.append(recipient)


async def test_concurrent_shared_rate_limit_consumption_is_serialized(
    identity_container: AppContainer,
) -> None:
    scope = f"integration:{uuid4().hex[:12]}"
    subject_hash = identity_container.action_token_manager.rate_limit_key(
        scope,
        "shared-subject",
    )
    request = RateLimitRequest(scope=scope, subject_hash=subject_hash, limit=1)
    now = datetime.now(UTC)

    async def consume() -> int | None:
        async with identity_container.session_factory() as session:
            repository = SqlAlchemyIdentityRepository(session)
            result = await repository.consume_rate_limits(
                [request],
                now=now,
                window=timedelta(minutes=1),
                block_for=timedelta(minutes=1),
            )
            await repository.commit()
            return result

    try:
        outcomes = await asyncio.gather(consume(), consume())
        assert outcomes.count(None) == 1
        assert outcomes.count(60) == 1
    finally:
        async with identity_container.session_factory() as session:
            await session.execute(
                delete(AuthRateLimitRecord).where(AuthRateLimitRecord.scope == scope),
            )
            await session.commit()


async def test_concurrent_outbox_workers_deliver_each_action_once(
    identity_container: AppContainer,
) -> None:
    clock = MutableClock(datetime.now(UTC))
    registered = await _register(identity_container, clock)
    sender = CapturingSender()

    async def deliver() -> tuple[int, int]:
        async with identity_container.session_factory() as session:
            service = IdentityMaintenanceService(
                repository=IdentityMaintenanceRepository(session),
                action_tokens=identity_container.action_token_manager,
                sender=sender,
                public_web_origin=identity_container.settings.auth_public_web_origin,
                clock=clock,
            )
            return await service.deliver_batch(
                batch_size=1,
                lease_for=timedelta(minutes=5),
            )

    try:
        outcomes = await asyncio.gather(deliver(), deliver())
        assert sum(sent for sent, _failed in outcomes) == 1
        assert sum(failed for _sent, failed in outcomes) == 0
        assert sender.recipients == [registered.identity.user.email]
    finally:
        await _delete_user(identity_container, registered.identity.user.id)

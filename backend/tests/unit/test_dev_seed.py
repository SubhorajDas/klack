"""Development fixture safety, idempotency, and transaction tests."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from klack import dev_seed
from klack.core.config import AppEnvironment, LogFormat, Settings
from klack.core.db.base import Base
from klack.dev_seed import (
    DEMO_ACCOUNTS,
    DEMO_WORKSPACE_ID,
    DEMO_WORKSPACE_NAME,
    DevelopmentSeedError,
    DevelopmentSeedResult,
    DevelopmentSeedSettings,
    seed_development_data,
)
from klack.modules.identity.infrastructure.models import (
    AuthRateLimitRecord,
    AuthSessionRecord,
    EmailActionTokenRecord,
    EmailOutboxRecord,
    PasswordCredentialRecord,
    RefreshTokenRecord,
    UserRecord,
)
from klack.modules.identity.infrastructure.security import PasswordVerification
from klack.modules.workspaces.infrastructure.models import (
    InvitationRecord,
    MembershipRecord,
    WorkspaceRecord,
)

PASSWORD = "Swagger fixture password"
NOW = datetime(2026, 9, 11, 8, 30, tzinfo=UTC)


class FakePasswordManager:
    """Fast salted-hash stand-in with the production manager's public contract."""

    def __init__(self) -> None:
        self.counter = 0
        self.password_by_hash: dict[str, str] = {}

    async def hash_async(self, password: str) -> str:
        self.counter += 1
        digest = sha256(f"{self.counter}:{password}".encode()).hexdigest()
        password_hash = f"test-hash${self.counter}${digest}"
        self.password_by_hash[password_hash] = password
        return password_hash

    async def verify_async(
        self,
        password: str,
        password_hash: str,
    ) -> PasswordVerification:
        return PasswordVerification(valid=self.password_by_hash.get(password_hash) == password)


@pytest.fixture
async def seed_sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def configure_sqlite(dbapi_connection: object, _connection_record: object) -> None:
        dbapi_connection.create_function("char_length", 1, len)  # type: ignore[attr-defined]
        dbapi_connection.execute("PRAGMA foreign_keys = ON")  # type: ignore[attr-defined]

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


def make_seed_settings(settings: Settings, **updates: object) -> DevelopmentSeedSettings:
    values = settings.model_dump()
    values.update(
        {
            "app_env": AppEnvironment.DEVELOPMENT,
            "dev_seed_enabled": True,
            "dev_seed_password": PASSWORD,
            **updates,
        },
    )
    if values["app_env"] in {AppEnvironment.STAGING, AppEnvironment.PRODUCTION}:
        values.update(
            {
                "log_format": LogFormat.JSON,
                "auth_cookie_secure": True,
                "auth_trusted_origin": "https://test.example",
                "auth_public_web_origin": "https://test.example",
            },
        )
    return DevelopmentSeedSettings.model_validate(values)


async def row_count(session: AsyncSession, record_type: type[Base]) -> int:
    count = await session.scalar(select(func.count()).select_from(record_type))
    assert count is not None
    return count


async def test_seed_creates_exact_fixture_and_is_idempotent(
    seed_sessions: async_sessionmaker[AsyncSession],
) -> None:
    passwords = FakePasswordManager()
    async with seed_sessions() as session:
        first = await seed_development_data(
            session,
            password=PASSWORD,
            password_manager=passwords,
            now=NOW,
        )

    assert first == DevelopmentSeedResult(
        users_created=4,
        credentials_created=4,
        workspaces_created=1,
        memberships_created=3,
    )
    async with seed_sessions() as session:
        users = (await session.scalars(select(UserRecord).order_by(UserRecord.id))).all()
        credentials = (
            await session.scalars(
                select(PasswordCredentialRecord).order_by(PasswordCredentialRecord.user_id),
            )
        ).all()
        workspace = await session.get(WorkspaceRecord, DEMO_WORKSPACE_ID)
        memberships = (
            await session.scalars(
                select(MembershipRecord).order_by(MembershipRecord.user_id),
            )
        ).all()

        assert [(user.id, user.email) for user in users] == [
            (account.id, account.email) for account in DEMO_ACCOUNTS
        ]
        assert all(user.email_verified_at is None and user.disabled_at is None for user in users)
        assert len({credential.password_hash for credential in credentials}) == 4
        assert all(PASSWORD not in credential.password_hash for credential in credentials)
        assert workspace is not None
        assert workspace.name == DEMO_WORKSPACE_NAME
        assert workspace.created_by_user_id == DEMO_ACCOUNTS[0].id
        assert [(item.user_id, item.role) for item in memberships] == [
            (account.id, str(account.role)) for account in DEMO_ACCOUNTS if account.role is not None
        ]
        assert await row_count(session, AuthSessionRecord) == 0
        assert await row_count(session, RefreshTokenRecord) == 0
        assert await row_count(session, EmailActionTokenRecord) == 0
        assert await row_count(session, EmailOutboxRecord) == 0
        assert await row_count(session, AuthRateLimitRecord) == 0
        assert await row_count(session, InvitationRecord) == 0
        original_hashes = [credential.password_hash for credential in credentials]

    async with seed_sessions() as session:
        second = await seed_development_data(
            session,
            password=PASSWORD,
            password_manager=passwords,
            now=NOW + timedelta(days=1),
        )

    assert second == DevelopmentSeedResult(0, 0, 0, 0)
    async with seed_sessions() as session:
        credentials = (
            await session.scalars(
                select(PasswordCredentialRecord).order_by(PasswordCredentialRecord.user_id),
            )
        ).all()
        assert [credential.password_hash for credential in credentials] == original_hashes
        assert all(credential.created_at.replace(tzinfo=UTC) == NOW for credential in credentials)


async def test_late_workspace_conflict_rolls_back_new_identity_rows(
    seed_sessions: async_sessionmaker[AsyncSession],
) -> None:
    conflicting_owner_id = UUID("90000000-0000-4000-8000-000000000001")
    async with seed_sessions() as session:
        session.add(
            UserRecord(
                id=conflicting_owner_id,
                email="unrelated@example.com",
                email_verified_at=None,
                created_at=NOW,
                disabled_at=None,
            ),
        )
        session.add(
            WorkspaceRecord(
                id=DEMO_WORKSPACE_ID,
                name="Unrelated workspace",
                created_by_user_id=conflicting_owner_id,
                created_at=NOW,
                updated_at=NOW,
            ),
        )
        await session.commit()

    with pytest.raises(DevelopmentSeedError, match="workspace conflicts"):
        async with seed_sessions() as session:
            await seed_development_data(
                session,
                password=PASSWORD,
                password_manager=FakePasswordManager(),
                now=NOW,
            )

    async with seed_sessions() as session:
        seeded_user_count = await session.scalar(
            select(func.count())
            .select_from(UserRecord)
            .where(UserRecord.id.in_([account.id for account in DEMO_ACCOUNTS])),
        )
        assert seeded_user_count == 0
        assert await row_count(session, PasswordCredentialRecord) == 0
        assert await row_count(session, WorkspaceRecord) == 1


async def test_seed_rejects_identity_collision_without_partial_writes(
    seed_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with seed_sessions() as session:
        session.add(
            UserRecord(
                id=DEMO_ACCOUNTS[0].id,
                email="collision@example.com",
                email_verified_at=None,
                created_at=NOW,
                disabled_at=None,
            ),
        )
        await session.commit()

    with pytest.raises(DevelopmentSeedError, match="identity conflicts"):
        async with seed_sessions() as session:
            await seed_development_data(
                session,
                password=PASSWORD,
                password_manager=FakePasswordManager(),
                now=NOW,
            )

    async with seed_sessions() as session:
        assert await row_count(session, UserRecord) == 1
        assert await row_count(session, PasswordCredentialRecord) == 0
        assert await row_count(session, WorkspaceRecord) == 0


async def test_seed_rejects_a_short_password_before_database_work(
    seed_sessions: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(DevelopmentSeedError, match="12 to 128"):
        async with seed_sessions() as session:
            await seed_development_data(
                session,
                password="too-short",
                password_manager=FakePasswordManager(),
                now=NOW,
            )

    async with seed_sessions() as session:
        assert await row_count(session, UserRecord) == 0


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"app_env": AppEnvironment.PRODUCTION}, "APP_ENV=development"),
        ({"dev_seed_enabled": False}, "DEV_SEED_ENABLED=true"),
        ({"dev_seed_password": None}, "DEV_SEED_PASSWORD is required"),
    ],
)
async def test_run_refuses_unsafe_configuration_before_building_container(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    updates: dict[str, object],
    message: str,
) -> None:
    built = False

    def unexpected_build(_settings: Settings) -> object:
        nonlocal built
        built = True
        raise AssertionError("container must not be built")

    monkeypatch.setattr(dev_seed, "build_container", unexpected_build)

    with pytest.raises(DevelopmentSeedError, match=message):
        await dev_seed._run(make_seed_settings(settings, **updates))

    assert not built


@pytest.mark.parametrize("seed_fails", [False, True])
async def test_run_always_disposes_its_engine(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    seed_fails: bool,
) -> None:
    class FakeEngine:
        disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    engine = FakeEngine()
    container = SimpleNamespace(engine=engine, password_manager=FakePasswordManager())

    async def fake_seed_container(_container: object, *, password: str) -> DevelopmentSeedResult:
        assert password == PASSWORD
        if seed_fails:
            raise DevelopmentSeedError("injected failure")
        return DevelopmentSeedResult(4, 4, 1, 3)

    monkeypatch.setattr(dev_seed, "configure_logging", lambda _settings: None)
    monkeypatch.setattr(dev_seed, "build_container", lambda _settings: container)
    monkeypatch.setattr(dev_seed, "_seed_container", fake_seed_container)

    if seed_fails:
        with pytest.raises(DevelopmentSeedError, match="injected"):
            await dev_seed._run(make_seed_settings(settings))
    else:
        assert await dev_seed._run(make_seed_settings(settings)) == DevelopmentSeedResult(
            4,
            4,
            1,
            3,
        )
    assert engine.disposed


def test_result_output_never_prints_the_configured_password(
    capsys: pytest.CaptureFixture[str],
) -> None:
    dev_seed._print_result(DevelopmentSeedResult(4, 4, 1, 3))

    output = capsys.readouterr().out
    assert DEMO_WORKSPACE_NAME in output
    assert all(account.email in output for account in DEMO_ACCOUNTS)
    assert PASSWORD not in output

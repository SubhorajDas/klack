"""Explicit development fixture seeding for interactive API exploration."""

import asyncio
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Protocol
from uuid import UUID

from pydantic import Field, SecretStr
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from klack.core.config import AppEnvironment, Settings
from klack.core.container import AppContainer, build_container
from klack.core.logging import configure_logging
from klack.modules.identity.infrastructure.models import (
    PasswordCredentialRecord,
    UserRecord,
)
from klack.modules.identity.infrastructure.security import PasswordVerification
from klack.modules.workspaces.domain.entities import WorkspaceRole
from klack.modules.workspaces.infrastructure.models import MembershipRecord, WorkspaceRecord

MIN_DEVELOPMENT_PASSWORD_LENGTH = 12
MAX_DEVELOPMENT_PASSWORD_LENGTH = 128
SEED_ADVISORY_LOCK_KEY = 5_424_097_509_827_954_757
DEMO_WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000001")
DEMO_WORKSPACE_NAME = "Klack Swagger Demo"


class DevelopmentSeedSettings(Settings):
    """Normal application settings plus an explicit local-seeding opt-in."""

    dev_seed_enabled: bool = False
    dev_seed_password: (
        Annotated[
            SecretStr,
            Field(
                min_length=MIN_DEVELOPMENT_PASSWORD_LENGTH,
                max_length=MAX_DEVELOPMENT_PASSWORD_LENGTH,
            ),
        ]
        | None
    ) = None


class DevelopmentSeedError(RuntimeError):
    """The requested fixture is unsafe or conflicts with existing state."""


class SeedPasswordManager(Protocol):
    """Password operations needed by the development fixture."""

    async def hash_async(self, password: str) -> str: ...

    async def verify_async(
        self,
        password: str,
        password_hash: str,
    ) -> PasswordVerification: ...


@dataclass(frozen=True, slots=True)
class DemoAccount:
    """One deterministic local identity and its optional demo-workspace role."""

    id: UUID
    email: str
    role: WorkspaceRole | None


DEMO_ACCOUNTS = (
    DemoAccount(
        id=UUID("10000000-0000-4000-8000-000000000001"),
        email="dev.owner@klack.example",
        role=WorkspaceRole.OWNER,
    ),
    DemoAccount(
        id=UUID("10000000-0000-4000-8000-000000000002"),
        email="dev.admin@klack.example",
        role=WorkspaceRole.ADMIN,
    ),
    DemoAccount(
        id=UUID("10000000-0000-4000-8000-000000000003"),
        email="dev.member@klack.example",
        role=WorkspaceRole.MEMBER,
    ),
    DemoAccount(
        id=UUID("10000000-0000-4000-8000-000000000004"),
        email="dev.outsider@klack.example",
        role=None,
    ),
)


@dataclass(frozen=True, slots=True)
class DevelopmentSeedResult:
    """Counts created by one idempotent fixture-seeding transaction."""

    users_created: int
    credentials_created: int
    workspaces_created: int
    memberships_created: int


def _require_explicit_development_opt_in(settings: DevelopmentSeedSettings) -> str:
    if settings.app_env is not AppEnvironment.DEVELOPMENT:
        msg = "development data can only be seeded when APP_ENV=development"
        raise DevelopmentSeedError(msg)
    if not settings.dev_seed_enabled:
        msg = "set DEV_SEED_ENABLED=true to acknowledge local fixture creation"
        raise DevelopmentSeedError(msg)
    if settings.dev_seed_password is None:
        msg = "DEV_SEED_PASSWORD is required"
        raise DevelopmentSeedError(msg)
    return settings.dev_seed_password.get_secret_value()


async def _acquire_seed_lock(session: AsyncSession) -> None:
    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        await session.scalar(select(func.pg_advisory_xact_lock(SEED_ADVISORY_LOCK_KEY)))


async def _ensure_user(
    session: AsyncSession,
    account: DemoAccount,
    *,
    created_at: datetime,
) -> tuple[UserRecord, bool]:
    records = (
        await session.scalars(
            select(UserRecord)
            .where((UserRecord.id == account.id) | (UserRecord.email == account.email))
            .with_for_update(),
        )
    ).all()
    if not records:
        record = UserRecord(
            id=account.id,
            email=account.email,
            email_verified_at=None,
            created_at=created_at,
            disabled_at=None,
        )
        session.add(record)
        return record, True
    if len(records) != 1 or records[0].id != account.id or records[0].email != account.email:
        msg = f"reserved development identity conflicts with existing state: {account.email}"
        raise DevelopmentSeedError(msg)
    record = records[0]
    if record.email_verified_at is not None or record.disabled_at is not None:
        msg = f"development identity is not enabled and unverified: {account.email}"
        raise DevelopmentSeedError(msg)
    return record, False


async def _ensure_credential(
    session: AsyncSession,
    account: DemoAccount,
    *,
    password: str,
    password_hash: str,
    password_manager: SeedPasswordManager,
    changed_at: datetime,
) -> bool:
    credential = await session.scalar(
        select(PasswordCredentialRecord)
        .where(PasswordCredentialRecord.user_id == account.id)
        .with_for_update(),
    )
    if credential is None:
        session.add(
            PasswordCredentialRecord(
                user_id=account.id,
                password_hash=password_hash,
                password_changed_at=changed_at,
                created_at=changed_at,
            ),
        )
        return True
    verification = await password_manager.verify_async(password, credential.password_hash)
    if not verification.valid:
        msg = f"development identity has a different password: {account.email}"
        raise DevelopmentSeedError(msg)
    return False


async def _ensure_workspace(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    created_at: datetime,
) -> tuple[WorkspaceRecord, bool]:
    workspace = await session.scalar(
        select(WorkspaceRecord).where(WorkspaceRecord.id == DEMO_WORKSPACE_ID).with_for_update(),
    )
    if workspace is None:
        workspace = WorkspaceRecord(
            id=DEMO_WORKSPACE_ID,
            name=DEMO_WORKSPACE_NAME,
            created_by_user_id=owner_user_id,
            created_at=created_at,
            updated_at=created_at,
        )
        session.add(workspace)
        return workspace, True
    if workspace.name != DEMO_WORKSPACE_NAME or workspace.created_by_user_id != owner_user_id:
        msg = "reserved development workspace conflicts with existing state"
        raise DevelopmentSeedError(msg)
    return workspace, False


async def _ensure_membership(
    session: AsyncSession,
    account: DemoAccount,
    *,
    joined_at: datetime,
) -> bool:
    membership = await session.scalar(
        select(MembershipRecord)
        .where(
            MembershipRecord.workspace_id == DEMO_WORKSPACE_ID,
            MembershipRecord.user_id == account.id,
        )
        .with_for_update(),
    )
    if account.role is None:
        if membership is not None:
            msg = "development outsider already belongs to the demo workspace"
            raise DevelopmentSeedError(msg)
        return False
    if membership is None:
        session.add(
            MembershipRecord(
                workspace_id=DEMO_WORKSPACE_ID,
                user_id=account.id,
                role=str(account.role),
                joined_at=joined_at,
            ),
        )
        return True
    if membership.role != str(account.role):
        msg = f"development membership has a different role: {account.email}"
        raise DevelopmentSeedError(msg)
    return False


async def seed_development_data(
    session: AsyncSession,
    *,
    password: str,
    password_manager: SeedPasswordManager,
    now: datetime | None = None,
) -> DevelopmentSeedResult:
    """Create the exact demo fixture once without producing sessions or invitations."""
    if not MIN_DEVELOPMENT_PASSWORD_LENGTH <= len(password) <= MAX_DEVELOPMENT_PASSWORD_LENGTH:
        msg = "development seed password must contain 12 to 128 characters"
        raise DevelopmentSeedError(msg)
    created_at = now or datetime.now(UTC)
    password_hashes = await asyncio.gather(
        *(password_manager.hash_async(password) for _account in DEMO_ACCOUNTS),
    )
    users_created = 0
    credentials_created = 0
    workspaces_created = 0
    memberships_created = 0
    try:
        async with session.begin():
            await _acquire_seed_lock(session)
            for account in DEMO_ACCOUNTS:
                _user, created = await _ensure_user(session, account, created_at=created_at)
                users_created += int(created)
            await session.flush()

            for account, password_hash in zip(DEMO_ACCOUNTS, password_hashes, strict=True):
                credentials_created += int(
                    await _ensure_credential(
                        session,
                        account,
                        password=password,
                        password_hash=password_hash,
                        password_manager=password_manager,
                        changed_at=created_at,
                    ),
                )
            await session.flush()

            _workspace, created = await _ensure_workspace(
                session,
                owner_user_id=DEMO_ACCOUNTS[0].id,
                created_at=created_at,
            )
            workspaces_created += int(created)
            await session.flush()

            for account in DEMO_ACCOUNTS:
                memberships_created += int(
                    await _ensure_membership(session, account, joined_at=created_at),
                )
            await session.flush()
    except IntegrityError as exc:
        msg = "development fixture conflicts with database constraints"
        raise DevelopmentSeedError(msg) from exc

    return DevelopmentSeedResult(
        users_created=users_created,
        credentials_created=credentials_created,
        workspaces_created=workspaces_created,
        memberships_created=memberships_created,
    )


async def _seed_container(container: AppContainer, *, password: str) -> DevelopmentSeedResult:
    async with container.session_factory() as session:
        return await seed_development_data(
            session,
            password=password,
            password_manager=container.password_manager,
        )


async def _run(settings: DevelopmentSeedSettings) -> DevelopmentSeedResult:
    password = _require_explicit_development_opt_in(settings)
    configure_logging(settings)
    container = build_container(settings)
    try:
        return await _seed_container(container, password=password)
    finally:
        await container.engine.dispose()


def _print_result(result: DevelopmentSeedResult) -> None:
    print("Development Swagger fixture is ready.")
    print(f"Workspace: {DEMO_WORKSPACE_NAME} ({DEMO_WORKSPACE_ID})")
    for account in DEMO_ACCOUNTS:
        role = account.role.value if account.role is not None else "outsider"
        print(f"- {account.email}: {role} ({account.id})")
    print("All accounts use the password configured in DEV_SEED_PASSWORD.")
    print(
        "Created this run: "
        f"{result.users_created} users, {result.credentials_created} credentials, "
        f"{result.workspaces_created} workspaces, {result.memberships_created} memberships.",
    )


def main() -> None:
    """Seed deterministic local identities after an explicit environment opt-in."""
    settings = DevelopmentSeedSettings()  # type: ignore[call-arg]
    try:
        result = asyncio.run(_run(settings))
    except DevelopmentSeedError as exc:
        print(f"Development seed refused: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    except KeyboardInterrupt:
        return
    _print_result(result)


if __name__ == "__main__":
    main()

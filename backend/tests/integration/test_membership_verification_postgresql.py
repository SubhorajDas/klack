"""Real HTTP/database coverage of environment-specific membership eligibility."""

import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from klack.bootstrap import create_app
from klack.core.config import AppEnvironment, LogFormat, Settings
from klack.modules.channels.application.service import ChannelService
from klack.modules.channels.domain.entities import ChannelVisibility
from klack.modules.channels.infrastructure.models import ChannelMembershipRecord, ChannelRecord
from klack.modules.channels.infrastructure.repository import SqlAlchemyChannelRepository
from klack.modules.identity.domain.entities import EmailActionPurpose
from klack.modules.identity.infrastructure.models import EmailOutboxRecord, UserRecord
from klack.modules.workspaces.application.service import WorkspaceAccessService, WorkspaceService
from klack.modules.workspaces.infrastructure.models import (
    MembershipRecord,
    WorkspaceRecord,
)
from klack.modules.workspaces.infrastructure.repository import SqlAlchemyWorkspaceRepository

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL"),
]


@pytest.fixture(scope="module")
def migrated_schema() -> None:
    command.upgrade(Config(Path(__file__).parents[2] / "alembic.ini"), "head")


@pytest.mark.parametrize("environment", list(AppEnvironment))
async def test_membership_modes_and_immediate_unlock_after_verification(
    migrated_schema: None, environment: AppEnvironment
) -> None:
    del migrated_schema
    secure = environment in {AppEnvironment.STAGING, AppEnvironment.PRODUCTION}
    origin = "https://test" if secure else "http://test"
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env=environment,
        log_format=LogFormat.JSON,
        auth_cookie_secure=secure,
        auth_trusted_origin=origin,
        auth_public_web_origin=origin,
        auth_jwt_secret="integration-jwt-secret-at-least-thirty-two-bytes",
        auth_refresh_secret="integration-refresh-secret-at-least-thirty-two-bytes",
        auth_action_secret="integration-action-secret-at-least-thirty-two-bytes",
        workspace_invitation_secret="integration-workspace-secret-at-least-thirty-two-bytes",
        realtime_enabled=False,
        files_enabled=False,
        auth_registration_rate_limit=100,
        auth_action_complete_rate_limit=100,
    )
    app = create_app(settings)
    users: list[UUID] = []
    async with app.router.lifespan_context(app):
        container = app.state.container
        async with (
            AsyncClient(transport=ASGITransport(app=app), base_url=origin) as owner,
            AsyncClient(transport=ASGITransport(app=app), base_url=origin) as member,
        ):
            try:
                for client in (owner, member):
                    response = await client.post(
                        "/api/v1/auth/register",
                        headers={"Origin": origin},
                        json={
                            "email": f"verification-{uuid4().hex}@example.com",
                            "password": "Integration-password-123!",
                        },
                    )
                    assert response.status_code == 201
                    users.append(UUID(response.json()["user"]["id"]))
                    client.headers.update(
                        {"Origin": origin, "X-CSRF-Token": client.cookies["klack_csrf"]}
                    )

                # Seed pre-existing memberships as if switching a development instance to prod.
                async with container.session_factory() as session:
                    repository = SqlAlchemyWorkspaceRepository(session)
                    workspaces = WorkspaceService(
                        repository=repository,
                        invitation_tokens=container.workspace_invitation_tokens,
                    )
                    workspace = await workspaces.create_workspace(
                        actor_user_id=users[0], name="Main"
                    )
                    invitation = await workspaces.create_invitation(
                        actor_user_id=users[0], workspace_id=workspace.id
                    )
                    await workspaces.accept_invitation(
                        actor_user_id=users[1], raw_token=invitation.raw_token
                    )
                    channels = ChannelService(
                        repository=SqlAlchemyChannelRepository(session),
                        workspace_access=WorkspaceAccessService(repository),
                    )
                    channel = await channels.create_channel(
                        actor_user_id=users[0],
                        workspace_id=workspace.id,
                        name="general",
                        visibility=ChannelVisibility.PUBLIC,
                    )

                async def exercise(suffix: str, blocked: bool) -> str:
                    # A fresh invitation lets each round prove acceptance without prior membership.
                    async with container.session_factory() as session:
                        service = WorkspaceService(
                            repository=SqlAlchemyWorkspaceRepository(session),
                            invitation_tokens=container.workspace_invitation_tokens,
                        )
                        other = await service.create_workspace(actor_user_id=users[1], name=suffix)
                        invite = await service.create_invitation(
                            actor_user_id=users[1], workspace_id=other.id
                        )
                        before = [
                            await session.scalar(select(func.count()).select_from(model))
                            for model in (
                                WorkspaceRecord,
                                MembershipRecord,
                                ChannelRecord,
                                ChannelMembershipRecord,
                            )
                        ]
                    root = f"/api/v1/workspaces/{workspace.id}"
                    responses = [
                        await owner.post("/api/v1/workspaces", json={"name": suffix}),
                        await owner.post(
                            "/api/v1/workspace-invitations/accept", json={"token": invite.raw_token}
                        ),
                        await owner.post(root + "/channels", json={"name": suffix}),
                        await member.put(root + f"/channels/{channel.channel.id}/memberships/me"),
                        await owner.put(
                            root + f"/channels/{channel.channel.id}/memberships/{users[1]}"
                        ),
                        await owner.post(
                            root + "/direct-messages", json={"user_id": str(users[1])}
                        ),
                    ]
                    expected = [403] * 6 if blocked else [201, 201, 201, 200, 200, 200]
                    assert [r.status_code for r in responses] == expected
                    if blocked:
                        assert all(
                            r.json()["code"] == "email_verification_required" for r in responses
                        )
                        async with container.session_factory() as session:
                            after = [
                                await session.scalar(select(func.count()).select_from(model))
                                for model in (
                                    WorkspaceRecord,
                                    MembershipRecord,
                                    ChannelRecord,
                                    ChannelMembershipRecord,
                                )
                            ]
                        assert after == before
                    return invite.raw_token

                blocked_invitation = await exercise("unverified", blocked=secure)
                for path in (
                    "/api/v1/auth/me",
                    "/api/v1/workspaces",
                    f"/api/v1/workspaces/{workspace.id}/channels",
                ):
                    assert (await owner.get(path)).status_code == 200
                # Verify only the owner first: administrators still cannot add unverified targets.
                for index, client in enumerate((owner, member)):
                    async with container.session_factory() as session:
                        record = await session.scalar(
                            select(EmailOutboxRecord)
                            .join(UserRecord, UserRecord.email == EmailOutboxRecord.recipient)
                            .where(UserRecord.id == users[index])
                        )
                        assert record is not None
                        token = container.action_token_manager.open_email_action(
                            encrypted_payload=record.encrypted_payload,
                            recipient=record.recipient,
                            purpose=EmailActionPurpose.VERIFY_EMAIL,
                        )
                    response = await client.post(
                        "/api/v1/auth/email-verification/complete", json={"token": token}
                    )
                    assert response.status_code == 204
                    if index == 0 and secure:
                        root = f"/api/v1/workspaces/{workspace.id}"
                        response = await owner.put(
                            root + f"/channels/{channel.channel.id}/memberships/{users[1]}"
                        )
                        assert response.status_code == 403
                        response = await owner.post(
                            root + "/direct-messages", json={"user_id": str(users[1])}
                        )
                        assert response.status_code == 403
                if secure:
                    response = await owner.post(
                        "/api/v1/workspace-invitations/accept",
                        json={"token": blocked_invitation},
                    )
                    assert response.status_code == 201
                await exercise("verified", blocked=False)
            finally:
                async with container.session_factory() as session:
                    await session.execute(
                        delete(WorkspaceRecord).where(WorkspaceRecord.created_by_user_id.in_(users))
                    )
                    await session.execute(delete(UserRecord).where(UserRecord.id.in_(users)))
                    await session.commit()

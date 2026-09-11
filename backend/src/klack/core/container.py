"""Small typed dependency container built once per process."""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from klack.core.config import Settings
from klack.core.db.session import (
    DatabaseHealthCheck,
    SessionFactory,
    create_database_health_check,
    create_engine,
    create_session_factory,
)
from klack.modules.channels.application.service import ChannelPolicy
from klack.modules.identity.application.service import IdentityPolicy
from klack.modules.identity.infrastructure.action_security import ActionTokenManager
from klack.modules.identity.infrastructure.security import (
    AccessTokenCodec,
    PasswordManager,
    SessionTokenManager,
)
from klack.modules.messaging.application.service import MessagePolicy
from klack.modules.workspaces.application.service import WorkspacePolicy
from klack.modules.workspaces.infrastructure.invitation_security import (
    InvitationTokenManager,
)


@dataclass(frozen=True, slots=True)
class AppContainer:
    """Long-lived technical dependencies shared by application entry points."""

    settings: Settings
    engine: AsyncEngine
    session_factory: SessionFactory
    database_health_check: DatabaseHealthCheck
    password_manager: PasswordManager
    access_token_codec: AccessTokenCodec
    session_token_manager: SessionTokenManager
    action_token_manager: ActionTokenManager
    identity_policy: IdentityPolicy
    workspace_invitation_tokens: InvitationTokenManager
    workspace_policy: WorkspacePolicy
    channel_policy: ChannelPolicy
    message_policy: MessagePolicy


def build_container(
    settings: Settings,
    *,
    database_health_check: DatabaseHealthCheck | None = None,
) -> AppContainer:
    """Construct process-level infrastructure without opening network connections."""
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    health_check = database_health_check or create_database_health_check(
        engine,
        timeout_seconds=settings.healthcheck_timeout_seconds,
    )
    return AppContainer(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        database_health_check=health_check,
        password_manager=PasswordManager(
            max_concurrency=settings.auth_password_max_concurrency,
        ),
        access_token_codec=AccessTokenCodec(
            secret=settings.auth_jwt_secret_value(),
            issuer=settings.auth_issuer,
            audience=settings.auth_audience,
            ttl=timedelta(seconds=settings.auth_access_ttl_seconds),
        ),
        session_token_manager=SessionTokenManager(
            secret=settings.auth_refresh_secret_value(),
        ),
        action_token_manager=ActionTokenManager(
            secret=settings.auth_action_secret_value(),
        ),
        identity_policy=IdentityPolicy(
            refresh_ttl=timedelta(seconds=settings.auth_refresh_ttl_seconds),
            refresh_min_interval=timedelta(
                seconds=settings.auth_refresh_min_interval_seconds,
            ),
            max_active_sessions=settings.auth_max_active_sessions,
            email_verification_ttl=timedelta(
                seconds=settings.auth_email_verification_ttl_seconds,
            ),
            password_recovery_ttl=timedelta(
                seconds=settings.auth_password_recovery_ttl_seconds,
            ),
            rate_limit_window=timedelta(
                seconds=settings.auth_rate_limit_window_seconds,
            ),
            rate_limit_block=timedelta(
                seconds=settings.auth_rate_limit_block_seconds,
            ),
            registration_rate_limit=settings.auth_registration_rate_limit,
            login_rate_limit=settings.auth_login_rate_limit,
            email_action_rate_limit=settings.auth_email_action_rate_limit,
            action_complete_rate_limit=settings.auth_action_complete_rate_limit,
        ),
        workspace_invitation_tokens=InvitationTokenManager(
            secret=settings.workspace_invitation_secret_value(),
        ),
        workspace_policy=WorkspacePolicy(
            invitation_ttl=timedelta(
                seconds=settings.workspace_invitation_ttl_seconds,
            ),
        ),
        channel_policy=ChannelPolicy(),
        message_policy=MessagePolicy(),
    )

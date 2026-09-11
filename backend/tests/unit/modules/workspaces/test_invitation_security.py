"""Opaque workspace invitation token security contracts."""

import base64
import hmac
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from klack.core.config import AppEnvironment, LogFormat, Settings
from klack.modules.workspaces.domain.errors import InvalidInvitationToken
from klack.modules.workspaces.infrastructure.invitation_security import (
    INVITATION_TOKEN_SECRET_BYTES,
    InvitationTokenManager,
)

TOKEN_ID = UUID("33333333-3333-4333-8333-333333333333")
SIGNING_SECRET = "workspace-invitation-secret-at-least-thirty-two-bytes"


def _encoded_secret(size: int = INVITATION_TOKEN_SECRET_BYTES) -> str:
    return base64.urlsafe_b64encode(b"s" * size).rstrip(b"=").decode("ascii")


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "app_env": AppEnvironment.TEST,
        "database_url": "postgresql+asyncpg://klack:secret@db:5432/klack",
        "auth_jwt_secret": "test-jwt-secret-at-least-thirty-two-bytes",
        "auth_refresh_secret": "test-refresh-secret-at-least-thirty-two-bytes",
        "auth_action_secret": "test-action-secret-at-least-thirty-two-bytes",
        "auth_trusted_origin": "http://test",
        "auth_public_web_origin": "http://test",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_invitation_token_round_trip_uses_a_domain_separated_hmac() -> None:
    manager = InvitationTokenManager(
        secret=SIGNING_SECRET,
        uuid_factory=lambda: TOKEN_ID,
        secret_factory=lambda size: _encoded_secret(size),
    )

    issued = manager.issue()
    presented = manager.present(issued.raw)
    unscoped_digest = hmac.new(SIGNING_SECRET.encode(), issued.raw.encode(), sha256).hexdigest()

    assert issued.token_id == TOKEN_ID
    assert issued.raw == f"{TOKEN_ID}.{_encoded_secret()}"
    assert issued.raw not in issued.digest
    assert issued.digest != unscoped_digest
    assert presented.token_id == TOKEN_ID
    assert manager.matches(presented.digest, issued.digest)


def test_invitation_token_is_bound_to_its_signing_secret_and_full_presentation() -> None:
    manager = InvitationTokenManager(
        secret=SIGNING_SECRET,
        uuid_factory=lambda: TOKEN_ID,
        secret_factory=lambda size: _encoded_secret(size),
    )
    issued = manager.issue()
    other_manager = InvitationTokenManager(secret="different-invitation-secret-at-least-32-bytes")
    tampered = f"{TOKEN_ID}.{_encoded_secret(INVITATION_TOKEN_SECRET_BYTES + 1)}"

    assert not manager.matches(other_manager.present(issued.raw).digest, issued.digest)
    assert not manager.matches(manager.present(tampered).digest, issued.digest)


@pytest.mark.parametrize(
    "raw_token",
    [
        None,
        "",
        "not-a-token",
        f"{uuid4()}.short",
        f"{uuid4()}.{'!' * 43}",
        f"{uuid4()}.{_encoded_secret()}.extra",
        "x" * 257,
    ],
)
def test_invalid_invitation_token_presentations_are_rejected(
    raw_token: str | None,
) -> None:
    manager = InvitationTokenManager(secret=SIGNING_SECRET)

    with pytest.raises(InvalidInvitationToken):
        manager.present(raw_token)


def test_invitation_token_manager_rejects_unsafe_factories_and_signing_keys() -> None:
    with pytest.raises(ValueError, match="signing secret"):
        InvitationTokenManager(secret="too-short")

    manager = InvitationTokenManager(
        secret=SIGNING_SECRET,
        secret_factory=lambda _size: "short",
    )
    with pytest.raises(ValueError, match="invalid secret"):
        manager.issue()

    oversized = InvitationTokenManager(
        secret=SIGNING_SECRET,
        secret_factory=lambda _size: _encoded_secret(256),
    )
    with pytest.raises(ValueError, match="oversized"):
        oversized.issue()


def test_workspace_invitation_settings_are_bounded_and_secret_isolated() -> None:
    settings = _settings(workspace_invitation_secret=SIGNING_SECRET)

    assert settings.workspace_invitation_ttl_seconds == 604_800
    assert settings.workspace_invitation_secret_value() == SIGNING_SECRET

    with pytest.raises(ValidationError, match="must be different"):
        _settings(
            workspace_invitation_secret="test-action-secret-at-least-thirty-two-bytes",
        )
    with pytest.raises(ValidationError):
        _settings(workspace_invitation_ttl_seconds=899)
    with pytest.raises(ValidationError):
        _settings(workspace_invitation_ttl_seconds=2_592_001)


def test_deployed_settings_reject_example_invitation_secret() -> None:
    with pytest.raises(ValidationError, match="checked-in example"):
        _settings(
            app_env=AppEnvironment.STAGING,
            log_format=LogFormat.JSON,
            auth_trusted_origin="https://app.example",
            auth_public_web_origin="https://app.example",
            workspace_invitation_secret=("development-workspace-invitation-secret-change-me-00004"),
        )

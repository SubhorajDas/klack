"""Verification eligibility and environment selection contracts."""

from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from klack.core.config import AppEnvironment, Settings
from klack.modules.identity.application.verification import EmailVerificationAccessService
from klack.modules.identity.domain.entities import User
from klack.modules.identity.domain.errors import EmailVerificationRequired


@pytest.mark.parametrize("environment", list(AppEnvironment))
def test_membership_mode(settings: Settings, environment: AppEnvironment) -> None:
    configured = settings.model_copy(update={"app_env": environment})
    assert configured.membership_email_verification_required == (
        environment in {AppEnvironment.STAGING, AppEnvironment.PRODUCTION}
    )


async def test_development_skips_verification_lookup() -> None:
    repository = AsyncMock()
    await EmailVerificationAccessService(repository, required=False).require_verified(uuid4())
    repository.get_user_by_id.assert_not_awaited()


async def test_verification_checks_current_state_and_rejects_disabled_or_missing_users() -> None:
    now = datetime.now(UTC)
    user = User(uuid4(), "person@example.com", None, now, None)
    repository = AsyncMock()
    service = EmailVerificationAccessService(repository, required=True)
    for current in (user, None, replace(user, email_verified_at=now, disabled_at=now)):
        repository.get_user_by_id.return_value = current
        with pytest.raises(EmailVerificationRequired):
            await service.require_verified(user.id)
    repository.get_user_by_id.return_value = replace(user, email_verified_at=now)
    await service.require_verified(user.id)

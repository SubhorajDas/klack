"""Identity maintenance process orchestration contracts."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from klack import identity_worker
from klack.core.config import Settings


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class FakeContainer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine = FakeEngine()


async def test_once_without_smtp_runs_cleanup_and_disposes(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = FakeContainer(settings)
    cleanup_calls = 0

    async def cleanup_once(_container: object) -> int:
        nonlocal cleanup_calls
        cleanup_calls += 1
        return 3

    monkeypatch.setattr(identity_worker, "configure_logging", lambda _settings: None)
    monkeypatch.setattr(identity_worker, "build_container", lambda _settings: container)
    monkeypatch.setattr(identity_worker, "_cleanup_once", cleanup_once)

    await identity_worker._run("once", settings)

    assert cleanup_calls == 1
    assert container.engine.disposed
    assert identity_worker._email_sender(settings) is None


async def test_delivery_requires_smtp_configuration_and_disposes(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = FakeContainer(settings)
    monkeypatch.setattr(identity_worker, "configure_logging", lambda _settings: None)
    monkeypatch.setattr(identity_worker, "build_container", lambda _settings: container)

    with pytest.raises(RuntimeError, match="SMTP_HOST"):
        await identity_worker._run("deliver", settings)

    assert container.engine.disposed


async def test_configured_delivery_and_cleanup_commands_are_dispatched(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = settings.model_copy(
        update={
            "smtp_host": "smtp.example",
            "email_from_address": "noreply@example.com",
        },
    )
    container = FakeContainer(configured)
    sender = SimpleNamespace()
    deliveries: list[object] = []
    cleanups: list[object] = []

    async def deliver_once(target: object, selected_sender: object) -> tuple[int, int]:
        deliveries.extend((target, selected_sender))
        return 1, 0

    async def cleanup_once(target: object) -> int:
        cleanups.append(target)
        return 0

    monkeypatch.setattr(identity_worker, "configure_logging", lambda _settings: None)
    monkeypatch.setattr(identity_worker, "build_container", lambda _settings: container)
    monkeypatch.setattr(identity_worker, "_email_sender", lambda _settings: sender)
    monkeypatch.setattr(identity_worker, "_deliver_once", deliver_once)
    monkeypatch.setattr(identity_worker, "_cleanup_once", cleanup_once)

    await identity_worker._run("deliver", configured)
    await identity_worker._run("cleanup", configured)

    assert deliveries == [container, sender]
    assert cleanups == [container]
    assert container.engine.disposed


def test_smtp_sender_factory_uses_secret_only_at_boundary(settings: Settings) -> None:
    configured = settings.model_copy(
        update={
            "smtp_host": "smtp.example",
            "email_from_address": "noreply@example.com",
            "smtp_username": "mailer",
            "smtp_password": SimpleNamespace(get_secret_value=lambda: "smtp-secret"),
        },
    )

    sender = identity_worker._email_sender(configured)

    assert sender is not None
    assert configured.smtp_password_value() == "smtp-secret"


async def test_continuous_worker_cleans_then_propagates_cancellation(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = FakeContainer(settings)
    cleanups = 0

    async def cleanup_once(_container: object) -> int:
        nonlocal cleanups
        cleanups += 1
        return 0

    async def cancel_sleep(_seconds: float) -> Any:
        raise asyncio.CancelledError

    monkeypatch.setattr(identity_worker, "configure_logging", lambda _settings: None)
    monkeypatch.setattr(identity_worker, "build_container", lambda _settings: container)
    monkeypatch.setattr(identity_worker, "_cleanup_once", cleanup_once)
    monkeypatch.setattr(identity_worker.asyncio, "sleep", cancel_sleep)

    with pytest.raises(asyncio.CancelledError):
        await identity_worker._run("run", settings)

    assert cleanups == 1
    assert container.engine.disposed

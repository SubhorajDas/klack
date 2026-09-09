"""Identity email-outbox and expired-row maintenance process."""

import argparse
import asyncio
from datetime import timedelta
from typing import NoReturn

import structlog

from klack.core.config import Settings
from klack.core.container import AppContainer, build_container
from klack.core.logging import configure_logging
from klack.modules.identity.infrastructure.maintenance import (
    EmailSender,
    IdentityMaintenanceRepository,
    IdentityMaintenanceService,
    SmtpEmailSender,
)

logger = structlog.get_logger(__name__)


class UnavailableEmailSender:
    """Guard used by cleanup-only processes without SMTP configuration."""

    async def send(self, *, recipient: str, subject: str, text_body: str) -> NoReturn:
        del recipient, subject, text_body
        msg = "SMTP delivery is not configured"
        raise RuntimeError(msg)


def _email_sender(settings: Settings) -> EmailSender | None:
    if settings.smtp_host is None or settings.email_from_address is None:
        return None
    return SmtpEmailSender(
        host=settings.smtp_host,
        port=settings.smtp_port,
        sender=str(settings.email_from_address),
        timeout_seconds=settings.smtp_timeout_seconds,
        starttls=settings.smtp_starttls,
        use_ssl=settings.smtp_use_ssl,
        username=settings.smtp_username,
        password=settings.smtp_password_value(),
    )


def _service(
    container: AppContainer,
    repository: IdentityMaintenanceRepository,
    sender: EmailSender,
) -> IdentityMaintenanceService:
    return IdentityMaintenanceService(
        repository=repository,
        action_tokens=container.action_token_manager,
        sender=sender,
        public_web_origin=container.settings.auth_public_web_origin,
    )


async def _deliver_once(container: AppContainer, sender: EmailSender) -> tuple[int, int]:
    async with container.session_factory() as session:
        service = _service(container, IdentityMaintenanceRepository(session), sender)
        return await service.deliver_batch(
            batch_size=container.settings.identity_outbox_batch_size,
            lease_for=timedelta(seconds=container.settings.identity_outbox_lease_seconds),
        )


async def _cleanup_once(container: AppContainer) -> int:
    async with container.session_factory() as session:
        service = _service(
            container,
            IdentityMaintenanceRepository(session),
            UnavailableEmailSender(),
        )
        counts = await service.cleanup(
            retention=timedelta(
                seconds=container.settings.identity_cleanup_retention_seconds,
            ),
            batch_size=container.settings.identity_cleanup_batch_size,
        )
        return counts.total


async def _run(command: str, settings: Settings) -> None:
    configure_logging(settings)
    container = build_container(settings)
    sender = _email_sender(settings)
    try:
        if command == "deliver":
            if sender is None:
                msg = "SMTP_HOST and EMAIL_FROM_ADDRESS are required for delivery"
                raise RuntimeError(msg)
            await _deliver_once(container, sender)
            return
        if command == "cleanup":
            await _cleanup_once(container)
            return
        if command == "once":
            if sender is None:
                logger.warning("identity_email_delivery_disabled")
            else:
                await _deliver_once(container, sender)
            await _cleanup_once(container)
            return

        cleanup_elapsed = settings.identity_cleanup_interval_seconds
        logger.info("identity_worker_started", email_delivery_enabled=sender is not None)
        while True:
            try:
                if sender is not None:
                    await _deliver_once(container, sender)
                cleanup_elapsed += settings.identity_worker_poll_seconds
                if cleanup_elapsed >= settings.identity_cleanup_interval_seconds:
                    await _cleanup_once(container)
                    cleanup_elapsed = 0
            except Exception:
                logger.exception("identity_worker_iteration_failed")
            await asyncio.sleep(settings.identity_worker_poll_seconds)
    finally:
        await container.engine.dispose()
        logger.info("identity_worker_stopped")


def main() -> None:
    """Run continuous or one-shot identity maintenance from installed package metadata."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("run", "once", "deliver", "cleanup"),
        nargs="?",
        default="run",
    )
    args = parser.parse_args()
    settings = Settings()  # type: ignore[call-arg]
    try:
        asyncio.run(_run(args.command, settings))
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()

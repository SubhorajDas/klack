"""Leased transactional-email delivery and bounded identity-row cleanup."""

import asyncio
import smtplib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Protocol
from urllib.parse import quote
from uuid import UUID, uuid4

import structlog
from sqlalchemy import and_, delete, or_, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from klack.modules.identity.domain.entities import EmailActionPurpose
from klack.modules.identity.infrastructure.action_security import ActionTokenManager
from klack.modules.identity.infrastructure.models import (
    AuthRateLimitRecord,
    AuthSessionRecord,
    EmailActionTokenRecord,
    EmailOutboxRecord,
)

Clock = Callable[[], datetime]
logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class LeasedEmail:
    """One encrypted outbox row leased to a worker instance."""

    id: UUID
    recipient: str
    purpose: EmailActionPurpose
    encrypted_payload: str
    attempts: int
    lease_id: UUID


@dataclass(frozen=True, slots=True)
class CleanupCounts:
    """Number of durable identity rows removed by one cleanup run."""

    outbox_messages: int = 0
    email_actions: int = 0
    sessions: int = 0
    rate_limits: int = 0

    @property
    def total(self) -> int:
        return self.outbox_messages + self.email_actions + self.sessions + self.rate_limits

    def __add__(self, other: "CleanupCounts") -> "CleanupCounts":
        return CleanupCounts(
            outbox_messages=self.outbox_messages + other.outbox_messages,
            email_actions=self.email_actions + other.email_actions,
            sessions=self.sessions + other.sessions,
            rate_limits=self.rate_limits + other.rate_limits,
        )


class EmailSender(Protocol):
    """Outbound email boundary used after an outbox lease is committed."""

    async def send(self, *, recipient: str, subject: str, text_body: str) -> None: ...


class SmtpEmailSender:
    """Small async facade over the standard library SMTP client."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        sender: str,
        timeout_seconds: float,
        starttls: bool,
        use_ssl: bool,
        username: str | None,
        password: str | None,
    ) -> None:
        self._host = host
        self._port = port
        self._sender = sender
        self._timeout_seconds = timeout_seconds
        self._starttls = starttls
        self._use_ssl = use_ssl
        self._username = username
        self._password = password

    async def send(self, *, recipient: str, subject: str, text_body: str) -> None:
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(text_body)
        await asyncio.to_thread(self._send_sync, message)

    def _send_sync(self, message: EmailMessage) -> None:
        client_type = smtplib.SMTP_SSL if self._use_ssl else smtplib.SMTP
        with client_type(
            self._host,
            self._port,
            timeout=self._timeout_seconds,
        ) as client:
            if self._starttls:
                client.starttls()
            if self._username is not None and self._password is not None:
                client.login(self._username, self._password)
            client.send_message(message)


class IdentityMaintenanceRepository:
    """PostgreSQL-safe leasing and cleanup operations for one worker session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim_email_batch(
        self,
        *,
        now: datetime,
        batch_size: int,
        lease_for: timedelta,
    ) -> list[LeasedEmail]:
        statement = (
            select(EmailOutboxRecord)
            .join(
                EmailActionTokenRecord,
                EmailActionTokenRecord.id == EmailOutboxRecord.action_token_id,
            )
            .where(
                EmailOutboxRecord.sent_at.is_(None),
                EmailOutboxRecord.available_at <= now,
                or_(
                    EmailOutboxRecord.leased_until.is_(None),
                    EmailOutboxRecord.leased_until <= now,
                ),
                EmailActionTokenRecord.used_at.is_(None),
                EmailActionTokenRecord.revoked_at.is_(None),
                EmailActionTokenRecord.expires_at > now,
            )
            .order_by(EmailOutboxRecord.available_at, EmailOutboxRecord.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True, of=EmailOutboxRecord)
        )
        records = list((await self._session.scalars(statement)).all())
        leases: list[LeasedEmail] = []
        for record in records:
            lease_id = uuid4()
            record.lease_id = lease_id
            record.leased_until = now + lease_for
            leases.append(
                LeasedEmail(
                    id=record.id,
                    recipient=record.recipient,
                    purpose=EmailActionPurpose(record.purpose),
                    encrypted_payload=record.encrypted_payload,
                    attempts=record.attempts,
                    lease_id=lease_id,
                ),
            )
        return leases

    async def mark_email_sent(
        self,
        *,
        message_id: UUID,
        lease_id: UUID,
        sent_at: datetime,
    ) -> None:
        await self._session.execute(
            update(EmailOutboxRecord)
            .where(
                EmailOutboxRecord.id == message_id,
                EmailOutboxRecord.lease_id == lease_id,
                EmailOutboxRecord.sent_at.is_(None),
            )
            .values(
                sent_at=sent_at,
                lease_id=None,
                leased_until=None,
                last_failure_code=None,
            ),
        )

    async def mark_email_failed(
        self,
        *,
        message_id: UUID,
        lease_id: UUID,
        available_at: datetime,
        failure_code: str,
    ) -> None:
        await self._session.execute(
            update(EmailOutboxRecord)
            .where(
                EmailOutboxRecord.id == message_id,
                EmailOutboxRecord.lease_id == lease_id,
                EmailOutboxRecord.sent_at.is_(None),
            )
            .values(
                attempts=EmailOutboxRecord.attempts + 1,
                available_at=available_at,
                lease_id=None,
                leased_until=None,
                last_failure_code=failure_code[:128],
            ),
        )

    async def cleanup_batch(
        self,
        *,
        cutoff: datetime,
        batch_size: int,
    ) -> CleanupCounts:
        outbox_ids = list(
            (
                await self._session.scalars(
                    select(EmailOutboxRecord.id)
                    .where(EmailOutboxRecord.sent_at <= cutoff)
                    .order_by(EmailOutboxRecord.sent_at, EmailOutboxRecord.id)
                    .limit(batch_size),
                )
            ).all(),
        )
        if outbox_ids:
            await self._session.execute(
                delete(EmailOutboxRecord).where(EmailOutboxRecord.id.in_(outbox_ids)),
            )

        action_ids = list(
            (
                await self._session.scalars(
                    select(EmailActionTokenRecord.id)
                    .where(
                        or_(
                            EmailActionTokenRecord.expires_at <= cutoff,
                            EmailActionTokenRecord.used_at <= cutoff,
                            EmailActionTokenRecord.revoked_at <= cutoff,
                        ),
                    )
                    .order_by(EmailActionTokenRecord.expires_at, EmailActionTokenRecord.id)
                    .limit(batch_size),
                )
            ).all(),
        )
        if action_ids:
            await self._session.execute(
                delete(EmailActionTokenRecord).where(
                    EmailActionTokenRecord.id.in_(action_ids),
                ),
            )

        session_ids = list(
            (
                await self._session.scalars(
                    select(AuthSessionRecord.id)
                    .where(
                        or_(
                            AuthSessionRecord.expires_at <= cutoff,
                            and_(
                                AuthSessionRecord.revoked_at.is_not(None),
                                AuthSessionRecord.revoked_at <= cutoff,
                            ),
                        ),
                    )
                    .order_by(AuthSessionRecord.expires_at, AuthSessionRecord.id)
                    .limit(batch_size),
                )
            ).all(),
        )
        if session_ids:
            await self._session.execute(
                delete(AuthSessionRecord).where(AuthSessionRecord.id.in_(session_ids)),
            )

        rate_keys = list(
            (
                await self._session.execute(
                    select(AuthRateLimitRecord.scope, AuthRateLimitRecord.subject_hash)
                    .where(AuthRateLimitRecord.updated_at <= cutoff)
                    .order_by(AuthRateLimitRecord.updated_at)
                    .limit(batch_size),
                )
            ).all(),
        )
        if rate_keys:
            await self._session.execute(
                delete(AuthRateLimitRecord).where(
                    tuple_(AuthRateLimitRecord.scope, AuthRateLimitRecord.subject_hash).in_(
                        rate_keys,
                    ),
                ),
            )

        return CleanupCounts(
            outbox_messages=len(outbox_ids),
            email_actions=len(action_ids),
            sessions=len(session_ids),
            rate_limits=len(rate_keys),
        )

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()


class IdentityMaintenanceService:
    """Deliver queued identity emails and drain old global identity state."""

    def __init__(
        self,
        *,
        repository: IdentityMaintenanceRepository,
        action_tokens: ActionTokenManager,
        sender: EmailSender,
        public_web_origin: str,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._action_tokens = action_tokens
        self._sender = sender
        self._public_web_origin = public_web_origin.rstrip("/")
        self._clock = clock

    async def deliver_batch(
        self,
        *,
        batch_size: int,
        lease_for: timedelta,
    ) -> tuple[int, int]:
        leases = await self._repository.claim_email_batch(
            now=self._clock(),
            batch_size=batch_size,
            lease_for=lease_for,
        )
        await self._repository.commit()
        sent = 0
        failed = 0
        for lease in leases:
            try:
                raw_token = self._action_tokens.open_email_action(
                    encrypted_payload=lease.encrypted_payload,
                    recipient=lease.recipient,
                    purpose=lease.purpose,
                )
                subject, body = self._render_email(lease.purpose, raw_token)
                await self._sender.send(
                    recipient=lease.recipient,
                    subject=subject,
                    text_body=body,
                )
            except Exception as exc:
                failed += 1
                retry_seconds = min(3_600, 30 * (2 ** min(lease.attempts, 7)))
                await self._repository.mark_email_failed(
                    message_id=lease.id,
                    lease_id=lease.lease_id,
                    available_at=self._clock() + timedelta(seconds=retry_seconds),
                    failure_code=type(exc).__name__,
                )
                await self._repository.commit()
                logger.warning(
                    "identity_email_delivery_failed",
                    outbox_id=str(lease.id),
                    failure_code=type(exc).__name__,
                    retry_seconds=retry_seconds,
                )
            else:
                sent += 1
                await self._repository.mark_email_sent(
                    message_id=lease.id,
                    lease_id=lease.lease_id,
                    sent_at=self._clock(),
                )
                await self._repository.commit()
                logger.info(
                    "identity_email_delivered",
                    outbox_id=str(lease.id),
                    purpose=lease.purpose,
                )
        return sent, failed

    async def cleanup(self, *, retention: timedelta, batch_size: int) -> CleanupCounts:
        cutoff = self._clock() - retention
        total = CleanupCounts()
        while True:
            batch = await self._repository.cleanup_batch(
                cutoff=cutoff,
                batch_size=batch_size,
            )
            await self._repository.commit()
            total += batch
            if (
                max(
                    batch.outbox_messages,
                    batch.email_actions,
                    batch.sessions,
                    batch.rate_limits,
                )
                < batch_size
            ):
                break
        logger.info(
            "identity_cleanup_completed",
            outbox_messages=total.outbox_messages,
            email_actions=total.email_actions,
            sessions=total.sessions,
            rate_limits=total.rate_limits,
        )
        return total

    def _render_email(self, purpose: EmailActionPurpose, raw_token: str) -> tuple[str, str]:
        encoded_token = quote(raw_token, safe="")
        if purpose is EmailActionPurpose.VERIFY_EMAIL:
            subject = "Verify your Klack email"
            route = "verify-email"
            introduction = "Verify your email address to finish setting up your Klack account."
        else:
            subject = "Reset your Klack password"
            route = "recover-password"
            introduction = "Use this link to choose a new password for your Klack account."
        link = f"{self._public_web_origin}/{route}?token={encoded_token}"
        return (
            subject,
            f"{introduction}\n\n{link}\n\nIf you did not request this, ignore this email.",
        )

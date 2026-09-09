"""Async SQLAlchemy implementation of the identity persistence port."""

from datetime import datetime, timedelta
from math import ceil
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from klack.modules.identity.application.ports import (
    IdentityConflict,
    RateLimitRequest,
)
from klack.modules.identity.domain.entities import (
    AuthenticatedIdentity,
    AuthSession,
    EmailActionContext,
    EmailActionPurpose,
    EmailActionToken,
    EmailOutboxMessage,
    LoginRecord,
    PasswordCredential,
    RefreshContext,
    RefreshToken,
    User,
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


class SqlAlchemyIdentityRepository:
    """Persist identity state in the request-scoped transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def email_exists(self, email: str) -> bool:
        statement = select(UserRecord.id).where(UserRecord.email == email)
        return (await self._session.scalar(statement)) is not None

    async def get_user_by_email(
        self,
        email: str,
        *,
        for_update: bool = False,
    ) -> User | None:
        statement = select(UserRecord).where(UserRecord.email == email)
        if for_update:
            statement = statement.with_for_update()
        record = await self._session.scalar(statement)
        return None if record is None else self._user(record)

    async def get_login(
        self,
        email: str,
        *,
        for_update: bool = False,
    ) -> LoginRecord | None:
        user_statement = select(UserRecord).where(UserRecord.email == email)
        if for_update:
            user_statement = user_statement.with_for_update()
        user_record = await self._session.scalar(user_statement)
        if user_record is None:
            return None
        credential_statement = select(PasswordCredentialRecord).where(
            PasswordCredentialRecord.user_id == user_record.id,
        )
        if for_update:
            credential_statement = credential_statement.with_for_update()
        credential_record = await self._session.scalar(credential_statement)
        if credential_record is None:
            return None
        return LoginRecord(
            user=self._user(user_record),
            credential=self._credential(credential_record),
        )

    async def consume_rate_limits(
        self,
        requests: list[RateLimitRequest],
        *,
        now: datetime,
        window: timedelta,
        block_for: timedelta,
    ) -> int | None:
        retry_after = 0
        for request in sorted(requests, key=lambda item: (item.scope, item.subject_hash)):
            await self._rate_limit_lock(request)
            key = (request.scope, request.subject_hash)
            record = await self._session.get(AuthRateLimitRecord, key)
            if record is None:
                self._session.add(
                    AuthRateLimitRecord(
                        scope=request.scope,
                        subject_hash=request.subject_hash,
                        window_started_at=now,
                        attempts=1,
                        blocked_until=None,
                        updated_at=now,
                    ),
                )
                continue

            record.updated_at = now
            if record.blocked_until is not None and record.blocked_until > now:
                retry_after = max(
                    retry_after,
                    ceil((record.blocked_until - now).total_seconds()),
                )
                continue

            if now >= record.window_started_at + window:
                record.window_started_at = now
                record.attempts = 1
                record.blocked_until = None
                continue

            record.attempts += 1
            if record.attempts > request.limit:
                record.blocked_until = now + block_for
                retry_after = max(retry_after, ceil(block_for.total_seconds()))

        return retry_after or None

    async def add_registration(
        self,
        user: User,
        credential: PasswordCredential,
        session: AuthSession,
        refresh_token: RefreshToken,
        email_action: EmailActionToken,
        outbox_message: EmailOutboxMessage,
    ) -> None:
        self._session.add(
            UserRecord(
                id=user.id,
                email=user.email,
                email_verified_at=user.email_verified_at,
                created_at=user.created_at,
                disabled_at=user.disabled_at,
            ),
        )
        await self._flush()
        self._session.add_all(
            [
                PasswordCredentialRecord(
                    user_id=credential.user_id,
                    password_hash=credential.password_hash,
                    password_changed_at=credential.password_changed_at,
                    created_at=credential.password_changed_at,
                ),
                self._session_record(session),
                self._email_action_record(email_action),
            ],
        )
        await self._flush()
        self._session.add_all(
            [
                self._refresh_record(refresh_token),
                self._outbox_record(outbox_message),
            ],
        )

    async def add_session(self, session: AuthSession, refresh_token: RefreshToken) -> None:
        self._session.add(self._session_record(session))
        await self._flush()
        self._session.add(self._refresh_record(refresh_token))

    async def enforce_active_session_cap(
        self,
        *,
        user_id: UUID,
        now: datetime,
        retain_active: int,
        reason: str,
    ) -> int:
        records = list(
            (
                await self._session.scalars(
                    select(AuthSessionRecord)
                    .where(
                        AuthSessionRecord.user_id == user_id,
                        AuthSessionRecord.revoked_at.is_(None),
                        AuthSessionRecord.expires_at > now,
                    )
                    .order_by(AuthSessionRecord.id)
                    .with_for_update(),
                )
            ).all(),
        )
        excess = max(0, len(records) - max(0, retain_active))
        if excess == 0:
            return 0
        oldest = sorted(records, key=lambda record: (record.created_at, record.id.int))[:excess]
        session_ids = [record.id for record in oldest]
        for record in oldest:
            record.revoked_at = now
            record.revocation_reason = reason
        await self._revoke_active_refresh_tokens(session_ids, now)
        return len(session_ids)

    async def update_password_hash(
        self,
        *,
        user_id: UUID,
        password_hash: str,
        changed_at: datetime,
    ) -> None:
        await self._session.execute(
            update(PasswordCredentialRecord)
            .where(PasswordCredentialRecord.user_id == user_id)
            .values(password_hash=password_hash, password_changed_at=changed_at),
        )

    async def get_active_session(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        now: datetime,
    ) -> AuthenticatedIdentity | None:
        record = await self._session.scalar(
            select(AuthSessionRecord).where(
                AuthSessionRecord.id == session_id,
                AuthSessionRecord.user_id == user_id,
                AuthSessionRecord.revoked_at.is_(None),
                AuthSessionRecord.expires_at > now,
            ),
        )
        if record is None:
            return None
        user_record = await self._session.get(UserRecord, user_id)
        if user_record is None or user_record.disabled_at is not None:
            return None
        return AuthenticatedIdentity(
            user=self._user(user_record),
            session=self._auth_session(record),
        )

    async def get_refresh_context(
        self,
        token_id: UUID,
        *,
        for_update: bool,
    ) -> RefreshContext | None:
        session_id = await self._session.scalar(
            select(RefreshTokenRecord.session_id).where(RefreshTokenRecord.id == token_id),
        )
        if session_id is None:
            return None
        session_statement = select(AuthSessionRecord).where(AuthSessionRecord.id == session_id)
        token_statement = select(RefreshTokenRecord).where(
            RefreshTokenRecord.id == token_id,
            RefreshTokenRecord.session_id == session_id,
        )
        if for_update:
            session_statement = session_statement.with_for_update()
            token_statement = token_statement.with_for_update()
        session_record = await self._session.scalar(session_statement)
        if session_record is None:
            return None
        token_record = await self._session.scalar(token_statement)
        if token_record is None:
            return None
        user_record = await self._session.get(UserRecord, session_record.user_id)
        if user_record is None:
            return None
        return RefreshContext(
            token=self._refresh_token(token_record),
            session=self._auth_session(session_record),
            user=self._user(user_record),
        )

    async def rotate_refresh(
        self,
        *,
        previous_token_id: UUID,
        replacement: RefreshToken,
        used_at: datetime,
        last_ip: str | None,
    ) -> None:
        previous = await self._session.get(RefreshTokenRecord, previous_token_id)
        session_record = await self._session.get(AuthSessionRecord, replacement.session_id)
        if previous is None or session_record is None:
            msg = "locked refresh state disappeared"
            raise RuntimeError(msg)
        self._session.add(self._refresh_record(replacement))
        await self._flush()
        previous.used_at = used_at
        previous.replaced_by_token_id = replacement.id
        session_record.last_seen_at = used_at
        session_record.last_ip = last_ip

    async def replace_email_action(
        self,
        *,
        token: EmailActionToken,
        outbox_message: EmailOutboxMessage,
        replaced_at: datetime,
    ) -> None:
        await self.revoke_email_actions(
            user_id=token.user_id,
            purpose=token.purpose,
            revoked_at=replaced_at,
        )
        self._session.add(self._email_action_record(token))
        await self._flush()
        self._session.add(self._outbox_record(outbox_message))

    async def get_email_action_context(
        self,
        token_id: UUID,
        *,
        purpose: EmailActionPurpose,
        for_update: bool,
    ) -> EmailActionContext | None:
        user_id = await self._session.scalar(
            select(EmailActionTokenRecord.user_id).where(
                EmailActionTokenRecord.id == token_id,
                EmailActionTokenRecord.purpose == str(purpose),
            ),
        )
        if user_id is None:
            return None
        user_statement = select(UserRecord).where(UserRecord.id == user_id)
        token_statement = select(EmailActionTokenRecord).where(
            EmailActionTokenRecord.id == token_id,
            EmailActionTokenRecord.user_id == user_id,
            EmailActionTokenRecord.purpose == str(purpose),
        )
        if for_update:
            user_statement = user_statement.with_for_update()
            token_statement = token_statement.with_for_update()
        user_record = await self._session.scalar(user_statement)
        if user_record is None:
            return None
        token_record = await self._session.scalar(token_statement)
        if token_record is None:
            return None
        return EmailActionContext(
            token=self._email_action_token(token_record),
            user=self._user(user_record),
        )

    async def mark_email_action_used(self, token_id: UUID, *, used_at: datetime) -> None:
        record = await self._session.get(EmailActionTokenRecord, token_id)
        if record is None:
            msg = "locked email action disappeared"
            raise RuntimeError(msg)
        record.used_at = used_at

    async def mark_email_verified(self, user_id: UUID, *, verified_at: datetime) -> None:
        record = await self._session.get(UserRecord, user_id)
        if record is None:
            msg = "locked identity disappeared"
            raise RuntimeError(msg)
        if record.email_verified_at is None:
            record.email_verified_at = verified_at

    async def revoke_email_actions(
        self,
        *,
        user_id: UUID,
        purpose: EmailActionPurpose,
        revoked_at: datetime,
        exclude_token_id: UUID | None = None,
    ) -> None:
        predicates = [
            EmailActionTokenRecord.user_id == user_id,
            EmailActionTokenRecord.purpose == str(purpose),
            EmailActionTokenRecord.used_at.is_(None),
            EmailActionTokenRecord.revoked_at.is_(None),
        ]
        if exclude_token_id is not None:
            predicates.append(EmailActionTokenRecord.id != exclude_token_id)
        await self._session.execute(
            update(EmailActionTokenRecord).where(*predicates).values(revoked_at=revoked_at),
        )

    async def list_active_sessions(
        self,
        *,
        user_id: UUID,
        now: datetime,
    ) -> list[AuthSession]:
        records = (
            await self._session.scalars(
                select(AuthSessionRecord)
                .where(
                    AuthSessionRecord.user_id == user_id,
                    AuthSessionRecord.revoked_at.is_(None),
                    AuthSessionRecord.expires_at > now,
                )
                .order_by(AuthSessionRecord.created_at.desc()),
            )
        ).all()
        return [self._auth_session(record) for record in records]

    async def revoke_session(
        self,
        *,
        session_id: UUID,
        user_id: UUID | None,
        revoked_at: datetime,
        reason: str,
    ) -> bool:
        predicates = [AuthSessionRecord.id == session_id]
        if user_id is not None:
            predicates.append(AuthSessionRecord.user_id == user_id)
        record = await self._session.scalar(
            select(AuthSessionRecord).where(*predicates).with_for_update(),
        )
        if record is None:
            return False
        if record.revoked_at is None:
            record.revoked_at = revoked_at
            record.revocation_reason = reason
            await self._revoke_active_refresh_tokens([session_id], revoked_at)
        return True

    async def revoke_all_sessions(
        self,
        *,
        user_id: UUID,
        revoked_at: datetime,
        reason: str,
    ) -> int:
        records = list(
            (
                await self._session.scalars(
                    select(AuthSessionRecord)
                    .where(
                        AuthSessionRecord.user_id == user_id,
                        AuthSessionRecord.revoked_at.is_(None),
                    )
                    .order_by(AuthSessionRecord.id)
                    .with_for_update(),
                )
            ).all(),
        )
        session_ids = [record.id for record in records]
        for record in records:
            record.revoked_at = revoked_at
            record.revocation_reason = reason
        await self._revoke_active_refresh_tokens(session_ids, revoked_at)
        return len(records)

    async def _rate_limit_lock(self, request: RateLimitRequest) -> None:
        bind = self._session.get_bind()
        if bind.dialect.name != "postgresql":
            return
        lock_key = int.from_bytes(
            bytes.fromhex(request.subject_hash[:16]),
            byteorder="big",
            signed=True,
        )
        await self._session.execute(select(func.pg_advisory_xact_lock(lock_key)))

    async def _revoke_active_refresh_tokens(
        self,
        session_ids: list[UUID],
        revoked_at: datetime,
    ) -> None:
        if not session_ids:
            return
        await self._session.execute(
            update(RefreshTokenRecord)
            .where(
                RefreshTokenRecord.session_id.in_(session_ids),
                RefreshTokenRecord.used_at.is_(None),
                RefreshTokenRecord.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at),
        )

    async def _flush(self) -> None:
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise IdentityConflict from exc

    async def commit(self) -> None:
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise IdentityConflict from exc

    async def rollback(self) -> None:
        await self._session.rollback()

    @staticmethod
    def _user(record: UserRecord) -> User:
        return User(
            id=record.id,
            email=record.email,
            email_verified_at=record.email_verified_at,
            created_at=record.created_at,
            disabled_at=record.disabled_at,
        )

    @staticmethod
    def _credential(record: PasswordCredentialRecord) -> PasswordCredential:
        return PasswordCredential(
            user_id=record.user_id,
            password_hash=record.password_hash,
            password_changed_at=record.password_changed_at,
        )

    @staticmethod
    def _auth_session(record: AuthSessionRecord) -> AuthSession:
        return AuthSession(
            id=record.id,
            user_id=record.user_id,
            csrf_token_hash=record.csrf_token_hash,
            created_at=record.created_at,
            last_seen_at=record.last_seen_at,
            expires_at=record.expires_at,
            revoked_at=record.revoked_at,
            revocation_reason=record.revocation_reason,
            created_ip=record.created_ip,
            last_ip=record.last_ip,
            user_agent=record.user_agent,
        )

    @staticmethod
    def _refresh_token(record: RefreshTokenRecord) -> RefreshToken:
        return RefreshToken(
            id=record.id,
            session_id=record.session_id,
            token_hash=record.token_hash,
            created_at=record.created_at,
            expires_at=record.expires_at,
            used_at=record.used_at,
            revoked_at=record.revoked_at,
            replaced_by_token_id=record.replaced_by_token_id,
        )

    @staticmethod
    def _email_action_token(record: EmailActionTokenRecord) -> EmailActionToken:
        return EmailActionToken(
            id=record.id,
            user_id=record.user_id,
            purpose=EmailActionPurpose(record.purpose),
            token_hash=record.token_hash,
            created_at=record.created_at,
            expires_at=record.expires_at,
            used_at=record.used_at,
            revoked_at=record.revoked_at,
        )

    @staticmethod
    def _session_record(session: AuthSession) -> AuthSessionRecord:
        return AuthSessionRecord(
            id=session.id,
            user_id=session.user_id,
            csrf_token_hash=session.csrf_token_hash,
            created_at=session.created_at,
            last_seen_at=session.last_seen_at,
            expires_at=session.expires_at,
            revoked_at=session.revoked_at,
            revocation_reason=session.revocation_reason,
            created_ip=session.created_ip,
            last_ip=session.last_ip,
            user_agent=session.user_agent,
        )

    @staticmethod
    def _refresh_record(token: RefreshToken) -> RefreshTokenRecord:
        return RefreshTokenRecord(
            id=token.id,
            session_id=token.session_id,
            token_hash=token.token_hash,
            created_at=token.created_at,
            expires_at=token.expires_at,
            used_at=token.used_at,
            revoked_at=token.revoked_at,
            replaced_by_token_id=token.replaced_by_token_id,
        )

    @staticmethod
    def _email_action_record(token: EmailActionToken) -> EmailActionTokenRecord:
        return EmailActionTokenRecord(
            id=token.id,
            user_id=token.user_id,
            purpose=str(token.purpose),
            token_hash=token.token_hash,
            created_at=token.created_at,
            expires_at=token.expires_at,
            used_at=token.used_at,
            revoked_at=token.revoked_at,
        )

    @staticmethod
    def _outbox_record(message: EmailOutboxMessage) -> EmailOutboxRecord:
        return EmailOutboxRecord(
            id=message.id,
            action_token_id=message.action_token_id,
            recipient=message.recipient,
            purpose=str(message.purpose),
            encrypted_payload=message.encrypted_payload,
            created_at=message.created_at,
            available_at=message.available_at,
            attempts=0,
            sent_at=None,
            lease_id=None,
            leased_until=None,
            last_failure_code=None,
        )

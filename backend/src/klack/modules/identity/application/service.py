"""Registration, login, access authentication, and session lifecycle use cases."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from hmac import compare_digest
from math import ceil
from uuid import UUID, uuid4

import structlog

from klack.modules.identity.application.ports import (
    IdentityConflict,
    IdentityRepository,
    RateLimitRequest,
)
from klack.modules.identity.domain.email import normalize_email
from klack.modules.identity.domain.entities import (
    AuthenticatedIdentity,
    AuthSession,
    EmailActionContext,
    EmailActionPurpose,
    EmailActionToken,
    EmailOutboxMessage,
    PasswordCredential,
    RefreshContext,
    RefreshToken,
    SessionMetadata,
    User,
)
from klack.modules.identity.domain.errors import (
    AuthenticationRateLimited,
    AuthenticationRequired,
    CsrfValidationFailed,
    EmailAlreadyRegistered,
    InvalidCredentials,
    InvalidEmailActionToken,
    InvalidEmailAddress,
    InvalidPassword,
    RefreshRateLimited,
    RefreshTokenReuseDetected,
    SessionExpired,
    SessionNotFound,
)
from klack.modules.identity.infrastructure.action_security import ActionTokenManager
from klack.modules.identity.infrastructure.security import (
    AccessTokenCodec,
    Clock,
    PasswordManager,
    SessionTokenManager,
    UuidFactory,
    utc_now,
)

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AuthenticationResult:
    """Public identity/session state plus browser-only credential presentations."""

    identity: AuthenticatedIdentity
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime
    csrf_token: str


@dataclass(frozen=True, slots=True)
class IdentityPolicy:
    """Durations controlled by validated application configuration."""

    refresh_ttl: timedelta
    refresh_min_interval: timedelta
    max_refresh_token_rows_per_session: int = 10_000
    max_active_sessions: int = 10
    email_verification_ttl: timedelta = timedelta(days=1)
    password_recovery_ttl: timedelta = timedelta(hours=1)
    rate_limit_window: timedelta = timedelta(minutes=15)
    rate_limit_block: timedelta = timedelta(minutes=15)
    registration_rate_limit: int = 5
    login_rate_limit: int = 10
    email_action_rate_limit: int = 5
    action_complete_rate_limit: int = 10


class IdentityService:
    """Own identity transaction intent independently from FastAPI and SQLAlchemy."""

    def __init__(
        self,
        *,
        repository: IdentityRepository,
        passwords: PasswordManager,
        access_tokens: AccessTokenCodec,
        session_tokens: SessionTokenManager,
        action_tokens: ActionTokenManager,
        policy: IdentityPolicy,
        clock: Clock = utc_now,
        uuid_factory: UuidFactory = uuid4,
    ) -> None:
        self._repository = repository
        self._passwords = passwords
        self._access_tokens = access_tokens
        self._session_tokens = session_tokens
        self._action_tokens = action_tokens
        self._policy = policy
        self._clock = clock
        self._uuid_factory = uuid_factory

    async def register(
        self,
        *,
        email: str,
        password: str,
        metadata: SessionMetadata,
    ) -> AuthenticationResult:
        """Create a unique identity and its first authenticated session atomically."""
        normalized_email = normalize_email(email)
        await self._enforce_rate_limit(
            scope="registration",
            subject=normalized_email,
            metadata=metadata,
            limit=self._policy.registration_rate_limit,
        )
        self._validate_registration_password(password)
        password_hash = await self._passwords.hash_async(password)
        if await self._repository.email_exists(normalized_email):
            raise EmailAlreadyRegistered

        now = self._clock()
        user = User(
            id=self._uuid_factory(),
            email=normalized_email,
            email_verified_at=None,
            created_at=now,
            disabled_at=None,
        )
        credential = PasswordCredential(
            user_id=user.id,
            password_hash=password_hash,
            password_changed_at=now,
        )
        identity, refresh_token, raw_refresh_token, csrf_token = self._new_session(
            user=user,
            metadata=metadata,
            now=now,
        )
        email_action, outbox_message = self._new_email_action(
            user=user,
            purpose=EmailActionPurpose.VERIFY_EMAIL,
            now=now,
        )
        try:
            await self._repository.add_registration(
                user,
                credential,
                identity.session,
                refresh_token,
                email_action,
                outbox_message,
            )
            await self._repository.commit()
        except IdentityConflict:
            raise EmailAlreadyRegistered from None

        logger.info(
            "identity_registered",
            user_id=str(user.id),
            session_id=str(identity.session.id),
        )
        return self._authentication_result(
            identity,
            refresh_token,
            raw_refresh_token,
            csrf_token,
        )

    async def login(
        self,
        *,
        email: str,
        password: str,
        metadata: SessionMetadata,
    ) -> AuthenticationResult:
        """Verify a password without revealing account existence and create a new session."""
        try:
            normalized_email = normalize_email(email)
        except InvalidEmailAddress:
            await self._enforce_rate_limit(
                scope="login",
                subject=email.strip().casefold()[:320],
                metadata=metadata,
                limit=self._policy.login_rate_limit,
            )
            await self._passwords.verify_dummy_async(password)
            raise InvalidCredentials from None

        await self._enforce_rate_limit(
            scope="login",
            subject=normalized_email,
            metadata=metadata,
            limit=self._policy.login_rate_limit,
        )
        login = await self._repository.get_login(normalized_email)
        await self._repository.rollback()
        if login is None:
            await self._passwords.verify_dummy_async(password)
            raise InvalidCredentials

        verification = await self._passwords.verify_async(
            password,
            login.credential.password_hash,
        )
        if not verification.valid or login.user.disabled_at is not None:
            raise InvalidCredentials
        replacement_hash = (
            await self._passwords.hash_async(password) if verification.needs_rehash else None
        )

        current_login = await self._repository.get_login(normalized_email, for_update=True)
        if current_login is None or current_login.user.disabled_at is not None:
            raise InvalidCredentials
        if not compare_digest(
            current_login.credential.password_hash,
            login.credential.password_hash,
        ):
            observed_hash = current_login.credential.password_hash
            await self._repository.rollback()
            current_verification = await self._passwords.verify_async(
                password,
                observed_hash,
            )
            if not current_verification.valid:
                raise InvalidCredentials
            replacement_hash = (
                await self._passwords.hash_async(password)
                if current_verification.needs_rehash
                else None
            )
            current_login = await self._repository.get_login(
                normalized_email,
                for_update=True,
            )
            if (
                current_login is None
                or current_login.user.disabled_at is not None
                or not compare_digest(
                    current_login.credential.password_hash,
                    observed_hash,
                )
            ):
                raise InvalidCredentials

        now = self._clock()
        revoked_for_cap = await self._repository.enforce_active_session_cap(
            user_id=current_login.user.id,
            now=now,
            retain_active=self._policy.max_active_sessions - 1,
            reason="session_limit",
        )
        identity, refresh_token, raw_refresh_token, csrf_token = self._new_session(
            user=current_login.user,
            metadata=metadata,
            now=now,
        )
        await self._repository.add_session(identity.session, refresh_token)
        if replacement_hash is not None:
            await self._repository.update_password_hash(
                user_id=current_login.user.id,
                password_hash=replacement_hash,
                changed_at=now,
            )
        await self._repository.commit()

        logger.info(
            "identity_authenticated",
            user_id=str(current_login.user.id),
            session_id=str(identity.session.id),
            sessions_revoked_for_cap=revoked_for_cap,
        )
        return self._authentication_result(
            identity,
            refresh_token,
            raw_refresh_token,
            csrf_token,
        )

    async def request_email_verification(
        self,
        *,
        identity: AuthenticatedIdentity,
        csrf_token: str | None,
        metadata: SessionMetadata,
    ) -> None:
        """Queue a replacement verification email for an unverified identity."""
        self.require_csrf(identity=identity, csrf_token=csrf_token)
        await self._enforce_rate_limit(
            scope="email_verification_request",
            subject=str(identity.user.id),
            metadata=metadata,
            limit=self._policy.email_action_rate_limit,
        )
        user = await self._repository.get_user_by_email(
            identity.user.email,
            for_update=True,
        )
        if user is None or user.disabled_at is not None:
            raise AuthenticationRequired
        if user.email_verified:
            await self._repository.rollback()
            return
        now = self._clock()
        token, outbox = self._new_email_action(
            user=user,
            purpose=EmailActionPurpose.VERIFY_EMAIL,
            now=now,
        )
        await self._repository.replace_email_action(
            token=token,
            outbox_message=outbox,
            replaced_at=now,
        )
        await self._repository.commit()
        logger.info("email_verification_queued", user_id=str(user.id))

    async def complete_email_verification(
        self,
        *,
        raw_token: str | None,
        metadata: SessionMetadata,
    ) -> None:
        """Consume one verification token and mark the mailbox verified."""
        await self._enforce_rate_limit(
            scope="email_action_complete",
            subject=(raw_token or "missing")[:64],
            metadata=metadata,
            limit=self._policy.action_complete_rate_limit,
        )
        purpose = EmailActionPurpose.VERIFY_EMAIL
        presented = self._action_tokens.present(raw_token, purpose)
        context = await self._repository.get_email_action_context(
            presented.token_id,
            purpose=purpose,
            for_update=True,
        )
        now = self._clock()
        self._require_active_email_action(context, presented.digest, now)
        assert context is not None
        await self._repository.mark_email_verified(context.user.id, verified_at=now)
        await self._repository.mark_email_action_used(context.token.id, used_at=now)
        await self._repository.revoke_email_actions(
            user_id=context.user.id,
            purpose=purpose,
            revoked_at=now,
            exclude_token_id=context.token.id,
        )
        await self._repository.commit()
        logger.info("email_verified", user_id=str(context.user.id))

    async def request_password_recovery(
        self,
        *,
        email: str,
        metadata: SessionMetadata,
    ) -> None:
        """Queue a generic password-recovery response without revealing account existence."""
        normalized_email: str | None
        try:
            normalized_email = normalize_email(email)
        except InvalidEmailAddress:
            normalized_email = None
            subject = email.strip().casefold()[:320]
        else:
            subject = normalized_email
        await self._enforce_rate_limit(
            scope="password_recovery_request",
            subject=subject,
            metadata=metadata,
            limit=self._policy.email_action_rate_limit,
        )
        if normalized_email is None:
            return
        user = await self._repository.get_user_by_email(normalized_email, for_update=True)
        if user is None or user.disabled_at is not None:
            await self._repository.rollback()
            return
        now = self._clock()
        token, outbox = self._new_email_action(
            user=user,
            purpose=EmailActionPurpose.RECOVER_PASSWORD,
            now=now,
        )
        await self._repository.replace_email_action(
            token=token,
            outbox_message=outbox,
            replaced_at=now,
        )
        await self._repository.commit()
        logger.info("password_recovery_queued", user_id=str(user.id))

    async def complete_password_recovery(
        self,
        *,
        raw_token: str | None,
        new_password: str,
        metadata: SessionMetadata,
    ) -> None:
        """Replace a password through a single-use email token and revoke every session."""
        self._validate_registration_password(new_password)
        await self._enforce_rate_limit(
            scope="password_recovery_complete",
            subject=(raw_token or "missing")[:64],
            metadata=metadata,
            limit=self._policy.action_complete_rate_limit,
        )
        purpose = EmailActionPurpose.RECOVER_PASSWORD
        presented = self._action_tokens.present(raw_token, purpose)
        snapshot = await self._repository.get_email_action_context(
            presented.token_id,
            purpose=purpose,
            for_update=False,
        )
        now = self._clock()
        self._require_active_email_action(snapshot, presented.digest, now)
        await self._repository.rollback()
        password_hash = await self._passwords.hash_async(new_password)

        context = await self._repository.get_email_action_context(
            presented.token_id,
            purpose=purpose,
            for_update=True,
        )
        now = self._clock()
        self._require_active_email_action(context, presented.digest, now)
        assert context is not None
        await self._repository.update_password_hash(
            user_id=context.user.id,
            password_hash=password_hash,
            changed_at=now,
        )
        await self._repository.mark_email_verified(context.user.id, verified_at=now)
        await self._repository.mark_email_action_used(context.token.id, used_at=now)
        await self._repository.revoke_email_actions(
            user_id=context.user.id,
            purpose=purpose,
            revoked_at=now,
            exclude_token_id=context.token.id,
        )
        revoked_sessions = await self._repository.revoke_all_sessions(
            user_id=context.user.id,
            revoked_at=now,
            reason="password_recovery",
        )
        await self._repository.commit()
        logger.info(
            "password_recovered",
            user_id=str(context.user.id),
            revoked_session_count=revoked_sessions,
        )

    async def authenticate_access(self, raw_access_token: str | None) -> AuthenticatedIdentity:
        """Resolve a signed access token through current durable account/session state."""
        if raw_access_token is None:
            raise AuthenticationRequired
        claims = self._access_tokens.decode(raw_access_token)
        identity = await self._repository.get_active_session(
            session_id=claims.session_id,
            user_id=claims.user_id,
            now=self._clock(),
        )
        if identity is None:
            raise AuthenticationRequired
        return identity

    async def refresh(
        self,
        *,
        raw_refresh_token: str | None,
        csrf_token: str | None,
        metadata: SessionMetadata,
    ) -> AuthenticationResult:
        """Rotate one refresh token under a row lock and revoke its family on replay."""
        context = await self._locked_refresh_context(raw_refresh_token)
        now = self._clock()
        if context.token.used_at is not None:
            await self._repository.revoke_session(
                session_id=context.session.id,
                user_id=None,
                revoked_at=now,
                reason="refresh_reuse",
            )
            await self._repository.commit()
            logger.warning(
                "refresh_token_reuse_detected",
                user_id=str(context.user.id),
                session_id=str(context.session.id),
                refresh_token_id=str(context.token.id),
            )
            raise RefreshTokenReuseDetected

        if csrf_token is None or not self._session_tokens.csrf_matches(
            csrf_token,
            context.session.csrf_token_hash,
        ):
            raise CsrfValidationFailed

        if (
            context.token.revoked_at is not None
            or context.token.expires_at <= now
            or not context.session.is_active(now)
            or context.user.disabled_at is not None
        ):
            raise SessionExpired

        storage_bound_interval = timedelta(
            seconds=ceil(
                (context.session.expires_at - context.session.created_at).total_seconds()
                / self._policy.max_refresh_token_rows_per_session,
            ),
        )
        effective_interval = max(
            self._policy.refresh_min_interval,
            storage_bound_interval,
        )
        refresh_allowed_at = context.session.last_seen_at + effective_interval
        if refresh_allowed_at > now:
            retry_after_seconds = max(
                1,
                ceil((refresh_allowed_at - now).total_seconds()),
            )
            raise RefreshRateLimited(retry_after_seconds)

        issued_refresh = self._session_tokens.issue_refresh()
        replacement = RefreshToken(
            id=issued_refresh.token_id,
            session_id=context.session.id,
            token_hash=issued_refresh.digest,
            created_at=now,
            expires_at=context.session.expires_at,
            used_at=None,
            revoked_at=None,
            replaced_by_token_id=None,
        )
        await self._repository.rotate_refresh(
            previous_token_id=context.token.id,
            replacement=replacement,
            used_at=now,
            last_ip=metadata.ip_address,
        )
        await self._repository.commit()

        refreshed_identity = AuthenticatedIdentity(
            user=context.user,
            session=replace(
                context.session,
                last_seen_at=now,
                last_ip=metadata.ip_address,
            ),
        )
        access = self._access_tokens.issue(
            user_id=context.user.id,
            session_id=context.session.id,
        )
        logger.info(
            "session_refreshed",
            user_id=str(context.user.id),
            session_id=str(context.session.id),
        )
        return AuthenticationResult(
            identity=refreshed_identity,
            access_token=access.raw,
            access_expires_at=access.expires_at,
            refresh_token=issued_refresh.raw,
            refresh_expires_at=replacement.expires_at,
            csrf_token=csrf_token,
        )

    async def logout(
        self,
        *,
        raw_access_token: str | None,
        raw_refresh_token: str | None,
        csrf_token: str | None,
    ) -> None:
        """Idempotently revoke the current session using access or refresh credentials."""
        identity: AuthenticatedIdentity | None = None
        try:
            identity = await self.authenticate_access(raw_access_token)
        except AuthenticationRequired:
            identity = await self._identity_for_logout(raw_refresh_token)

        if identity is None:
            await self._repository.rollback()
            return
        self.require_csrf(identity=identity, csrf_token=csrf_token)
        await self._repository.revoke_session(
            session_id=identity.session.id,
            user_id=identity.user.id,
            revoked_at=self._clock(),
            reason="logout",
        )
        await self._repository.commit()
        logger.info(
            "session_revoked",
            user_id=str(identity.user.id),
            session_id=str(identity.session.id),
            reason="logout",
        )

    async def logout_all(
        self,
        *,
        identity: AuthenticatedIdentity,
        csrf_token: str | None,
    ) -> None:
        """Revoke every active session owned by the authenticated user."""
        self.require_csrf(identity=identity, csrf_token=csrf_token)
        count = await self._repository.revoke_all_sessions(
            user_id=identity.user.id,
            revoked_at=self._clock(),
            reason="logout_all",
        )
        await self._repository.commit()
        logger.info(
            "all_sessions_revoked",
            user_id=str(identity.user.id),
            revoked_session_count=count,
        )

    async def list_sessions(self, identity: AuthenticatedIdentity) -> list[AuthSession]:
        """List the user's currently active sessions newest first."""
        return await self._repository.list_active_sessions(
            user_id=identity.user.id,
            now=self._clock(),
        )

    async def revoke_session(
        self,
        *,
        identity: AuthenticatedIdentity,
        session_id: UUID,
        csrf_token: str | None,
    ) -> bool:
        """Revoke one owned session and report whether it was the current login."""
        self.require_csrf(identity=identity, csrf_token=csrf_token)
        found = await self._repository.revoke_session(
            session_id=session_id,
            user_id=identity.user.id,
            revoked_at=self._clock(),
            reason="user_revoked",
        )
        if not found:
            raise SessionNotFound
        await self._repository.commit()
        logger.info(
            "session_revoked",
            user_id=str(identity.user.id),
            session_id=str(session_id),
            reason="user_revoked",
        )
        return session_id == identity.session.id

    def require_csrf(
        self,
        *,
        identity: AuthenticatedIdentity,
        csrf_token: str | None,
    ) -> None:
        """Validate a header/cookie value against the current session digest."""
        if not self._session_tokens.csrf_matches(
            csrf_token,
            identity.session.csrf_token_hash,
        ):
            raise CsrfValidationFailed

    async def _enforce_rate_limit(
        self,
        *,
        scope: str,
        subject: str,
        metadata: SessionMetadata,
        limit: int,
    ) -> None:
        requests = [
            RateLimitRequest(
                scope=f"{scope}:subject",
                subject_hash=self._action_tokens.rate_limit_key(scope, subject),
                limit=limit,
            ),
            RateLimitRequest(
                scope=f"{scope}:ip",
                subject_hash=self._action_tokens.rate_limit_key(
                    f"{scope}:ip",
                    metadata.ip_address or "unknown",
                ),
                limit=limit,
            ),
        ]
        retry_after = await self._repository.consume_rate_limits(
            requests,
            now=self._clock(),
            window=self._policy.rate_limit_window,
            block_for=self._policy.rate_limit_block,
        )
        await self._repository.commit()
        if retry_after is not None:
            raise AuthenticationRateLimited(retry_after)

    def _new_email_action(
        self,
        *,
        user: User,
        purpose: EmailActionPurpose,
        now: datetime,
    ) -> tuple[EmailActionToken, EmailOutboxMessage]:
        issued = self._action_tokens.issue(purpose)
        ttl = (
            self._policy.email_verification_ttl
            if purpose is EmailActionPurpose.VERIFY_EMAIL
            else self._policy.password_recovery_ttl
        )
        token = EmailActionToken(
            id=issued.token_id,
            user_id=user.id,
            purpose=purpose,
            token_hash=issued.digest,
            created_at=now,
            expires_at=now + ttl,
            used_at=None,
            revoked_at=None,
        )
        outbox = EmailOutboxMessage(
            id=self._uuid_factory(),
            action_token_id=token.id,
            recipient=user.email,
            purpose=purpose,
            encrypted_payload=self._action_tokens.seal_email_action(
                raw_token=issued.raw,
                recipient=user.email,
                purpose=purpose,
            ),
            created_at=now,
            available_at=now,
        )
        return token, outbox

    def _require_active_email_action(
        self,
        context: EmailActionContext | None,
        presented_digest: str,
        now: datetime,
    ) -> None:
        if (
            context is None
            or context.user.disabled_at is not None
            or not context.token.is_active(now)
            or not self._action_tokens.matches(
                presented_digest,
                context.token.token_hash,
            )
        ):
            raise InvalidEmailActionToken

    async def _locked_refresh_context(
        self,
        raw_refresh_token: str | None,
    ) -> RefreshContext:
        if raw_refresh_token is None:
            raise SessionExpired
        presented = self._session_tokens.present_refresh(raw_refresh_token)
        context = await self._repository.get_refresh_context(
            presented.token_id,
            for_update=True,
        )
        if context is None or not self._session_tokens.refresh_matches(
            presented.digest,
            context.token.token_hash,
        ):
            raise SessionExpired
        return context

    async def _identity_for_logout(
        self,
        raw_refresh_token: str | None,
    ) -> AuthenticatedIdentity | None:
        if raw_refresh_token is None:
            return None
        try:
            presented = self._session_tokens.present_refresh(raw_refresh_token)
        except SessionExpired:
            return None
        context = await self._repository.get_refresh_context(
            presented.token_id,
            for_update=True,
        )
        now = self._clock()
        if (
            context is None
            or not self._session_tokens.refresh_matches(
                presented.digest,
                context.token.token_hash,
            )
            or not context.session.is_active(now)
            or context.user.disabled_at is not None
        ):
            return None
        return AuthenticatedIdentity(user=context.user, session=context.session)

    def _new_session(
        self,
        *,
        user: User,
        metadata: SessionMetadata,
        now: datetime,
    ) -> tuple[AuthenticatedIdentity, RefreshToken, str, str]:
        csrf = self._session_tokens.issue_csrf()
        issued_refresh = self._session_tokens.issue_refresh()
        expires_at = now + self._policy.refresh_ttl
        session = AuthSession(
            id=self._uuid_factory(),
            user_id=user.id,
            csrf_token_hash=csrf.digest,
            created_at=now,
            last_seen_at=now,
            expires_at=expires_at,
            revoked_at=None,
            revocation_reason=None,
            created_ip=metadata.ip_address,
            last_ip=metadata.ip_address,
            user_agent=metadata.user_agent,
        )
        refresh_token = RefreshToken(
            id=issued_refresh.token_id,
            session_id=session.id,
            token_hash=issued_refresh.digest,
            created_at=now,
            expires_at=expires_at,
            used_at=None,
            revoked_at=None,
            replaced_by_token_id=None,
        )
        return (
            AuthenticatedIdentity(user=user, session=session),
            refresh_token,
            issued_refresh.raw,
            csrf.raw,
        )

    def _authentication_result(
        self,
        identity: AuthenticatedIdentity,
        refresh_token: RefreshToken,
        raw_refresh_token: str,
        csrf_token: str,
    ) -> AuthenticationResult:
        access = self._access_tokens.issue(
            user_id=identity.user.id,
            session_id=identity.session.id,
        )
        return AuthenticationResult(
            identity=identity,
            access_token=access.raw,
            access_expires_at=access.expires_at,
            refresh_token=raw_refresh_token,
            refresh_expires_at=refresh_token.expires_at,
            csrf_token=csrf_token,
        )

    @staticmethod
    def _validate_registration_password(password: str) -> None:
        if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
            raise InvalidPassword

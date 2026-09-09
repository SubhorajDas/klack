"""Expected identity failures that API adapters translate safely."""


class IdentityError(Exception):
    """Base class for an expected identity operation failure."""


class InvalidEmailAddress(IdentityError, ValueError):
    """The supplied email cannot be normalized into a supported mailbox."""


class InvalidPassword(IdentityError, ValueError):
    """A registration or replacement password is outside accepted bounds."""


class EmailAlreadyRegistered(IdentityError):
    """Registration collided with an existing normalized email."""


class InvalidCredentials(IdentityError):
    """Login credentials did not identify an enabled account."""


class AuthenticationRequired(IdentityError):
    """No valid active access session was presented."""


class SessionExpired(IdentityError):
    """A refresh or logout credential no longer identifies an active session."""


class RefreshTokenReuseDetected(IdentityError):
    """A consumed refresh token was replayed and its session was revoked."""


class RefreshRateLimited(IdentityError):
    """A valid refresh was attempted before the session rotation cooldown elapsed."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("refresh rotation cooldown has not elapsed")
        self.retry_after_seconds = retry_after_seconds


class AuthenticationRateLimited(IdentityError):
    """A shared authentication throttle rejected an operation."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("authentication rate limit exceeded")
        self.retry_after_seconds = retry_after_seconds


class InvalidEmailActionToken(IdentityError):
    """An email verification or password recovery token is invalid or expired."""


class CsrfValidationFailed(IdentityError):
    """A cookie-authenticated unsafe request failed CSRF validation."""


class OriginNotAllowed(IdentityError):
    """An unsafe browser request did not come from the configured exact origin."""


class SessionNotFound(IdentityError):
    """The requested session is not owned by the authenticated user."""

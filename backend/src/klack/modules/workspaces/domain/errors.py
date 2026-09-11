"""Expected workspace failures that API adapters translate safely."""


class WorkspaceError(Exception):
    """Base class for an expected workspace operation failure."""


class InvalidWorkspaceName(WorkspaceError, ValueError):
    """The supplied workspace name is empty or exceeds the supported bound."""


class WorkspaceNotFound(WorkspaceError):
    """The workspace does not exist or is intentionally hidden from this actor."""


class WorkspacePermissionDenied(WorkspaceError):
    """The actor is a member but their current role cannot perform the operation."""


class MembershipNotFound(WorkspaceError):
    """The target user is not a member of the visible workspace."""


class MembershipAlreadyExists(WorkspaceError):
    """The invitation recipient already belongs to the workspace."""


class OwnerInvariantViolation(WorkspaceError):
    """The operation would leave a workspace without an owner."""


class InvitationNotFound(WorkspaceError):
    """The requested invitation does not belong to the visible workspace."""


class InvitationNotActive(WorkspaceError):
    """The requested invitation is expired, revoked, or already consumed."""


class InvalidInvitationToken(WorkspaceError):
    """A bearer invitation is malformed, unknown, mismatched, or inactive."""

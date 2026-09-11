"""Persistence-agnostic workspace, membership, and invitation values."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class WorkspaceRole(StrEnum):
    """A member's authority within one workspace."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


@dataclass(frozen=True, slots=True)
class Workspace:
    """A collaboration boundary owned by one or more members."""

    id: UUID
    name: str
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class WorkspaceMembership:
    """A user's current role in one workspace."""

    workspace_id: UUID
    user_id: UUID
    role: WorkspaceRole
    joined_at: datetime


@dataclass(frozen=True, slots=True)
class WorkspaceInvitation:
    """Stored HMAC-only state for one manually shared invitation link."""

    id: UUID
    workspace_id: UUID
    created_by_user_id: UUID
    token_hash: str
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None
    accepted_by_user_id: UUID | None
    revoked_at: datetime | None
    revoked_by_user_id: UUID | None

    def is_active(self, now: datetime) -> bool:
        """Return whether this invitation may still be managed or consumed."""
        return self.accepted_at is None and self.revoked_at is None and self.expires_at > now

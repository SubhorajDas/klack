"""Persistence and invitation-security ports used by workspace services."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from klack.modules.workspaces.domain.entities import (
    Workspace,
    WorkspaceInvitation,
    WorkspaceMembership,
    WorkspaceRole,
)


class WorkspaceConflict(Exception):
    """A database uniqueness invariant rejected workspace state."""


@dataclass(frozen=True, slots=True)
class IssuedInvitationToken:
    """A new bearer presentation and its persistence-safe digest."""

    token_id: UUID
    raw: str
    digest: str


@dataclass(frozen=True, slots=True)
class PresentedInvitationToken:
    """A parsed bearer presentation suitable for a constant-time comparison."""

    token_id: UUID
    digest: str


class InvitationTokenManager(Protocol):
    """Issue and verify purpose-bound invitation bearer credentials."""

    def issue(self) -> IssuedInvitationToken: ...

    def present(self, raw_token: str | None) -> PresentedInvitationToken: ...

    def matches(self, presented_digest: str, stored_digest: str) -> bool: ...


class WorkspaceRepository(Protocol):
    """Transactions and durable state required by workspace use cases."""

    async def add_workspace(
        self,
        workspace: Workspace,
        owner_membership: WorkspaceMembership,
    ) -> None: ...

    async def list_workspaces(self, *, user_id: UUID) -> list[Workspace]: ...

    async def get_workspace(
        self,
        workspace_id: UUID,
        *,
        for_update: bool = False,
    ) -> Workspace | None: ...

    async def update_workspace_name(
        self,
        *,
        workspace_id: UUID,
        name: str,
        updated_at: datetime,
    ) -> None: ...

    async def get_membership(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        for_update: bool = False,
    ) -> WorkspaceMembership | None: ...

    async def list_memberships(
        self,
        *,
        workspace_id: UUID,
    ) -> list[WorkspaceMembership]: ...

    async def count_memberships_by_role(
        self,
        *,
        workspace_id: UUID,
        role: WorkspaceRole,
    ) -> int: ...

    async def update_membership_role(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        role: WorkspaceRole,
    ) -> None: ...

    async def remove_membership(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
    ) -> None: ...

    async def add_invitation(self, invitation: WorkspaceInvitation) -> None: ...

    async def list_invitations(
        self,
        *,
        workspace_id: UUID,
    ) -> list[WorkspaceInvitation]: ...

    async def get_invitation_workspace_id(self, invitation_id: UUID) -> UUID | None: ...

    async def get_invitation(
        self,
        *,
        workspace_id: UUID,
        invitation_id: UUID,
        for_update: bool = False,
    ) -> WorkspaceInvitation | None: ...

    async def revoke_invitation(
        self,
        *,
        invitation_id: UUID,
        revoked_at: datetime,
        revoked_by_user_id: UUID,
    ) -> None: ...

    async def accept_invitation(
        self,
        *,
        invitation_id: UUID,
        membership: WorkspaceMembership,
        accepted_at: datetime,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

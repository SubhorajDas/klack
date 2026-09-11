"""Workspace, membership, authorization, and invitation use cases."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from klack.modules.workspaces.application.ports import (
    InvitationTokenManager,
    WorkspaceConflict,
    WorkspaceRepository,
)
from klack.modules.workspaces.domain.entities import (
    Workspace,
    WorkspaceInvitation,
    WorkspaceMembership,
    WorkspaceRole,
)
from klack.modules.workspaces.domain.errors import (
    InvalidInvitationToken,
    InvalidWorkspaceName,
    InvitationNotActive,
    InvitationNotFound,
    MembershipAlreadyExists,
    MembershipNotFound,
    OwnerInvariantViolation,
    WorkspaceNotFound,
    WorkspacePermissionDenied,
)


def utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class WorkspacePolicy:
    """Validated workspace name and invitation limits."""

    invitation_ttl: timedelta = timedelta(days=7)
    minimum_name_length: int = 1
    maximum_name_length: int = 100

    def __post_init__(self) -> None:
        if self.invitation_ttl <= timedelta(0):
            raise ValueError("invitation_ttl must be positive")
        if self.minimum_name_length < 1:
            raise ValueError("minimum_name_length must be positive")
        if self.maximum_name_length < self.minimum_name_length:
            raise ValueError("maximum_name_length must not be below minimum_name_length")


@dataclass(frozen=True, slots=True)
class InvitationCreation:
    """Public invitation metadata plus its one-time bearer presentation."""

    invitation: WorkspaceInvitation
    raw_token: str


class WorkspaceService:
    """Own workspace transaction intent independently from adapters and persistence."""

    def __init__(
        self,
        *,
        repository: WorkspaceRepository,
        invitation_tokens: InvitationTokenManager,
        policy: WorkspacePolicy | None = None,
        clock: Callable[[], datetime] = utc_now,
        uuid_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._repository = repository
        self._invitation_tokens = invitation_tokens
        self._policy = policy or WorkspacePolicy()
        self._clock = clock
        self._uuid_factory = uuid_factory

    async def create_workspace(self, *, actor_user_id: UUID, name: str) -> Workspace:
        """Create a workspace and its first owner membership atomically."""
        normalized_name = self._normalize_name(name)
        now = self._clock()
        workspace = Workspace(
            id=self._uuid_factory(),
            name=normalized_name,
            created_by_user_id=actor_user_id,
            created_at=now,
            updated_at=now,
        )
        owner = WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=actor_user_id,
            role=WorkspaceRole.OWNER,
            joined_at=now,
        )
        try:
            await self._repository.add_workspace(workspace, owner)
            await self._repository.commit()
        except WorkspaceConflict:
            await self._repository.rollback()
            raise
        return workspace

    async def list_workspaces(self, *, actor_user_id: UUID) -> list[Workspace]:
        """List only workspaces in which the actor currently has membership."""
        return await self._repository.list_workspaces(user_id=actor_user_id)

    async def get_workspace(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
    ) -> Workspace:
        """Load a visible workspace, masking both absence and outsider access."""
        workspace, _membership = await self._load_visible_workspace(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            for_update=False,
        )
        return workspace

    async def rename_workspace(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        name: str,
    ) -> Workspace:
        """Rename a workspace after reauthorizing its owner under the workspace lock."""
        normalized_name = self._normalize_name(name)
        workspace, actor = await self._load_visible_workspace(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            for_update=True,
        )
        if actor.role is not WorkspaceRole.OWNER:
            await self._repository.rollback()
            raise WorkspacePermissionDenied
        if normalized_name == workspace.name:
            await self._repository.rollback()
            return workspace
        now = self._clock()
        await self._repository.update_workspace_name(
            workspace_id=workspace_id,
            name=normalized_name,
            updated_at=now,
        )
        await self._repository.commit()
        return replace(workspace, name=normalized_name, updated_at=now)

    async def list_memberships(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
    ) -> list[WorkspaceMembership]:
        """List the roster of a workspace visible to the actor."""
        await self._load_visible_workspace(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            for_update=False,
        )
        return await self._repository.list_memberships(workspace_id=workspace_id)

    async def get_current_membership(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
    ) -> WorkspaceMembership:
        """Return the actor's current membership while masking outsider access."""
        _workspace, membership = await self._load_visible_workspace(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            for_update=False,
        )
        return membership

    async def change_membership_role(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        target_user_id: UUID,
        role: WorkspaceRole,
    ) -> WorkspaceMembership:
        """Let an owner change any role without removing the workspace's final owner."""
        _workspace, actor = await self._load_visible_workspace(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            for_update=True,
        )
        if actor.role is not WorkspaceRole.OWNER:
            await self._repository.rollback()
            raise WorkspacePermissionDenied
        target = await self._locked_target_membership(
            workspace_id=workspace_id,
            target_user_id=target_user_id,
            actor=actor,
        )
        if target.role is role:
            await self._repository.rollback()
            return target
        await self._protect_final_owner(workspace_id=workspace_id, membership=target)
        await self._repository.update_membership_role(
            workspace_id=workspace_id,
            user_id=target_user_id,
            role=role,
        )
        await self._repository.commit()
        return replace(target, role=role)

    async def remove_membership(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        target_user_id: UUID,
    ) -> None:
        """Remove a member according to the owner's or administrator's current role."""
        _workspace, actor = await self._load_visible_workspace(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            for_update=True,
        )
        if actor.role not in {WorkspaceRole.OWNER, WorkspaceRole.ADMIN}:
            await self._repository.rollback()
            raise WorkspacePermissionDenied
        target = await self._locked_target_membership(
            workspace_id=workspace_id,
            target_user_id=target_user_id,
            actor=actor,
        )
        if actor.role is WorkspaceRole.ADMIN and target.role is not WorkspaceRole.MEMBER:
            await self._repository.rollback()
            raise WorkspacePermissionDenied
        await self._protect_final_owner(workspace_id=workspace_id, membership=target)
        await self._repository.remove_membership(
            workspace_id=workspace_id,
            user_id=target_user_id,
        )
        await self._repository.commit()

    async def leave_workspace(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
    ) -> None:
        """Remove the actor's membership while retaining at least one owner."""
        _workspace, actor = await self._load_visible_workspace(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            for_update=True,
        )
        await self._protect_final_owner(workspace_id=workspace_id, membership=actor)
        await self._repository.remove_membership(
            workspace_id=workspace_id,
            user_id=actor_user_id,
        )
        await self._repository.commit()

    async def create_invitation(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
    ) -> InvitationCreation:
        """Create a member-only invitation and return its bearer credential once."""
        await self._require_invitation_manager(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            for_update=True,
        )
        issued = self._invitation_tokens.issue()
        now = self._clock()
        invitation = WorkspaceInvitation(
            id=issued.token_id,
            workspace_id=workspace_id,
            created_by_user_id=actor_user_id,
            token_hash=issued.digest,
            created_at=now,
            expires_at=now + self._policy.invitation_ttl,
            accepted_at=None,
            accepted_by_user_id=None,
            revoked_at=None,
            revoked_by_user_id=None,
        )
        try:
            await self._repository.add_invitation(invitation)
            await self._repository.commit()
        except WorkspaceConflict:
            await self._repository.rollback()
            raise
        return InvitationCreation(invitation=invitation, raw_token=issued.raw)

    async def list_invitations(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
    ) -> list[WorkspaceInvitation]:
        """List invitation metadata for a current owner or administrator."""
        await self._require_invitation_manager(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            for_update=False,
        )
        return await self._repository.list_invitations(workspace_id=workspace_id)

    async def rotate_invitation(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        invitation_id: UUID,
    ) -> InvitationCreation:
        """Revoke an active invitation and atomically create its replacement."""
        await self._require_invitation_manager(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            for_update=True,
        )
        current = await self._repository.get_invitation(
            workspace_id=workspace_id,
            invitation_id=invitation_id,
            for_update=True,
        )
        now = self._clock()
        if current is None:
            await self._repository.rollback()
            raise InvitationNotFound
        if not current.is_active(now):
            await self._repository.rollback()
            raise InvitationNotActive

        issued = self._invitation_tokens.issue()
        replacement = WorkspaceInvitation(
            id=issued.token_id,
            workspace_id=workspace_id,
            created_by_user_id=actor_user_id,
            token_hash=issued.digest,
            created_at=now,
            expires_at=now + self._policy.invitation_ttl,
            accepted_at=None,
            accepted_by_user_id=None,
            revoked_at=None,
            revoked_by_user_id=None,
        )
        try:
            await self._repository.revoke_invitation(
                invitation_id=current.id,
                revoked_at=now,
                revoked_by_user_id=actor_user_id,
            )
            await self._repository.add_invitation(replacement)
            await self._repository.commit()
        except WorkspaceConflict:
            await self._repository.rollback()
            raise
        return InvitationCreation(invitation=replacement, raw_token=issued.raw)

    async def revoke_invitation(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        invitation_id: UUID,
    ) -> None:
        """Revoke one active invitation under the workspace and invitation locks."""
        await self._require_invitation_manager(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            for_update=True,
        )
        invitation = await self._repository.get_invitation(
            workspace_id=workspace_id,
            invitation_id=invitation_id,
            for_update=True,
        )
        now = self._clock()
        if invitation is None:
            await self._repository.rollback()
            raise InvitationNotFound
        if not invitation.is_active(now):
            await self._repository.rollback()
            raise InvitationNotActive
        await self._repository.revoke_invitation(
            invitation_id=invitation_id,
            revoked_at=now,
            revoked_by_user_id=actor_user_id,
        )
        await self._repository.commit()

    async def accept_invitation(
        self,
        *,
        actor_user_id: UUID,
        raw_token: str | None,
    ) -> WorkspaceMembership:
        """Consume one valid bearer invitation and create a member role atomically."""
        presented = self._invitation_tokens.present(raw_token)
        workspace_id = await self._repository.get_invitation_workspace_id(presented.token_id)
        if workspace_id is None:
            await self._repository.rollback()
            raise InvalidInvitationToken
        workspace = await self._repository.get_workspace(workspace_id, for_update=True)
        if workspace is None:
            await self._repository.rollback()
            raise InvalidInvitationToken
        invitation = await self._repository.get_invitation(
            workspace_id=workspace.id,
            invitation_id=presented.token_id,
            for_update=True,
        )
        now = self._clock()
        if (
            invitation is None
            or not invitation.is_active(now)
            or not self._invitation_tokens.matches(
                presented.digest,
                invitation.token_hash,
            )
        ):
            await self._repository.rollback()
            raise InvalidInvitationToken
        existing = await self._repository.get_membership(
            workspace_id=workspace.id,
            user_id=actor_user_id,
            for_update=True,
        )
        if existing is not None:
            await self._repository.rollback()
            raise MembershipAlreadyExists
        membership = WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=actor_user_id,
            role=WorkspaceRole.MEMBER,
            joined_at=now,
        )
        try:
            await self._repository.accept_invitation(
                invitation_id=invitation.id,
                membership=membership,
                accepted_at=now,
            )
            await self._repository.commit()
        except WorkspaceConflict:
            await self._repository.rollback()
            raise MembershipAlreadyExists from None
        return membership

    async def _load_visible_workspace(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        for_update: bool,
    ) -> tuple[Workspace, WorkspaceMembership]:
        workspace = await self._repository.get_workspace(
            workspace_id,
            for_update=for_update,
        )
        if workspace is None:
            if for_update:
                await self._repository.rollback()
            raise WorkspaceNotFound
        membership = await self._repository.get_membership(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            for_update=for_update,
        )
        if membership is None:
            if for_update:
                await self._repository.rollback()
            raise WorkspaceNotFound
        return workspace, membership

    async def _require_invitation_manager(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        for_update: bool,
    ) -> tuple[Workspace, WorkspaceMembership]:
        workspace, membership = await self._load_visible_workspace(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            for_update=for_update,
        )
        if membership.role not in {WorkspaceRole.OWNER, WorkspaceRole.ADMIN}:
            if for_update:
                await self._repository.rollback()
            raise WorkspacePermissionDenied
        return workspace, membership

    async def _locked_target_membership(
        self,
        *,
        workspace_id: UUID,
        target_user_id: UUID,
        actor: WorkspaceMembership,
    ) -> WorkspaceMembership:
        if actor.user_id == target_user_id:
            return actor
        target = await self._repository.get_membership(
            workspace_id=workspace_id,
            user_id=target_user_id,
            for_update=True,
        )
        if target is None:
            await self._repository.rollback()
            raise MembershipNotFound
        return target

    async def _protect_final_owner(
        self,
        *,
        workspace_id: UUID,
        membership: WorkspaceMembership,
    ) -> None:
        if membership.role is not WorkspaceRole.OWNER:
            return
        owner_count = await self._repository.count_memberships_by_role(
            workspace_id=workspace_id,
            role=WorkspaceRole.OWNER,
        )
        if owner_count <= 1:
            await self._repository.rollback()
            raise OwnerInvariantViolation

    def _normalize_name(self, name: str) -> str:
        normalized = name.strip()
        if (
            not self._policy.minimum_name_length
            <= len(normalized)
            <= (self._policy.maximum_name_length)
        ):
            raise InvalidWorkspaceName
        return normalized

"""Public request and response contracts for workspace endpoints."""

from datetime import datetime
from typing import Annotated, Self
from urllib.parse import quote
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from klack.modules.workspaces.application.service import InvitationCreation
from klack.modules.workspaces.domain.entities import (
    Workspace,
    WorkspaceInvitation,
    WorkspaceMembership,
    WorkspaceRole,
)


class StrictRequest(BaseModel):
    """Reject unexpected fields on workspace mutation requests."""

    model_config = ConfigDict(extra="forbid")


class CreateWorkspaceRequest(StrictRequest):
    """Create a workspace owned by the current identity."""

    name: Annotated[str, Field(min_length=1, max_length=100)]

    @field_validator("name", mode="before")
    @classmethod
    def trim_name_before_length_validation(cls, value: object) -> object:
        """Apply the public trim-then-bound contract before Pydantic checks length."""
        return value.strip() if isinstance(value, str) else value


class RenameWorkspaceRequest(StrictRequest):
    """Replace a workspace's display name."""

    name: Annotated[str, Field(min_length=1, max_length=100)]

    @field_validator("name", mode="before")
    @classmethod
    def trim_name_before_length_validation(cls, value: object) -> object:
        """Apply the public trim-then-bound contract before Pydantic checks length."""
        return value.strip() if isinstance(value, str) else value


class ChangeMembershipRoleRequest(StrictRequest):
    """Replace one member's workspace role."""

    role: WorkspaceRole


class AcceptInvitationRequest(StrictRequest):
    """Present a manually shared, single-use invitation credential."""

    token: Annotated[SecretStr, Field(min_length=1, max_length=256)]


class WorkspaceResponse(BaseModel):
    """Public workspace metadata."""

    id: UUID
    name: str
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, workspace: Workspace) -> Self:
        return cls(
            id=workspace.id,
            name=workspace.name,
            created_by_user_id=workspace.created_by_user_id,
            created_at=workspace.created_at,
            updated_at=workspace.updated_at,
        )


class WorkspacesResponse(BaseModel):
    """Workspaces visible through the current identity's memberships."""

    workspaces: list[WorkspaceResponse]


class MembershipResponse(BaseModel):
    """One user's current role in a workspace."""

    workspace_id: UUID
    user_id: UUID
    role: WorkspaceRole
    joined_at: datetime

    @classmethod
    def from_domain(cls, membership: WorkspaceMembership) -> Self:
        return cls(
            workspace_id=membership.workspace_id,
            user_id=membership.user_id,
            role=membership.role,
            joined_at=membership.joined_at,
        )


class MembershipsResponse(BaseModel):
    """Current membership roster for a visible workspace."""

    memberships: list[MembershipResponse]


class InvitationResponse(BaseModel):
    """Invitation lifecycle metadata without its bearer credential."""

    id: UUID
    workspace_id: UUID
    created_by_user_id: UUID
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None
    accepted_by_user_id: UUID | None
    revoked_at: datetime | None
    revoked_by_user_id: UUID | None

    @classmethod
    def from_domain(cls, invitation: WorkspaceInvitation) -> Self:
        return cls(
            id=invitation.id,
            workspace_id=invitation.workspace_id,
            created_by_user_id=invitation.created_by_user_id,
            created_at=invitation.created_at,
            expires_at=invitation.expires_at,
            accepted_at=invitation.accepted_at,
            accepted_by_user_id=invitation.accepted_by_user_id,
            revoked_at=invitation.revoked_at,
            revoked_by_user_id=invitation.revoked_by_user_id,
        )


class InvitationsResponse(BaseModel):
    """Invitation metadata visible to workspace managers."""

    invitations: list[InvitationResponse]


class InvitationCreationResponse(BaseModel):
    """Invitation metadata plus its one-time manually shareable URL."""

    invitation: InvitationResponse
    invite_url: str

    @classmethod
    def from_result(cls, result: InvitationCreation, *, public_web_origin: str) -> Self:
        encoded_token = quote(result.raw_token, safe="")
        return cls(
            invitation=InvitationResponse.from_domain(result.invitation),
            invite_url=f"{public_web_origin.rstrip('/')}/join#token={encoded_token}",
        )

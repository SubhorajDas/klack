"""Versioned workspace, membership, and invitation endpoints."""

from uuid import UUID

from fastapi import APIRouter, Request, Response, status

from klack.modules.identity.api.dependencies import (
    CurrentIdentityDependency,
    CurrentMutationIdentityDependency,
)
from klack.modules.workspaces.api.dependencies import WorkspaceServiceDependency
from klack.modules.workspaces.api.schemas import (
    AcceptInvitationRequest,
    ChangeMembershipRoleRequest,
    CreateWorkspaceRequest,
    InvitationCreationResponse,
    InvitationResponse,
    InvitationsResponse,
    MembershipResponse,
    MembershipsResponse,
    RenameWorkspaceRequest,
    WorkspaceResponse,
    WorkspacesResponse,
)

router = APIRouter(tags=["workspaces"])


@router.post(
    "/workspaces",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace(
    payload: CreateWorkspaceRequest,
    response: Response,
    service: WorkspaceServiceDependency,
    identity: CurrentMutationIdentityDependency,
) -> WorkspaceResponse:
    """Create a workspace with the current identity as its first owner."""
    workspace = await service.create_workspace(
        actor_user_id=identity.user.id,
        name=payload.name,
    )
    response.headers["Cache-Control"] = "no-store"
    return WorkspaceResponse.from_domain(workspace)


@router.get("/workspaces", response_model=WorkspacesResponse)
async def list_workspaces(
    response: Response,
    service: WorkspaceServiceDependency,
    identity: CurrentIdentityDependency,
) -> WorkspacesResponse:
    """List workspaces in which the current identity is a member."""
    workspaces = await service.list_workspaces(actor_user_id=identity.user.id)
    response.headers["Cache-Control"] = "no-store"
    return WorkspacesResponse(
        workspaces=[WorkspaceResponse.from_domain(workspace) for workspace in workspaces],
    )


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: UUID,
    response: Response,
    service: WorkspaceServiceDependency,
    identity: CurrentIdentityDependency,
) -> WorkspaceResponse:
    """Return one workspace visible to the current identity."""
    workspace = await service.get_workspace(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
    )
    response.headers["Cache-Control"] = "no-store"
    return WorkspaceResponse.from_domain(workspace)


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
async def rename_workspace(
    workspace_id: UUID,
    payload: RenameWorkspaceRequest,
    response: Response,
    service: WorkspaceServiceDependency,
    identity: CurrentMutationIdentityDependency,
) -> WorkspaceResponse:
    """Rename a workspace as one of its current owners."""
    workspace = await service.rename_workspace(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        name=payload.name,
    )
    response.headers["Cache-Control"] = "no-store"
    return WorkspaceResponse.from_domain(workspace)


@router.get(
    "/workspaces/{workspace_id}/memberships",
    response_model=MembershipsResponse,
)
async def list_memberships(
    workspace_id: UUID,
    response: Response,
    service: WorkspaceServiceDependency,
    identity: CurrentIdentityDependency,
) -> MembershipsResponse:
    """List the membership roster of a visible workspace."""
    memberships = await service.list_memberships(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
    )
    response.headers["Cache-Control"] = "no-store"
    return MembershipsResponse(
        memberships=[MembershipResponse.from_domain(item) for item in memberships],
    )


@router.get(
    "/workspaces/{workspace_id}/memberships/me",
    response_model=MembershipResponse,
)
async def current_membership(
    workspace_id: UUID,
    response: Response,
    service: WorkspaceServiceDependency,
    identity: CurrentIdentityDependency,
) -> MembershipResponse:
    """Return the current identity's membership in one workspace."""
    membership = await service.get_current_membership(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
    )
    response.headers["Cache-Control"] = "no-store"
    return MembershipResponse.from_domain(membership)


@router.patch(
    "/workspaces/{workspace_id}/memberships/{user_id}",
    response_model=MembershipResponse,
)
async def change_membership_role(
    workspace_id: UUID,
    user_id: UUID,
    payload: ChangeMembershipRoleRequest,
    response: Response,
    service: WorkspaceServiceDependency,
    identity: CurrentMutationIdentityDependency,
) -> MembershipResponse:
    """Change a membership role while preserving at least one owner."""
    membership = await service.change_membership_role(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        target_user_id=user_id,
        role=payload.role,
    )
    response.headers["Cache-Control"] = "no-store"
    return MembershipResponse.from_domain(membership)


@router.delete(
    "/workspaces/{workspace_id}/memberships/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_membership(
    workspace_id: UUID,
    user_id: UUID,
    service: WorkspaceServiceDependency,
    identity: CurrentMutationIdentityDependency,
) -> Response:
    """Remove a member according to the actor's current role."""
    await service.remove_membership(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        target_user_id=user_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})


@router.post(
    "/workspaces/{workspace_id}/leave",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def leave_workspace(
    workspace_id: UUID,
    service: WorkspaceServiceDependency,
    identity: CurrentMutationIdentityDependency,
) -> Response:
    """Remove the current identity's membership when ownership remains valid."""
    await service.leave_workspace(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})


@router.post(
    "/workspaces/{workspace_id}/invitations",
    response_model=InvitationCreationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_invitation(
    workspace_id: UUID,
    request: Request,
    response: Response,
    service: WorkspaceServiceDependency,
    identity: CurrentMutationIdentityDependency,
) -> InvitationCreationResponse:
    """Create a single-use member invitation and reveal its link once."""
    result = await service.create_invitation(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
    )
    response.headers["Cache-Control"] = "no-store"
    return InvitationCreationResponse.from_result(
        result,
        public_web_origin=request.app.state.container.settings.auth_public_web_origin,
    )


@router.get(
    "/workspaces/{workspace_id}/invitations",
    response_model=InvitationsResponse,
)
async def list_invitations(
    workspace_id: UUID,
    response: Response,
    service: WorkspaceServiceDependency,
    identity: CurrentIdentityDependency,
) -> InvitationsResponse:
    """List invitation metadata without revealing bearer credentials."""
    invitations = await service.list_invitations(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
    )
    response.headers["Cache-Control"] = "no-store"
    return InvitationsResponse(
        invitations=[InvitationResponse.from_domain(item) for item in invitations],
    )


@router.post(
    "/workspaces/{workspace_id}/invitations/{invitation_id}/rotate",
    response_model=InvitationCreationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def rotate_invitation(
    workspace_id: UUID,
    invitation_id: UUID,
    request: Request,
    response: Response,
    service: WorkspaceServiceDependency,
    identity: CurrentMutationIdentityDependency,
) -> InvitationCreationResponse:
    """Revoke one active invitation and reveal its replacement link once."""
    result = await service.rotate_invitation(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        invitation_id=invitation_id,
    )
    response.headers["Cache-Control"] = "no-store"
    return InvitationCreationResponse.from_result(
        result,
        public_web_origin=request.app.state.container.settings.auth_public_web_origin,
    )


@router.delete(
    "/workspaces/{workspace_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_invitation(
    workspace_id: UUID,
    invitation_id: UUID,
    service: WorkspaceServiceDependency,
    identity: CurrentMutationIdentityDependency,
) -> Response:
    """Revoke one active invitation."""
    await service.revoke_invitation(
        actor_user_id=identity.user.id,
        workspace_id=workspace_id,
        invitation_id=invitation_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})


@router.post(
    "/workspace-invitations/accept",
    response_model=MembershipResponse,
    status_code=status.HTTP_201_CREATED,
)
async def accept_invitation(
    payload: AcceptInvitationRequest,
    response: Response,
    service: WorkspaceServiceDependency,
    identity: CurrentMutationIdentityDependency,
) -> MembershipResponse:
    """Consume a manually shared invitation as the current identity."""
    membership = await service.accept_invitation(
        actor_user_id=identity.user.id,
        raw_token=payload.token.get_secret_value(),
    )
    response.headers["Cache-Control"] = "no-store"
    return MembershipResponse.from_domain(membership)

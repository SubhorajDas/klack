"""Problem Details mappings for expected workspace failures."""

from dataclasses import dataclass

from fastapi import Request
from starlette.responses import JSONResponse

from klack.core.problems import problem_response
from klack.modules.workspaces.domain.errors import (
    InvalidInvitationToken,
    InvalidWorkspaceName,
    InvitationNotActive,
    InvitationNotFound,
    MembershipAlreadyExists,
    MembershipNotFound,
    OwnerInvariantViolation,
    WorkspaceError,
    WorkspaceNotFound,
    WorkspacePermissionDenied,
)


@dataclass(frozen=True, slots=True)
class ErrorContract:
    """Stable public representation for one expected failure type."""

    status: int
    code: str
    detail: str


ERROR_CONTRACTS: dict[type[WorkspaceError], ErrorContract] = {
    InvalidWorkspaceName: ErrorContract(422, "validation_error", "The workspace name is invalid."),
    WorkspaceNotFound: ErrorContract(404, "workspace_not_found", "The workspace was not found."),
    WorkspacePermissionDenied: ErrorContract(
        403,
        "workspace_permission_denied",
        "The current membership cannot perform this operation.",
    ),
    MembershipNotFound: ErrorContract(404, "membership_not_found", "The membership was not found."),
    MembershipAlreadyExists: ErrorContract(
        409,
        "membership_already_exists",
        "The user already belongs to this workspace.",
    ),
    OwnerInvariantViolation: ErrorContract(
        409,
        "owner_invariant_violation",
        "The workspace must retain at least one owner.",
    ),
    InvitationNotFound: ErrorContract(404, "invitation_not_found", "The invitation was not found."),
    InvitationNotActive: ErrorContract(
        409,
        "invitation_not_active",
        "The invitation is no longer active.",
    ),
    InvalidInvitationToken: ErrorContract(
        400,
        "invalid_invitation_token",
        "The invitation is invalid or expired.",
    ),
}


async def workspace_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return the stable public contract for one expected workspace failure."""
    if not isinstance(exc, WorkspaceError):
        raise exc
    contract = ERROR_CONTRACTS.get(type(exc))
    if contract is None:
        contract = ErrorContract(400, "workspace_error", "The workspace operation failed.")
    return problem_response(
        request,
        status_code=contract.status,
        code=contract.code,
        detail=contract.detail,
    )

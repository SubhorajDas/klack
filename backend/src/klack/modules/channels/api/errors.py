"""Problem Details mappings for expected channel failures."""

from dataclasses import dataclass

from fastapi import Request
from starlette.responses import JSONResponse

from klack.core.problems import problem_response
from klack.modules.channels.domain.errors import (
    ChannelArchived,
    ChannelError,
    ChannelMembershipNotFound,
    ChannelNameConflict,
    ChannelNotFound,
    ChannelPermissionDenied,
    InvalidChannelName,
    TargetWorkspaceMembershipNotFound,
)


@dataclass(frozen=True, slots=True)
class ErrorContract:
    """Stable public representation for one expected failure type."""

    status: int
    code: str
    detail: str


ERROR_CONTRACTS: dict[type[ChannelError], ErrorContract] = {
    InvalidChannelName: ErrorContract(422, "validation_error", "The channel name is invalid."),
    ChannelNotFound: ErrorContract(404, "channel_not_found", "The channel was not found."),
    ChannelPermissionDenied: ErrorContract(
        403,
        "channel_permission_denied",
        "The current membership cannot perform this operation.",
    ),
    ChannelNameConflict: ErrorContract(
        409,
        "channel_name_conflict",
        "A channel with this name already exists in the workspace.",
    ),
    ChannelMembershipNotFound: ErrorContract(
        404,
        "channel_membership_not_found",
        "The channel membership was not found.",
    ),
    TargetWorkspaceMembershipNotFound: ErrorContract(
        404,
        "target_workspace_membership_not_found",
        "The target user is not a member of the workspace.",
    ),
    ChannelArchived: ErrorContract(
        409,
        "channel_archived",
        "The channel is archived.",
    ),
}


async def channel_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return the stable public contract for one expected channel failure."""
    if not isinstance(exc, ChannelError):
        raise exc
    contract = ERROR_CONTRACTS.get(type(exc))
    if contract is None:
        contract = ErrorContract(400, "channel_error", "The channel operation failed.")
    return problem_response(
        request,
        status_code=contract.status,
        code=contract.code,
        detail=contract.detail,
    )

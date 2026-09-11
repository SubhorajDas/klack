"""Problem Details mappings for expected messaging failures."""

from dataclasses import dataclass

from fastapi import Request
from starlette.responses import JSONResponse

from klack.core.problems import problem_response
from klack.modules.messaging.domain.errors import (
    InvalidMessageBody,
    InvalidMessageCursor,
    MessageDeleted,
    MessageNotFound,
    MessagePermissionDenied,
    MessagingError,
)


@dataclass(frozen=True, slots=True)
class ErrorContract:
    """Stable public representation for one expected failure type."""

    status: int
    code: str
    detail: str


ERROR_CONTRACTS: dict[type[MessagingError], ErrorContract] = {
    InvalidMessageBody: ErrorContract(422, "validation_error", "The message body is invalid."),
    InvalidMessageCursor: ErrorContract(
        422,
        "invalid_message_cursor",
        "The message history cursor is invalid.",
    ),
    MessageNotFound: ErrorContract(404, "message_not_found", "The message was not found."),
    MessagePermissionDenied: ErrorContract(
        403,
        "message_permission_denied",
        "Only the message author can perform this operation.",
    ),
    MessageDeleted: ErrorContract(409, "message_deleted", "The message has been deleted."),
}


async def messaging_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return the stable public contract for one expected messaging failure."""
    if not isinstance(exc, MessagingError):
        raise exc
    contract = ERROR_CONTRACTS.get(type(exc))
    if contract is None:
        contract = ErrorContract(400, "messaging_error", "The messaging operation failed.")
    return problem_response(
        request,
        status_code=contract.status,
        code=contract.code,
        detail=contract.detail,
    )

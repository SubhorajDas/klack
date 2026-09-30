"""Attachment values and the messaging integration boundary."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Attachment:
    id: UUID
    filename: str
    size: int
    content_type: str


class AttachmentGateway(Protocol):
    async def prepare(
        self, ids: tuple[UUID, ...], actor: UUID, workspace: UUID, channel: UUID
    ) -> tuple[Attachment, ...]: ...

    async def attach(self, ids: tuple[UUID, ...], message_id: UUID) -> None: ...

    async def remove(self, message_id: UUID) -> None: ...


class FileError(Exception):
    def __init__(self, detail: str, status: int = 422) -> None:
        super().__init__(detail)
        self.status = status

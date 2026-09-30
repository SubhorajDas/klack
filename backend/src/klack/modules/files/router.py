"""Authenticated, bounded uploads and private downloads."""

import asyncio
from dataclasses import asdict
from datetime import timedelta
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from klack.api.dependencies import get_session
from klack.core.problems import problem_response
from klack.modules.files.domain import FileError
from klack.modules.files.models import FileRecord
from klack.modules.files.service import FileService, expired, now
from klack.modules.files.storage import FileStorage, inspect_content
from klack.modules.identity.api.dependencies import (
    CurrentIdentityDependency,
    CurrentMutationIdentityDependency,
)
from klack.modules.messaging.infrastructure.models import MessageRecord


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(tags=["files"], dependencies=[Depends(no_store)])
PREFIX = "/workspaces/{workspace_id}/channels/{channel_id}/files"
Session = Annotated[AsyncSession, Depends(get_session)]


class FileResponse(Response):
    """Keep a transfer slot until bytes have been sent, including client disconnects."""

    def __init__(
        self, content: bytes, media_type: str, headers: dict[str, str], gate: asyncio.Semaphore
    ) -> None:
        super().__init__(content, media_type=media_type, headers=headers)
        self.gate = gate

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.gate.release()


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)


async def file_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, FileError):
        raise exc
    return problem_response(request, status_code=exc.status, code="file_error", detail=str(exc))


@router.get(PREFIX + "/limits")
async def limits(request: Request, identity: CurrentIdentityDependency) -> dict[str, object]:
    settings = request.app.state.container.settings
    return {
        "enabled": settings.files_enabled,
        "max_bytes": settings.files_max_bytes,
        "max_attachments": settings.files_max_attachments,
    }


@router.post(PREFIX, status_code=201)
async def reserve(
    workspace_id: UUID,
    channel_id: UUID,
    payload: UploadRequest,
    request: Request,
    session: Session,
    identity: CurrentMutationIdentityDependency,
) -> dict[str, object]:
    row = await FileService(session, request.app.state.container.settings).reserve(
        identity.user.id, workspace_id, channel_id, payload.filename, payload.size
    )
    return {**asdict(row.attachment()), "status": row.status}


@router.put(PREFIX + "/{file_id}/content")
async def upload(
    workspace_id: UUID,
    channel_id: UUID,
    file_id: UUID,
    request: Request,
    session: Session,
    identity: CurrentMutationIdentityDependency,
) -> dict[str, object]:
    settings = request.app.state.container.settings
    if not settings.files_enabled:
        raise FileError("File sharing is disabled.", 503)
    service = FileService(session, settings)
    await service.authorize(identity.user.id, workspace_id, channel_id, True)
    row = await service.owned(file_id, identity.user.id, workspace_id, channel_id)
    if row.status == "ready" and not expired(row.expires_at):
        result = asdict(row.attachment())
        await session.rollback()
        return result
    if row.status != "pending" or expired(row.expires_at):
        raise FileError("This upload is unavailable. Please select the file again.", 409)
    expected_size, key = row.size, row.storage_key
    if request.headers.get("content-length") not in (None, str(expected_size)):
        raise FileError("The file size does not match the upload reservation.", 413)
    row.status = "uploading"
    row.expires_at = now() + timedelta(hours=1)
    await session.commit()
    try:
        gate = request.app.state.files_io_gate
        try:
            async with asyncio.timeout(2):
                await gate.acquire()
        except TimeoutError as exc:
            raise FileError("Uploads are busy. Please retry shortly.", 429) from exc
        try:
            data = bytearray()
            async with asyncio.timeout(120):
                async for chunk in request.stream():
                    if len(data) + len(chunk) > min(expected_size, settings.files_max_bytes):
                        raise FileError("The file exceeds its upload size limit.", 413)
                    data.extend(chunk)
            if len(data) != expected_size:
                raise FileError("The upload was incomplete. Please retry.")
            content = bytes(data)
            del data
            content_type = await run_in_threadpool(inspect_content, content, settings)
            await run_in_threadpool(FileStorage(settings).put, key, content)
        finally:
            gate.release()
        # Uploading never holds conversation locks during network I/O. Reauthorize now.
        await service.authorize(identity.user.id, workspace_id, channel_id, True)
        row = await service.owned(file_id, identity.user.id, workspace_id, channel_id)
        if row.status != "uploading" or expired(row.expires_at):
            raise FileError("The upload expired or was cancelled.", 409)
        row.status = "ready"
        row.content_type = content_type
        row.expires_at = now() + timedelta(hours=24)
        result = asdict(row.attachment())
        await session.commit()
        return result
    except BaseException:
        await session.rollback()
        # A durable cleanup record also covers a successful storage write with a lost response.
        await session.execute(
            update(FileRecord)
            .where(FileRecord.id == file_id, FileRecord.status == "uploading")
            .values(status="deleting", expires_at=now())
        )
        await session.commit()
        raise


@router.delete(PREFIX + "/{file_id}", status_code=204)
async def cancel(
    workspace_id: UUID,
    channel_id: UUID,
    file_id: UUID,
    request: Request,
    session: Session,
    identity: CurrentMutationIdentityDependency,
) -> Response:
    service = FileService(session, request.app.state.container.settings)
    await service.authorize(identity.user.id, workspace_id, channel_id, False)
    row = await service.owned(file_id, identity.user.id, workspace_id, channel_id)
    if row.message_id:
        raise FileError("Delete the message to remove a shared file.", 409)
    # In-flight uploads retain their lease so cleanup cannot race a storage write.
    if row.status == "uploading":
        raise FileError("The upload is still finishing. It will expire automatically.", 409)
    if row.status != "removed":
        row.status = "deleting"
        row.expires_at = now()
    await session.commit()
    return Response(status_code=204)


@router.get(PREFIX)
async def list_files(
    workspace_id: UUID,
    channel_id: UUID,
    request: Request,
    session: Session,
    identity: CurrentIdentityDependency,
    before: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict[str, object]:
    await FileService(session, request.app.state.container.settings).authorize(
        identity.user.id, workspace_id, channel_id, False
    )
    statement = (
        select(FileRecord)
        .join(MessageRecord, FileRecord.message_id == MessageRecord.id)
        .where(
            FileRecord.workspace_id == workspace_id,
            FileRecord.channel_id == channel_id,
            FileRecord.status == "attached",
            MessageRecord.deleted_at.is_(None),
        )
    )
    if before:
        cursor = await session.get(FileRecord, before)
        if cursor is None or cursor.channel_id != channel_id:
            raise FileError("Invalid file history cursor.")
        statement = statement.where(
            or_(
                FileRecord.created_at < cursor.created_at,
                and_(FileRecord.created_at == cursor.created_at, FileRecord.id < cursor.id),
            )
        )
    rows = list(
        (
            await session.scalars(
                statement.order_by(FileRecord.created_at.desc(), FileRecord.id.desc()).limit(
                    limit + 1
                )
            )
        ).all()
    )
    return {
        "files": [
            {**asdict(row.attachment()), "message_id": row.message_id, "created_at": row.created_at}
            for row in rows[:limit]
        ],
        "next_before": rows[limit - 1].id if len(rows) > limit else None,
    }


@router.get(PREFIX + "/{file_id}/content")
async def download(
    workspace_id: UUID,
    channel_id: UUID,
    file_id: UUID,
    request: Request,
    session: Session,
    identity: CurrentIdentityDependency,
    preview: bool = False,
) -> Response:
    settings = request.app.state.container.settings
    service = FileService(session, settings)
    row = await service.readable(file_id, identity.user.id, workspace_id, channel_id)
    key, size = row.storage_key, row.size
    filename, content_type = row.filename, row.content_type
    await session.rollback()
    gate = request.app.state.files_io_gate
    try:
        async with asyncio.timeout(2):
            await gate.acquire()
    except TimeoutError as exc:
        raise FileError("Downloads are busy. Please retry shortly.", 429) from exc
    try:
        content = await run_in_threadpool(FileStorage(settings).get, key)
        if len(content) != size:
            raise FileError("The stored file is unavailable. Please retry later.", 503)
        # Recheck after storage I/O, including message deletion and revoked membership.
        await service.readable(file_id, identity.user.id, workspace_id, channel_id)
        await session.rollback()
        inline = preview and content_type in {"image/png", "image/jpeg", "image/gif", "image/webp"}
        disposition = "inline" if inline else "attachment"
        return FileResponse(
            content,
            gate=gate,
            media_type=content_type if inline else "application/octet-stream",
            headers={
                "Content-Disposition": (
                    f"{disposition}; filename*=UTF-8''{quote(filename, safe='')}"
                ),
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )
    except BaseException:
        gate.release()
        raise

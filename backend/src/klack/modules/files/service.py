"""Reservations and attachment links use the same authorization locks as messages."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from klack.core.config import Settings
from klack.modules.channels.application.service import ChannelContentAccessService
from klack.modules.channels.domain.errors import ChannelArchived
from klack.modules.channels.infrastructure.repository import SqlAlchemyChannelRepository
from klack.modules.files.domain import Attachment, FileError
from klack.modules.files.models import FileRecord
from klack.modules.messaging.infrastructure.models import MessageRecord
from klack.modules.workspaces.application.service import WorkspaceAccessService
from klack.modules.workspaces.infrastructure.repository import SqlAlchemyWorkspaceRepository


def now() -> datetime:
    return datetime.now(UTC)


def expired(value: datetime) -> bool:
    return value.replace(tzinfo=UTC) <= now()


class FileService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.access = ChannelContentAccessService(
            repository=SqlAlchemyChannelRepository(session),
            workspace_access=WorkspaceAccessService(SqlAlchemyWorkspaceRepository(session)),
        )

    async def authorize(self, actor: UUID, workspace: UUID, channel: UUID, write: bool) -> None:
        channel_value = await self.access.require_access(
            actor_user_id=actor, workspace_id=workspace, channel_id=channel, for_update=write
        )
        if write and channel_value.is_archived:
            raise ChannelArchived

    async def reserve(
        self, actor: UUID, workspace: UUID, channel: UUID, filename: str, size: int
    ) -> FileRecord:
        if not self.settings.files_enabled:
            raise FileError("File sharing is disabled.", 503)
        if not 0 < size <= self.settings.files_max_bytes:
            raise FileError("The file exceeds the upload size limit or is empty.", 413)
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if (
            not filename
            or len(filename) > 255
            or any(ord(c) < 32 or ord(c) == 127 for c in filename)
        ):
            raise FileError("Choose a file with a valid filename.")
        await self.authorize(actor, workspace, channel, True)
        used = await self.session.scalar(
            select(func.coalesce(func.sum(FileRecord.size), 0)).where(
                FileRecord.workspace_id == workspace, FileRecord.status != "removed"
            )
        )
        if int(used or 0) + size > self.settings.files_workspace_quota_bytes:
            raise FileError("This workspace has reached its file storage limit.", 413)
        count = await self.session.scalar(
            select(func.count())
            .select_from(FileRecord)
            .where(
                FileRecord.workspace_id == workspace,
                FileRecord.uploader_id == actor,
                FileRecord.created_at > now() - timedelta(hours=1),
            )
        )
        if int(count or 0) >= self.settings.files_uploads_per_hour:
            raise FileError("Too many uploads. Please try again later.", 429)
        file_id = uuid4()
        record = FileRecord(
            id=file_id,
            workspace_id=workspace,
            channel_id=channel,
            uploader_id=actor,
            filename=filename,
            size=size,
            storage_key=f"{workspace.hex}/{file_id.hex}",
            content_type="application/octet-stream",
            status="pending",
            created_at=now(),
            expires_at=now() + timedelta(hours=24),
            position=0,
        )
        self.session.add(record)
        await self.session.commit()
        return record

    async def owned(self, file_id: UUID, actor: UUID, workspace: UUID, channel: UUID) -> FileRecord:
        record = await self.session.scalar(
            select(FileRecord)
            .where(
                FileRecord.id == file_id,
                FileRecord.workspace_id == workspace,
                FileRecord.channel_id == channel,
                FileRecord.uploader_id == actor,
            )
            .with_for_update()
        )
        if record is None:
            raise FileError("The upload was not found.", 404)
        return record

    async def prepare(
        self, ids: tuple[UUID, ...], actor: UUID, workspace: UUID, channel: UUID
    ) -> tuple[Attachment, ...]:
        if not self.settings.files_enabled:
            raise FileError("File sharing is disabled.", 503)
        if len(ids) > self.settings.files_max_attachments or len(set(ids)) != len(ids):
            raise FileError("Too many or duplicate attachments.")
        records = []
        # All callers already hold the workspace/channel lock; deterministic row order.
        for file_id in sorted(ids):
            row = await self.owned(file_id, actor, workspace, channel)
            if row.status != "ready" or row.message_id or expired(row.expires_at):
                raise FileError("An attachment is unavailable. Remove it and upload it again.", 409)
            records.append(row)
        values = {row.id: row.attachment() for row in records}
        return tuple(values[file_id] for file_id in ids)

    async def attach(self, ids: tuple[UUID, ...], message_id: UUID) -> None:
        for position, file_id in enumerate(ids):
            await self.session.execute(
                update(FileRecord)
                .where(FileRecord.id == file_id)
                .values(status="attached", message_id=message_id, position=position)
            )

    async def remove(self, message_id: UUID) -> None:
        await self.session.execute(
            update(FileRecord)
            .where(FileRecord.message_id == message_id, FileRecord.status == "attached")
            .values(
                status="deleting",
                expires_at=now(),
                filename="",
                content_type="application/octet-stream",
            )
        )

    async def readable(
        self, file_id: UUID, actor: UUID, workspace: UUID, channel: UUID
    ) -> FileRecord:
        await self.authorize(actor, workspace, channel, False)
        record = await self.session.scalar(
            select(FileRecord)
            .join(MessageRecord, FileRecord.message_id == MessageRecord.id)
            .where(
                FileRecord.id == file_id,
                FileRecord.workspace_id == workspace,
                FileRecord.channel_id == channel,
                FileRecord.status == "attached",
                MessageRecord.deleted_at.is_(None),
            )
        )
        if record is None:
            raise FileError("The file was not found.", 404)
        return record

"""Exercise the real file/message services and database through authenticated HTTP."""

import asyncio
import io
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import delete, event, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from klack.api.dependencies import get_session
from klack.core.config import Settings
from klack.core.db.metadata import target_metadata
from klack.modules.channels.infrastructure.models import ChannelMembershipRecord, ChannelRecord
from klack.modules.files.domain import FileError
from klack.modules.files.models import FileRecord
from klack.modules.files.service import FileService
from klack.modules.files.storage import FileStorage
from klack.modules.files.worker import cleanup
from klack.modules.identity.api.dependencies import get_identity_service
from klack.modules.identity.domain.errors import AuthenticationRequired, CsrfValidationFailed
from klack.modules.identity.infrastructure.models import UserRecord
from klack.modules.workspaces.infrastructure.models import MembershipRecord, WorkspaceRecord

ACTOR, OTHER, WORKSPACE, CHANNEL, SECOND = [UUID(int=i) for i in range(1, 6)]
BASE = f"/api/v1/workspaces/{WORKSPACE}/channels/{CHANNEL}"
HEADERS = {"Origin": "http://test", "X-CSRF-Token": "files-csrf"}


@dataclass
class Harness:
    client: AsyncClient
    factory: async_sessionmaker[AsyncSession]
    settings: Settings

    async def upload(self, data: bytes = b"hello", filename: str = "notes.txt") -> dict:
        reserved = await self.client.post(
            BASE + "/files", json={"filename": filename, "size": len(data)}, headers=HEADERS
        )
        assert reserved.status_code == 201, reserved.text
        uploaded = await self.client.put(
            BASE + f"/files/{reserved.json()['id']}/content", content=data, headers=HEADERS
        )
        assert uploaded.status_code == 200, uploaded.text
        return uploaded.json()


@pytest.fixture
async def files(app: FastAPI, client: AsyncClient, settings: Settings, tmp_path: Path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def configure(connection, _):
        connection.create_function("char_length", 1, len)
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(target_metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    instant = datetime.now(UTC)
    async with factory() as session:
        for user_id in (ACTOR, OTHER):
            session.add(UserRecord(id=user_id, email=f"{user_id}@test.example", created_at=instant))
        await session.flush()
        session.add(
            WorkspaceRecord(
                id=WORKSPACE,
                name="Files",
                created_by_user_id=ACTOR,
                created_at=instant,
                updated_at=instant,
            )
        )
        await session.flush()
        for user_id in (ACTOR, OTHER):
            session.add(
                MembershipRecord(
                    workspace_id=WORKSPACE,
                    user_id=user_id,
                    role="owner" if user_id == ACTOR else "member",
                    joined_at=instant,
                )
            )
        for channel_id in (CHANNEL, SECOND):
            session.add(
                ChannelRecord(
                    id=channel_id,
                    workspace_id=WORKSPACE,
                    name=str(channel_id),
                    visibility="private",
                    created_by_user_id=ACTOR,
                    created_at=instant,
                    updated_at=instant,
                )
            )
        await session.flush()
        for channel_id in (CHANNEL, SECOND):
            session.add(
                ChannelMembershipRecord(
                    workspace_id=WORKSPACE,
                    channel_id=channel_id,
                    user_id=ACTOR,
                    added_by_user_id=ACTOR,
                    joined_at=instant,
                )
            )
        await session.commit()

    class Identity:
        async def authenticate_access(self, token):
            if token not in ("files", "other"):
                raise AuthenticationRequired
            return SimpleNamespace(user=SimpleNamespace(id=ACTOR if token == "files" else OTHER))

        def require_csrf(self, *, identity, csrf_token):
            if csrf_token != "files-csrf":
                raise CsrfValidationFailed

    async def sessions():
        async with factory() as session:
            yield session

    configured = settings.model_copy(update={"files_local_path": str(tmp_path / "files")})
    object.__setattr__(app.state.container, "settings", configured)
    app.dependency_overrides[get_session] = sessions
    app.dependency_overrides[get_identity_service] = Identity
    client.cookies.set("klack_access", "files")
    client.cookies.set("klack_csrf", "files-csrf")
    yield Harness(client, factory, configured)
    app.dependency_overrides.clear()
    await engine.dispose()


async def test_file_only_send_retry_history_download_edit_delete(files: Harness):
    attachment = await files.upload()
    payload = {"attachment_ids": [attachment["id"]], "client_message_id": str(uuid4())}
    response = await files.client.post(BASE + "/messages", json=payload, headers=HEADERS)
    assert response.status_code == 201, response.text
    message = response.json()
    assert message["body"] == "" and message["attachments"] == [attachment]
    repeated = await files.client.post(BASE + "/messages", json=payload, headers=HEADERS)
    assert repeated.json()["id"] == message["id"]
    conflict = await files.client.post(
        BASE + "/messages", json={**payload, "body": "changed"}, headers=HEADERS
    )
    assert conflict.status_code == 409
    assert (await files.client.get(BASE + "/messages")).json()["messages"][0]["attachments"] == [
        attachment
    ]
    assert len((await files.client.get(BASE + "/files")).json()["files"]) == 1
    url = BASE + f"/files/{attachment['id']}/content"
    downloaded = await files.client.get(url + "?preview=true")
    assert downloaded.content == b"hello"
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    for body in ("caption", ""):
        edited = await files.client.patch(
            BASE + f"/messages/{message['id']}", json={"body": body}, headers=HEADERS
        )
        assert edited.status_code == 200 and edited.json()["attachments"] == [attachment]
    assert (
        await files.client.delete(BASE + f"/files/{attachment['id']}", headers=HEADERS)
    ).status_code == 409
    assert (
        await files.client.delete(BASE + f"/messages/{message['id']}", headers=HEADERS)
    ).status_code == 204
    assert (await files.client.get(url)).status_code == 404
    tombstone = (await files.client.get(BASE + "/messages")).json()["messages"][0]
    assert tombstone["body"] is None and tombstone["attachments"] == []
    assert (await files.client.get(BASE + "/files")).json()["files"] == []
    assert await cleanup(files.factory, files.settings) == 1
    assert not await asyncio.to_thread(
        lambda: list(Path(files.settings.files_local_path).rglob(attachment["id"].replace("-", "")))
    )


async def test_attachment_ownership_membership_and_conversation_scope(files: Harness):
    attachment = await files.upload()
    assert (await files.client.get(BASE + f"/files/{attachment['id']}/content")).status_code == 404
    other_base = BASE.replace(str(CHANNEL), str(SECOND))
    payload = {"attachment_ids": [attachment["id"]]}
    assert (
        await files.client.post(other_base + "/messages", json=payload, headers=HEADERS)
    ).status_code == 404
    message = await files.client.post(BASE + "/messages", json=payload, headers=HEADERS)
    assert message.status_code == 201
    # One upload cannot be attached to a second message.
    assert (
        await files.client.post(BASE + "/messages", json=payload, headers=HEADERS)
    ).status_code == 409
    files.client.cookies.set("klack_access", "other")
    assert (await files.client.get(BASE + f"/files/{attachment['id']}/content")).status_code in (
        403,
        404,
    )
    assert (await files.client.get(BASE + "/files")).status_code in (403, 404)
    files.client.cookies.set("klack_access", "files")
    async with files.factory() as session:
        await session.execute(
            delete(ChannelMembershipRecord).where(ChannelMembershipRecord.channel_id == CHANNEL)
        )
        await session.commit()
    assert (await files.client.get(BASE + f"/files/{attachment['id']}/content")).status_code in (
        403,
        404,
    )


async def test_upload_bounds_pending_cancel_expiry_and_csrf(files: Harness):
    assert (await files.client.get(BASE + "/files/limits")).json()["max_bytes"] == 26214400
    request = {"filename": "notes.txt", "size": 5}
    assert (await files.client.post(BASE + "/files", json=request)).status_code in (403, 404)
    for filename, size in [("bad\nname", 5), ("..", 0), ("huge", 26214401)]:
        assert (
            await files.client.post(
                BASE + "/files", json={"filename": filename, "size": size}, headers=HEADERS
            )
        ).status_code in (413, 422)
    pending = (await files.client.post(BASE + "/files", json=request, headers=HEADERS)).json()
    url = BASE + f"/files/{pending['id']}"
    assert (
        await files.client.post(
            BASE + "/messages", json={"attachment_ids": [pending["id"]]}, headers=HEADERS
        )
    ).status_code == 409
    assert (
        await files.client.put(url + "/content", content=b"too long", headers=HEADERS)
    ).status_code == 413
    assert (await files.client.delete(url, headers=HEADERS)).status_code == 204
    assert (
        await files.client.put(url + "/content", content=b"hello", headers=HEADERS)
    ).status_code == 409
    assert (await files.client.delete(url, headers=HEADERS)).status_code == 204
    attachment = await files.upload()
    async with files.factory() as session:
        await session.execute(
            update(FileRecord)
            .where(FileRecord.id == UUID(attachment["id"]))
            .values(expires_at=datetime.now(UTC) - timedelta(hours=1))
        )
        await session.commit()
    assert (
        await files.client.post(
            BASE + "/messages", json={"attachment_ids": [attachment["id"]]}, headers=HEADERS
        )
    ).status_code == 409
    assert await cleanup(files.factory, files.settings) == 2


async def test_image_preview_thread_and_files_pagination(files: Harness):
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    attachment = await files.upload(buffer.getvalue(), "photo.png")
    assert attachment["content_type"] == "image/png"
    root = (
        await files.client.post(BASE + "/messages", json={"body": "thread"}, headers=HEADERS)
    ).json()
    reply = await files.client.post(
        BASE + "/messages",
        json={"reply_to_message_id": root["id"], "attachment_ids": [attachment["id"]]},
        headers=HEADERS,
    )
    assert reply.status_code == 201
    assert (
        await files.client.get(BASE + f"/files/{attachment['id']}/content?preview=true")
    ).headers["content-type"] == "image/png"
    second = await files.upload(filename="second.txt")
    await files.client.post(
        BASE + "/messages", json={"attachment_ids": [second["id"]]}, headers=HEADERS
    )
    page = (await files.client.get(BASE + "/files?limit=1")).json()
    older = (await files.client.get(BASE + "/files?limit=1&before=" + page["next_before"])).json()
    assert older["files"][0]["id"] == attachment["id"] and older["next_before"] is None
    assert (await files.client.get(BASE + f"/files?before={uuid4()}")).status_code == 422


async def test_quota_rate_limit_archival_and_cleanup_retry(files: Harness, monkeypatch):
    async with files.factory() as session:
        configured = files.settings.model_copy(update={"files_workspace_quota_bytes": 5})
        service = FileService(session, configured)
        await service.reserve(ACTOR, WORKSPACE, CHANNEL, "one", 5)

        with pytest.raises(FileError, match="storage limit"):
            await service.reserve(ACTOR, WORKSPACE, CHANNEL, "two", 1)
        await session.rollback()
        service = FileService(
            session, files.settings.model_copy(update={"files_uploads_per_hour": 1})
        )
        with pytest.raises(FileError, match="Too many"):
            await service.reserve(ACTOR, WORKSPACE, CHANNEL, "two", 1)
        await session.rollback()
        await session.execute(
            update(FileRecord).values(
                status="deleting", expires_at=datetime.now(UTC) - timedelta(seconds=1)
            )
        )
        await session.commit()
    original = FileStorage.delete
    monkeypatch.setattr(FileStorage, "delete", lambda *args: (_ for _ in ()).throw(OSError()))
    assert await cleanup(files.factory, files.settings) == 0
    monkeypatch.setattr(FileStorage, "delete", original)
    async with files.factory() as session:
        await session.execute(
            update(FileRecord).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
    assert await cleanup(files.factory, files.settings) == 1
    async with files.factory() as session:
        await session.execute(
            update(ChannelRecord)
            .where(ChannelRecord.id == CHANNEL)
            .values(archived_at=datetime.now(UTC), archived_by_user_id=ACTOR)
        )
        await session.commit()
    assert (
        await files.client.post(
            BASE + "/files", json={"filename": "file", "size": 1}, headers=HEADERS
        )
    ).status_code == 409


async def test_failed_scan_and_misreported_stream_never_become_attachments(
    files: Harness, monkeypatch
):
    async def reserved():
        response = await files.client.post(
            BASE + "/files", headers=HEADERS, json={"filename": "test.bin", "size": 5}
        )
        assert response.status_code == 201
        return response.json()["id"]

    def rejected_scan(*args):
        raise FileError("The file did not pass the safety scan.")

    monkeypatch.setattr("klack.modules.files.router.inspect_content", rejected_scan)
    file_id = await reserved()
    response = await files.client.put(
        BASE + f"/files/{file_id}/content", content=b"hello", headers=HEADERS
    )
    assert response.status_code == 422 and "safety scan" in response.json()["detail"]
    assert (
        await files.client.post(
            BASE + "/messages", headers=HEADERS, json={"attachment_ids": [file_id]}
        )
    ).status_code == 409
    assert await cleanup(files.factory, files.settings) == 1
    for data, expected_status in [(b"123456", 413), (b"123", 422)]:
        file_id = await reserved()
        response = await files.client.put(
            BASE + f"/files/{file_id}/content",
            content=data,
            headers={**HEADERS, "Content-Length": "5"},
        )
        assert response.status_code == expected_status
    assert await cleanup(files.factory, files.settings) == 2


async def test_ready_upload_is_immutable_and_corrupt_download_releases_capacity(
    files: Harness, app, monkeypatch
):
    attachment = await files.upload()
    url = BASE + f"/files/{attachment['id']}/content"
    repeated = await files.client.put(url, content=b"replacement", headers=HEADERS)
    assert repeated.status_code == 200 and repeated.json() == attachment
    assert (
        await files.client.post(
            BASE + "/messages", headers=HEADERS, json={"attachment_ids": [attachment["id"]]}
        )
    ).status_code == 201
    assert (await files.client.get(url)).content == b"hello"
    monkeypatch.setattr(FileStorage, "get", lambda *args: b"truncated")
    assert (await files.client.get(url)).status_code == 503
    assert app.state.files_io_gate._value == 4


async def test_disabled_uploads_lease_cancellation_and_foreign_owner(files: Harness, app):
    pending = (
        await files.client.post(
            BASE + "/files", headers=HEADERS, json={"filename": "test", "size": 5}
        )
    ).json()
    file_id = UUID(pending["id"])
    async with files.factory() as session:
        await session.execute(
            update(FileRecord).where(FileRecord.id == file_id).values(status="uploading")
        )
        await session.commit()
    assert (
        await files.client.delete(BASE + f"/files/{file_id}", headers=HEADERS)
    ).status_code == 409
    async with files.factory() as session:
        await session.execute(
            update(FileRecord)
            .where(FileRecord.id == file_id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        session.add(
            ChannelMembershipRecord(
                workspace_id=WORKSPACE,
                channel_id=CHANNEL,
                user_id=OTHER,
                added_by_user_id=ACTOR,
                joined_at=datetime.now(UTC),
            )
        )
        await session.commit()
    assert await cleanup(files.factory, files.settings) == 1
    attachment = await files.upload()
    files.client.cookies.set("klack_access", "other")
    assert (
        await files.client.post(
            BASE + "/messages", headers=HEADERS, json={"attachment_ids": [attachment["id"]]}
        )
    ).status_code == 404
    files.client.cookies.set("klack_access", "files")
    object.__setattr__(
        app.state.container, "settings", files.settings.model_copy(update={"files_enabled": False})
    )
    assert (await files.client.get(BASE + "/files/limits")).json()["enabled"] is False
    assert (
        await files.client.post(
            BASE + "/files", headers=HEADERS, json={"filename": "test", "size": 5}
        )
    ).status_code == 503
    assert (
        await files.client.put(
            BASE + f"/files/{attachment['id']}/content", content=b"hello", headers=HEADERS
        )
    ).status_code == 503
    assert (
        await files.client.post(
            BASE + "/messages", headers=HEADERS, json={"attachment_ids": [attachment["id"]]}
        )
    ).status_code == 503


async def test_membership_revoked_during_upload_blocks_readiness(files: Harness, monkeypatch):
    from klack.modules.files import router

    original = router.run_in_threadpool

    async def revoke_after_put(function, *args):
        result = await original(function, *args)
        if getattr(function, "__name__", "") == "put":
            async with files.factory() as session:
                await session.execute(
                    delete(ChannelMembershipRecord).where(
                        ChannelMembershipRecord.channel_id == CHANNEL
                    )
                )
                await session.commit()
        return result

    monkeypatch.setattr(router, "run_in_threadpool", revoke_after_put)
    response = await files.client.post(
        BASE + "/files", headers=HEADERS, json={"filename": "test", "size": 5}
    )
    file_id = response.json()["id"]
    uploaded = await files.client.put(
        BASE + f"/files/{file_id}/content", content=b"hello", headers=HEADERS
    )
    assert uploaded.status_code == 403
    async with files.factory() as session:
        record = await session.get(FileRecord, UUID(file_id))
        assert record.status == "deleting"
    assert await cleanup(files.factory, files.settings) == 1

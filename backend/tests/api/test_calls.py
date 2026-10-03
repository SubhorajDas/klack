"""Call authorization, lifecycle, and token grants against FK-backed persistence."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import jwt
import pytest
from pydantic import SecretStr
from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from klack.api.dependencies import get_session
from klack.core.db.metadata import target_metadata
from klack.modules.calling.models import CallRecord, CallSeatRecord
from klack.modules.calling.router import service
from klack.modules.calling.service import CallService
from klack.modules.channels.infrastructure.models import ChannelMembershipRecord, ChannelRecord
from klack.modules.identity.api.dependencies import (
    get_current_identity,
    get_current_mutating_identity,
)
from klack.modules.identity.infrastructure.models import AuthSessionRecord, UserRecord
from klack.modules.workspaces.infrastructure.models import MembershipRecord, WorkspaceRecord

A, B, OUTSIDER, W, C = [UUID(int=n) for n in range(1, 6)]
DEVICE_A, DEVICE_B = uuid4(), uuid4()


@pytest.fixture
async def calls_api(app, client, settings):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def sqlite(connection, _):
        connection.create_function("char_length", 1, len)
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(target_metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    config = settings.model_copy(
        update={
            "livekit_url": "wss://test.livekit.cloud",
            "livekit_api_key": SecretStr("test-key"),
            "livekit_api_secret": SecretStr("test-secret-long-enough-for-hmac-sha256"),
        }
    )
    now = datetime.now(UTC)
    async with factory() as db:
        db.add_all(
            [
                UserRecord(id=uid, email=f"u{uid.int}@example.com", created_at=now)
                for uid in (A, B, OUTSIDER)
            ]
        )
        await db.flush()
        db.add(
            WorkspaceRecord(
                id=W, name="Calls", created_by_user_id=A, created_at=now, updated_at=now
            )
        )
        await db.flush()
        db.add_all(
            [
                MembershipRecord(
                    workspace_id=W,
                    user_id=uid,
                    role="owner" if uid == A else "member",
                    joined_at=now,
                )
                for uid in (A, B, OUTSIDER)
            ]
        )
        db.add(
            ChannelRecord(
                id=C,
                workspace_id=W,
                name="dm-test",
                visibility="private",
                direct_key=f"{A.hex}:{B.hex}",
                created_by_user_id=A,
                created_at=now,
                updated_at=now,
            )
        )
        await db.flush()
        db.add_all(
            [
                ChannelMembershipRecord(
                    channel_id=C, workspace_id=W, user_id=uid, added_by_user_id=A, joined_at=now
                )
                for uid in (A, B)
            ]
        )
        db.add_all(
            [
                AuthSessionRecord(
                    id=uid,
                    user_id=uid,
                    csrf_token_hash="a" * 64,
                    created_at=now,
                    last_seen_at=now,
                    expires_at=now + timedelta(days=1),
                )
                for uid in (A, B, OUTSIDER)
            ]
        )
        await db.commit()
        actor = SimpleNamespace(user=SimpleNamespace(id=A), session=SimpleNamespace(id=A))

        async def database():
            try:
                yield db
            except Exception:
                await db.rollback()
                raise

        def switch(uid):
            actor.user.id = uid
            actor.session.id = uid

        app.dependency_overrides[get_session] = database
        app.dependency_overrides[get_current_identity] = lambda: actor
        app.dependency_overrides[get_current_mutating_identity] = lambda: actor
        app.dependency_overrides[service] = lambda: CallService(db, config)
        yield client, db, switch, config, factory
        app.dependency_overrides.clear()
    await engine.dispose()


async def start(client, **overrides):
    return await client.post(
        "/api/v1/calls",
        json={
            "workspace_id": str(W),
            "channel_id": str(C),
            "device_id": str(DEVICE_A),
            "request_id": str(uuid4()),
            **overrides,
        },
    )


async def act(client, call_id, action, device=DEVICE_B):
    return await client.post(f"/api/v1/calls/{call_id}/{action}", json={"device_id": str(device)})


async def test_accept_token_mute_grants_end_and_history(calls_api):
    client, db, switch, config, _ = calls_api
    response = await start(client)
    assert response.status_code == 201
    call = response.json()
    assert call["owned"] and call["status"] == "ringing"
    cid = call["id"]
    assert (await act(client, cid, "connection/token", DEVICE_A)).status_code == 409
    assert (await act(client, cid, "accept", DEVICE_A)).status_code == 403
    assert (await act(client, cid, "decline", DEVICE_A)).status_code == 403
    switch(B)
    inbox = (await client.get(f"/api/v1/calls?device_id={DEVICE_B}")).json()
    assert inbox["calls"][0]["id"] == cid
    assert not inbox["calls"][0]["owned"]
    assert (await act(client, cid, "accept")).json()["status"] == "active"
    assert (await act(client, cid, "accept")).json()["owned"]
    assert (await act(client, cid, "accept", uuid4())).status_code == 409
    assert (await act(client, cid, "connection/token", uuid4())).status_code == 409
    token = (await act(client, cid, "connection/token")).json()["token"]
    claims = jwt.decode(token, config.livekit_api_secret.get_secret_value(), algorithms=["HS256"])
    assert claims["sub"] == str(B)
    assert claims["video"]["room"] == f"klack-call-{cid}"
    assert claims["video"]["canPublishSources"] == ["microphone"]
    assert claims["video"]["canPublishData"] is False
    assert claims["exp"] - claims["nbf"] == 60
    assert (await act(client, cid, "heartbeat")).status_code == 200
    switch(A)
    assert (await act(client, cid, "heartbeat", DEVICE_A)).status_code == 200
    assert (await act(client, cid, "end", DEVICE_A)).json()["status"] == "ended"
    assert (await act(client, cid, "end", DEVICE_A)).json()["status"] == "ended"
    assert (await act(client, cid, "connection/token", DEVICE_A)).status_code == 409
    assert not list(await db.scalars(select(CallSeatRecord)))
    history = (
        await client.get(f"/api/v1/workspaces/{W}/channels/{C}/calls?device_id={DEVICE_A}")
    ).json()
    assert history["calls"][0]["ended_at"]


@pytest.mark.parametrize("action,status", [("decline", "declined"), ("end", "cancelled")])
async def test_decline_cancel_and_retry(calls_api, action, status):
    client, db, switch, _, _ = calls_api
    rid = str(uuid4())
    first = await start(client, request_id=rid)
    assert (await start(client, request_id=rid)).json()["id"] == first.json()["id"]
    assert (await start(client)).status_code == 409
    assert (await start(client, request_id=rid, device_id=str(uuid4()))).status_code == 409
    switch(B)
    assert (await act(client, rid, action)).json()["status"] == status
    assert not list(await db.scalars(select(CallSeatRecord)))
    assert (await act(client, rid, "accept")).status_code == 409


async def test_expiry_and_missed_call_pagination(calls_api):
    client, db, switch, _, _ = calls_api
    cid = (await start(client)).json()["id"]
    row = await db.get(CallRecord, UUID(cid))
    row.created_at = datetime.now(UTC) - timedelta(seconds=46)
    await db.commit()
    switch(B)
    assert not (await client.get(f"/api/v1/calls?device_id={DEVICE_B}")).json()["calls"]
    path = f"/api/v1/workspaces/{W}/channels/{C}/calls?device_id={DEVICE_B}&limit=1"
    assert (await client.get(path)).json()["calls"][0]["status"] == "missed"
    switch(A)
    second = (await start(client)).json()["id"]
    await act(client, second, "end", DEVICE_A)
    page = (await client.get(path)).json()
    assert page["next_before"] == second
    assert (await client.get(path + f"&before={second}")).json()["calls"][0]["id"] == cid
    assert (await client.get(path + f"&before={uuid4()}")).status_code == 404


@pytest.mark.parametrize("reason", ["stale", "revoked", "removed", "disabled"])
async def test_active_call_loses_lease_or_access(calls_api, reason):
    client, db, switch, _, _ = calls_api
    cid = (await start(client)).json()["id"]
    switch(B)
    await act(client, cid, "accept")
    row = await db.get(CallRecord, UUID(cid))
    if reason == "stale":
        row.callee_seen = datetime.now(UTC) - timedelta(seconds=61)
    elif reason == "revoked":
        auth = await db.get(AuthSessionRecord, B)
        auth.revoked_at = datetime.now(UTC)
    elif reason == "disabled":
        user = await db.get(UserRecord, B)
        user.disabled_at = datetime.now(UTC)
    else:
        await db.execute(
            delete(ChannelMembershipRecord).where(ChannelMembershipRecord.user_id == B)
        )
    await db.commit()
    switch(A)
    assert not (await client.get(f"/api/v1/calls?device_id={DEVICE_A}")).json()["calls"]
    assert row.status == "ended"


async def test_outsider_and_cross_workspace_are_rejected(calls_api):
    client, db, switch, _, _ = calls_api
    cid = (await start(client)).json()["id"]
    switch(OUTSIDER)
    assert (await start(client)).status_code == 404
    assert (await act(client, cid, "accept")).status_code == 404
    assert not (await client.get(f"/api/v1/calls?device_id={DEVICE_B}")).json()["calls"]
    assert (
        await client.get(f"/api/v1/workspaces/{W}/channels/{C}/calls?device_id={DEVICE_B}")
    ).status_code == 404
    switch(A)
    assert (await start(client, workspace_id=str(uuid4()))).status_code == 404
    assert (await act(client, str(uuid4()), "end")).status_code == 404
    await act(client, cid, "end", DEVICE_A)
    await db.execute(delete(ChannelMembershipRecord).where(ChannelMembershipRecord.user_id == B))
    await db.commit()
    assert (await start(client)).status_code == 409


async def test_worker_cleans_cloud_rooms_and_retries(calls_api, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from klack.modules.calling import worker

    client, db, switch, config, factory = calls_api
    cid = (await start(client)).json()["id"]
    switch(B)
    await act(client, cid, "accept")
    await act(client, cid, "end")
    cloud = MagicMock()
    cloud.room.delete_room = AsyncMock()
    cloud.__aenter__ = AsyncMock(return_value=cloud)
    cloud.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(worker.livekit, "LiveKitAPI", lambda *args: cloud)
    await worker.sweep(factory, config)
    cloud.room.delete_room.assert_awaited_once()
    await db.refresh(await db.get(CallRecord, UUID(cid)))
    row = await db.get(CallRecord, UUID(cid))
    assert row.cleanup_after is not None
    row.ended_at = datetime.now(UTC) - timedelta(seconds=100)
    row.cleanup_after = datetime.now(UTC) - timedelta(seconds=1)
    await db.commit()
    await worker.sweep(factory, config)
    await db.refresh(row)
    assert row.cleanup_after is None

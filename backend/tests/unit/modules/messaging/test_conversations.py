"""Real persistence and HTTP tests for conversation features and isolation."""

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from test_message_repository import AUTHOR_ID, CHANNEL_ID, NOW, OTHER_CHANNEL_ID, WORKSPACE_ID
from test_message_repository import session as session

from klack.api.dependencies import get_session
from klack.modules.identity.api.dependencies import (
    get_current_identity,
    get_current_mutating_identity,
)
from klack.modules.identity.infrastructure.models import UserRecord
from klack.modules.workspaces.infrastructure.models import MembershipRecord, WorkspaceRecord


async def test_global_dm_identity_counts_and_departure(conversation_api, session: AsyncSession):
    client, actor = conversation_api
    other = uuid4()
    session.add(
        WorkspaceRecord(
            id=other,
            name="Other workspace",
            created_by_user_id=AUTHOR_ID,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    await session.flush()
    for user_id in [AUTHOR_ID, PEER]:
        session.add(
            MembershipRecord(
                workspace_id=other,
                user_id=user_id,
                role="owner" if user_id == AUTHOR_ID else "member",
                joined_at=NOW,
            )
        )
    await session.commit()
    opened = await client.post(f"{BASE}/direct-messages", json={"user_id": str(PEER)})
    assert opened.status_code == 200
    dm = opened.json()
    reverse_workspace = await client.post(
        f"/api/v1/workspaces/{other}/direct-messages", json={"user_id": str(PEER)}
    )
    assert reverse_workspace.json()["id"] == dm["id"]
    assert (await client.post("/api/v1/direct-messages", json={"user_id": str(PEER)})).json()[
        "id"
    ] == dm["id"]
    path = f"{BASE}/channels/{dm['id']}"
    await client.post(MESSAGES, json={"body": "Channel update"})
    direct = (await client.post(f"{path}/messages", json={"body": "Personal update"})).json()
    actor.user.id = PEER
    counts = (await client.get("/api/v1/unread-counts")).json()
    assert counts["direct_messages"] == 1
    assert counts["channels"][dm["id"]] == 1
    assert (await client.get("/api/v1/direct-messages")).json()["channels"][0]["id"] == dm["id"]
    assert (await client.get("/api/v1/contacts")).status_code == 200
    await session.execute(delete(MembershipRecord).where(MembershipRecord.user_id == PEER))
    await session.commit()
    assert (await client.get(f"{path}/messages")).status_code == 200
    assert len((await client.get("/api/v1/alerts")).json()["alerts"]) == 1
    assert (await client.post("/api/v1/direct-messages", json={"user_id": str(AUTHOR_ID)})).json()[
        "id"
    ] == dm["id"]
    await client.put(f"{path}/read-cursor", json={"message_id": direct["id"]})
    assert (await client.get("/api/v1/unread-counts")).json()["total"] == 0
    actor.user.id = ADMIN
    assert (await client.get("/api/v1/direct-messages")).json() == {"channels": []}
    assert (await client.get(f"/api/v1/direct-messages/{dm['id']}")).status_code == 404


PEER = UUID(int=2)
ADMIN = UUID(int=3)


@pytest.fixture
async def conversation_api(app: FastAPI, client: AsyncClient, session: AsyncSession):
    for user_id in [PEER, ADMIN]:
        session.add(UserRecord(id=user_id, email=f"{user_id.int}@example.com", created_at=NOW))
        await session.flush()
        session.add(
            MembershipRecord(
                workspace_id=WORKSPACE_ID,
                user_id=user_id,
                role="admin" if user_id == ADMIN else "member",
                joined_at=NOW,
            )
        )
    await session.commit()
    actor = SimpleNamespace(user=SimpleNamespace(id=AUTHOR_ID))

    async def db():
        try:
            yield session
        except Exception:
            await session.rollback()
            raise

    app.dependency_overrides[get_session] = db
    app.dependency_overrides[get_current_identity] = lambda: actor
    app.dependency_overrides[get_current_mutating_identity] = lambda: actor
    yield client, actor
    app.dependency_overrides.clear()


BASE = f"/api/v1/workspaces/{WORKSPACE_ID}"
MESSAGES = f"{BASE}/channels/{CHANNEL_ID}/messages"


async def test_quoted_replies_reactions_and_monotonic_reads(conversation_api):
    client, _actor = conversation_api
    first = (await client.post(MESSAGES, json={"body": "Root"})).json()
    root = first["id"]
    retry = str(uuid4())
    reply_payload = {"body": "Reply", "reply_to_message_id": root, "client_message_id": retry}
    reply = await client.post(MESSAGES, json=reply_payload)
    assert reply.status_code == 201
    reply_id = reply.json()["id"]
    assert (await client.post(MESSAGES, json=reply_payload)).json()["id"] == reply_id
    assert (
        await client.post(MESSAGES, json={**reply_payload, "reply_to_message_id": None})
    ).status_code == 409
    timeline = (await client.get(MESSAGES)).json()["messages"]
    assert [m["id"] for m in timeline] == [reply_id, root]
    assert timeline[0]["quote"]["id"] == root
    assert timeline[0]["quote"]["body"] == "Root"
    assert timeline[1]["revision"] == 1
    assert (await client.get(MESSAGES, params={"before": reply_id})).status_code == 200
    nested = await client.post(MESSAGES, json={"body": "Nested", "reply_to_message_id": reply_id})
    assert nested.status_code == 201
    assert nested.json()["quote"]["body"] == "Reply"
    assert (await client.get(MESSAGES, params={"around": reply_id})).status_code == 200
    assert (
        await client.post(
            f"{BASE}/channels/{OTHER_CHANNEL_ID}/messages",
            json={"body": "Cross", "reply_to_message_id": root},
        )
    ).status_code == 404
    reaction = f"{MESSAGES}/{root}/reactions/👍"
    reacted = await client.put(reaction)
    assert reacted.status_code == 200
    assert reacted.json()["reactions"] == [["👍", str(AUTHOR_ID)]]
    assert (await client.put(reaction)).json()["revision"] == reacted.json()["revision"]
    assert (await client.delete(reaction)).json()["reactions"] == []
    assert (await client.delete(reaction)).json()["reactions"] == []
    assert (await client.put(f"{MESSAGES}/{root}/reactions/nope")).status_code == 422
    assert (await client.put(f"{MESSAGES}/{uuid4()}/reactions/👍")).status_code == 404
    read = f"{BASE}/channels/{CHANNEL_ID}/read-cursor"
    assert (await client.get(read)).json() == {"message_id": None, "unread_count": 0}
    assert (await client.put(read, json={"message_id": reply_id})).json()["message_id"] == reply_id
    assert (await client.put(read, json={"message_id": root})).json()["message_id"] == reply_id
    assert (await client.put(read, json={"message_id": str(uuid4())})).status_code == 404
    assert (await client.delete(f"{MESSAGES}/{root}")).status_code == 204
    assert (await client.put(reaction)).status_code == 409
    timeline = (await client.get(MESSAGES)).json()["messages"]
    quoted = next(m for m in timeline if m["id"] == reply_id)
    assert quoted["quote"]["body"] is None
    assert quoted["quote"]["deleted_at"] is not None
    assert (
        await client.post(MESSAGES, json={"body": "After deletion", "reply_to_message_id": root})
    ).status_code == 201


async def test_direct_privacy_and_unread(conversation_api, session: AsyncSession):
    client, actor = conversation_api
    dms = f"{BASE}/direct-messages"
    assert (await client.post(dms, json={"user_id": str(AUTHOR_ID)})).status_code == 422
    assert (await client.post(dms, json={"user_id": str(uuid4())})).status_code == 404
    opened = await client.post(dms, json={"user_id": str(PEER)})
    assert opened.status_code == 200
    channel = opened.json()["id"]
    path = f"{BASE}/channels/{channel}"
    root = (await client.post(f"{path}/messages", json={"body": "Private"})).json()["id"]
    assert (await client.get(f"{BASE}/channels")).json()["channels"][0]["id"] == str(CHANNEL_ID)
    assert len((await client.get(dms)).json()["channels"]) == 1
    actor.user.id = PEER
    assert (await client.post(dms, json={"user_id": str(AUTHOR_ID)})).json()["id"] == channel
    assert (await client.get(f"{path}/read-cursor")).json()["unread_count"] == 1
    assert (await client.put(f"{path}/read-cursor", json={"message_id": root})).json()[
        "unread_count"
    ] == 0
    second = (await client.post(f"{path}/messages", json={"body": "Hi"})).json()["id"]
    assert (await client.put(f"{path}/read-cursor", json={"message_id": second})).json()[
        "message_id"
    ] == second
    assert (await client.put(f"{path}/read-cursor", json={"message_id": root})).json()[
        "message_id"
    ] == second
    actor.user.id = ADMIN
    assert (await client.get(dms)).json()["channels"] == []
    for route in [path, f"{path}/messages", f"{path}/memberships", f"{path}/read-cursor"]:
        assert (await client.get(route)).status_code == 404
    for method, route, body in [
        ("PUT", "/memberships/me", None),
        ("PUT", f"/memberships/{ADMIN}", None),
        ("DELETE", f"/memberships/{PEER}", None),
        ("POST", "/archive", None),
        ("POST", "/unarchive", None),
        ("PATCH", "", {"visibility": "public"}),
    ]:
        assert (await client.request(method, path + route, json=body)).status_code == 404
    actor.user.id = AUTHOR_ID
    assert (await client.delete(f"{path}/memberships/me")).status_code == 404
    await session.execute(
        delete(MembershipRecord).where(
            MembershipRecord.workspace_id == WORKSPACE_ID, MembershipRecord.user_id == PEER
        )
    )
    await session.commit()
    actor.user.id = PEER
    assert (await client.get(f"{path}/messages")).status_code == 200
    assert len((await client.get("/api/v1/direct-messages")).json()["channels"]) == 1


async def test_reply_pagination_and_archival(conversation_api):
    client, _actor = conversation_api
    root = (await client.post(MESSAGES, json={"body": "Root"})).json()["id"]
    ids = [
        (await client.post(MESSAGES, json={"body": str(i), "reply_to_message_id": root})).json()[
            "id"
        ]
        for i in range(3)
    ]
    first = (await client.get(MESSAGES, params={"limit": 2})).json()
    assert [m["id"] for m in first["messages"]] == list(reversed(ids[1:]))
    second = (
        await client.get(MESSAGES, params={"limit": 2, "before": first["next_before"]})
    ).json()
    assert [m["id"] for m in second["messages"]] == [ids[0], root]
    assert second["next_before"] is None
    assert (await client.post(f"{BASE}/channels/{CHANNEL_ID}/archive")).status_code == 200
    assert (await client.put(f"{MESSAGES}/{root}/reactions/👍")).status_code == 409
    assert (
        await client.post(MESSAGES, json={"body": "closed", "reply_to_message_id": root})
    ).status_code == 409
    assert (await client.get(MESSAGES, params={"around": root})).status_code == 200


async def test_quotes_follow_edits_and_deletions_outside_the_history_page(conversation_api):
    client, _actor = conversation_api
    original = (await client.post(MESSAGES, json={"body": "Original " * 60})).json()
    reply = (
        await client.post(
            MESSAGES,
            json={
                "body": "Quoted reply",
                "reply_to_message_id": original["id"],
            },
        )
    ).json()
    assert len(reply["quote"]["body"]) == 240
    await client.patch(f"{MESSAGES}/{original['id']}", json={"body": "Edited original"})
    page = (await client.get(MESSAGES, params={"limit": 1})).json()
    assert len(page["messages"]) == 1
    assert page["messages"][0]["quote"]["body"] == "Edited original"
    await client.delete(f"{MESSAGES}/{original['id']}")
    page = (await client.get(MESSAGES, params={"limit": 1})).json()
    assert page["messages"][0]["quote"]["body"] is None
    assert page["messages"][0]["quote"]["attachment_count"] == 0
    assert page["messages"][0]["quote"]["deleted_at"] is not None
    await client.delete(f"{MESSAGES}/{reply['id']}")
    page = (await client.get(MESSAGES, params={"limit": 1})).json()
    assert page["messages"][0]["quote"] is None


async def test_quote_context_validates_channel_access_and_cursors(conversation_api):
    client, _actor = conversation_api
    root = (await client.post(MESSAGES, json={"body": "Original"})).json()["id"]
    ids = [
        (await client.post(MESSAGES, json={"body": f"Message {i}"})).json()["id"] for i in range(8)
    ]
    page = (await client.get(MESSAGES, params={"around": ids[3], "limit": 4})).json()
    assert [m["id"] for m in page["messages"]] == list(reversed(ids[1:6]))
    assert (
        await client.get(MESSAGES, params={"around": ids[3], "before": root})
    ).status_code == 422
    assert (await client.get(MESSAGES, params={"around": str(uuid4())})).status_code == 404
    other = f"{BASE}/channels/{OTHER_CHANNEL_ID}/messages"
    assert (await client.get(other, params={"around": root})).status_code == 404
    assert (
        await client.post(MESSAGES, json={"body": "Invalid", "reply_to_message_id": str(uuid4())})
    ).status_code == 404


async def test_alerts_include_replies_and_enforce_membership(
    conversation_api, session: AsyncSession
):
    client, actor = conversation_api
    dm = (await client.post(f"{BASE}/direct-messages", json={"user_id": str(PEER)})).json()
    path = f"{BASE}/channels/{dm['id']}"
    root = (await client.post(f"{path}/messages", json={"body": "Hello"})).json()
    reply = (
        await client.post(
            f"{path}/messages", json={"body": "Quoted update", "reply_to_message_id": root["id"]}
        )
    ).json()
    deleted = (await client.post(f"{path}/messages", json={"body": "Removed"})).json()
    await client.delete(f"{path}/messages/{deleted['id']}")
    assert (await client.get(f"{BASE}/alerts")).json() == {"alerts": []}
    actor.user.id = PEER
    own = await client.post(f"{path}/messages", json={"body": "My message"})
    assert own.status_code == 201
    response = await client.get(f"{BASE}/alerts")
    assert response.status_code == 200
    alerts = response.json()["alerts"]
    assert len(alerts) == 1
    assert alerts[0]["channel"]["id"] == dm["id"]
    assert alerts[0]["unread_count"] == 2
    assert alerts[0]["message"]["id"] == reply["id"]
    assert alerts[0]["message"]["reply_to_message_id"] == root["id"]
    await client.put(f"{path}/read-cursor", json={"message_id": root["id"]})
    assert (await client.get(f"{BASE}/alerts")).json()["alerts"][0]["unread_count"] == 1
    await client.put(f"{path}/read-cursor", json={"message_id": reply["id"]})
    assert (await client.get(f"{BASE}/alerts")).json() == {"alerts": []}
    actor.user.id = AUTHOR_ID
    assert len((await client.get(f"{BASE}/alerts")).json()["alerts"]) == 1
    actor.user.id = ADMIN
    assert (await client.get(f"{BASE}/alerts")).json() == {"alerts": []}
    assert (await client.get(f"/api/v1/workspaces/{uuid4()}/alerts")).status_code == 404
    await session.execute(
        delete(MembershipRecord).where(
            MembershipRecord.workspace_id == WORKSPACE_ID, MembershipRecord.user_id == PEER
        )
    )
    await session.commit()
    actor.user.id = PEER
    assert (await client.get(f"{BASE}/alerts")).status_code == 404


async def test_alerts_channel_membership_archival_and_new_arrivals(conversation_api):
    client, actor = conversation_api
    channel_path = f"{BASE}/channels/{CHANNEL_ID}"
    first = (await client.post(MESSAGES, json={"body": "First"})).json()
    actor.user.id = PEER
    # Public visibility alone must not subscribe someone to alerts.
    assert (await client.get(f"{BASE}/alerts")).json() == {"alerts": []}
    assert (await client.put(f"{channel_path}/memberships/me")).status_code == 200
    snapshot = (await client.get(f"{BASE}/alerts")).json()["alerts"][0]
    assert snapshot["message"]["id"] == first["id"]
    actor.user.id = AUTHOR_ID
    newer = (await client.post(MESSAGES, json={"body": "Arrived after snapshot"})).json()
    await client.post(f"{channel_path}/archive")
    actor.user.id = PEER
    await client.put(f"{channel_path}/read-cursor", json={"message_id": first["id"]})
    remaining = (await client.get(f"{BASE}/alerts")).json()["alerts"]
    assert len(remaining) == 1
    assert remaining[0]["channel"]["archived_at"] is not None
    assert remaining[0]["unread_count"] == 1
    assert remaining[0]["message"]["id"] == newer["id"]
    actor.user.id = AUTHOR_ID
    await client.delete(f"{channel_path}/memberships/{PEER}")
    actor.user.id = PEER
    assert (await client.get(f"{BASE}/alerts")).json() == {"alerts": []}

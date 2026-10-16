"""Exercise organization and notification behavior through HTTP and real persistence."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from test_conversations import ADMIN, BASE, MESSAGES, PEER
from test_conversations import conversation_api as conversation_api
from test_message_repository import AUTHOR_ID, CHANNEL_ID
from test_message_repository import session as session

from klack.modules.channels.infrastructure.models import ChannelMembershipRecord
from klack.modules.messaging.infrastructure.models import MentionRecord, NotificationPauseRecord
from klack.modules.workspaces.infrastructure.models import MembershipRecord

PATH = f"{BASE}/channels/{CHANNEL_ID}"


def mention(user=PEER, label="2"):
    return {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Hello "},
                    {"type": "mention", "attrs": {"id": str(user), "label": label}},
                ],
            }
        ],
    }


async def join_peer(client, actor):
    actor.user.id = PEER
    assert (await client.put(f"{PATH}/memberships/me")).status_code == 200
    actor.user.id = AUTHOR_ID


async def test_shared_pins_private_saves_and_access_revocation(conversation_api, session):
    client, actor = conversation_api
    await join_peer(client, actor)
    message = (await client.post(MESSAGES, json={"body": "Keep this"})).json()
    pin = f"{MESSAGES}/{message['id']}/pin"
    save = f"{MESSAGES}/{message['id']}/save"
    for _ in range(2):
        assert (await client.put(pin)).status_code == 204
        assert (await client.put(save)).status_code == 204
    assert (await client.get(f"{PATH}/message-marks")).json() == {
        "pinned": [message["id"]],
        "saved": [message["id"]],
    }
    assert len((await client.get("/api/v1/saved-messages")).json()["items"]) == 1
    actor.user.id = PEER
    assert (await client.get("/api/v1/saved-messages")).json()["items"] == []
    assert len((await client.get(f"{PATH}/pins")).json()["items"]) == 1
    assert (await client.delete(pin)).status_code == 204
    assert (await client.get(f"{PATH}/pins")).json()["items"] == []
    actor.user.id = ADMIN
    assert (await client.get(f"{PATH}/pins")).status_code == 403
    assert (await client.put(save)).status_code == 403
    actor.user.id = AUTHOR_ID
    await session.execute(delete(MembershipRecord).where(MembershipRecord.user_id == AUTHOR_ID))
    await session.commit()
    assert (await client.get("/api/v1/saved-messages")).json()["items"] == []


async def test_collections_use_current_content_and_hide_deletions(conversation_api):
    client, _ = conversation_api
    message = (await client.post(MESSAGES, json={"body": "Original"})).json()
    url = f"{MESSAGES}/{message['id']}"
    await client.put(url + "/pin")
    await client.put(url + "/save")
    await client.patch(url, json={"body": "Updated"})
    for path in [f"{PATH}/pins", "/api/v1/saved-messages"]:
        assert (await client.get(path)).json()["items"][0]["message"]["body"] == "Updated"
    await client.delete(url)
    for path in [f"{PATH}/pins", "/api/v1/saved-messages"]:
        assert (await client.get(path)).json()["items"] == []
    assert (await client.put(url + "/pin")).status_code == 409


async def test_mentions_are_validated_and_preferences_filter_alerts(conversation_api, session):
    client, actor = conversation_api
    assert (
        await client.post(MESSAGES, json={"body": "Hello @2", "document": mention()})
    ).status_code == 422
    await join_peer(client, actor)
    assert len((await client.get(f"{PATH}/mention-candidates")).json()) == 2
    result = await client.post(MESSAGES, json={"body": "Hello @2", "document": mention()})
    assert result.status_code == 201, result.text
    message = result.json()
    assert (
        await client.post(
            MESSAGES, json={"body": "Hello @admin", "document": mention(ADMIN, "admin")}
        )
    ).status_code == 422
    await client.post(MESSAGES, json={"body": "Ordinary update"})
    actor.user.id = PEER
    assert (await client.get(f"{PATH}/notifications")).json() == {"mode": "all"}
    assert (await client.put(f"{PATH}/notifications", json={"mode": "mentions"})).status_code == 200
    alerts = (await client.get("/api/v1/alerts")).json()["alerts"]
    assert len(alerts) == 1
    assert alerts[0]["message"]["id"] == message["id"]
    assert alerts[0]["unread_count"] == 1
    assert (await client.get("/api/v1/unread-counts")).json()["total"] == 2
    await client.put(f"{PATH}/notifications", json={"mode": "muted"})
    assert (await client.get("/api/v1/alerts")).json()["alerts"] == []
    await client.put(f"{PATH}/notifications", json={"mode": "all"})
    assert (await client.get("/api/v1/alerts")).json()["alerts"][0]["unread_count"] == 2
    assert (await client.put("/api/v1/notification-pause", json={"minutes": 60})).status_code == 200
    assert (await client.get("/api/v1/alerts")).json()["alerts"] == []
    assert (await client.get("/api/v1/unread-counts")).json()["total"] == 2
    pause = await session.get(NotificationPauseRecord, PEER)
    pause.paused_until = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()
    assert len((await client.get("/api/v1/alerts")).json()["alerts"]) == 1
    await client.put("/api/v1/notification-pause", json={"minutes": 0})
    assert (await client.get("/api/v1/notification-pause")).json() == {"paused_until": None}
    actor.user.id = AUTHOR_ID
    await client.patch(f"{MESSAGES}/{message['id']}", json={"body": "No mention now"})
    assert list(await session.scalars(select(MentionRecord))) == []


async def test_dm_bookmarks_survive_workspace_departure_and_are_private(conversation_api, session):
    client, actor = conversation_api
    dm = (await client.post("/api/v1/direct-messages", json={"user_id": str(PEER)})).json()
    path = f"{BASE}/channels/{dm['id']}"
    message = (await client.post(path + "/messages", json={"body": "Private"})).json()
    assert (await client.put(f"{path}/messages/{message['id']}/save")).status_code == 204
    assert (await client.put(f"{path}/messages/{message['id']}/pin")).status_code == 204
    await session.execute(delete(MembershipRecord).where(MembershipRecord.user_id == AUTHOR_ID))
    await session.commit()
    assert len((await client.get("/api/v1/saved-messages")).json()["items"]) == 1
    assert len((await client.get(path + "/mention-candidates")).json()) == 2
    actor.user.id = ADMIN
    assert (await client.get(path + "/pins")).status_code == 404
    assert (await client.get(path + "/mention-candidates")).status_code == 404


async def test_pagination_and_membership_cleanup(conversation_api, session):
    client, actor = conversation_api
    await join_peer(client, actor)
    for body in ["First", "Second", "Third"]:
        message = (await client.post(MESSAGES, json={"body": body})).json()
        await client.put(f"{MESSAGES}/{message['id']}/save")
    first = (await client.get("/api/v1/saved-messages?limit=2")).json()
    assert len(first["items"]) == 2 and first["next_offset"] == 2
    second = (await client.get("/api/v1/saved-messages?limit=2&offset=2")).json()
    assert len(second["items"]) == 1 and second["next_offset"] is None
    actor.user.id = PEER
    await client.put(f"{PATH}/notifications", json={"mode": "muted"})
    await session.execute(
        delete(ChannelMembershipRecord).where(
            ChannelMembershipRecord.channel_id == CHANNEL_ID,
            ChannelMembershipRecord.user_id == PEER,
        )
    )
    await session.commit()
    assert (await client.get(f"{PATH}/notifications")).status_code == 403
    await client.put(f"{PATH}/memberships/me")
    assert (await client.get(f"{PATH}/notifications")).json() == {"mode": "all"}


async def test_retained_mentions_do_not_block_reactions_after_recipient_leaves(
    conversation_api, session
):
    client, actor = conversation_api
    await join_peer(client, actor)
    message = (await client.post(MESSAGES, json={"body": "Hello @2", "document": mention()})).json()
    await session.execute(
        delete(ChannelMembershipRecord).where(
            ChannelMembershipRecord.channel_id == CHANNEL_ID,
            ChannelMembershipRecord.user_id == PEER,
        )
    )
    await session.commit()
    assert (await client.put(f"{MESSAGES}/{message['id']}/reactions/👍")).status_code == 200
    assert (
        await client.patch(
            f"{MESSAGES}/{message['id']}", json={"body": "Hello @2", "document": mention()}
        )
    ).status_code == 200


async def test_organization_migration_round_trip(session):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect

    from klack.core.db.metadata import target_metadata

    path = Path(__file__).parents[4] / "alembic/versions/20261016_0013_message_organization.py"
    spec = importlib.util.spec_from_file_location("organization_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    names = [
        "message_mentions",
        "message_pins",
        "message_saved",
        "message_notification_preferences",
        "message_notification_pauses",
    ]

    def verify(connection):
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            assert not set(names).intersection(inspect(connection).get_table_names())
            migration.upgrade()
            for name in names:
                table = target_metadata.tables[name]
                actual = inspect(connection).get_columns(name)
                assert {column["name"] for column in actual} == set(table.columns.keys())
                assert inspect(connection).get_pk_constraint(name)["constrained_columns"] == [
                    column.name for column in table.primary_key.columns
                ]
            migration.downgrade()
            migration.upgrade()

    connection = await session.connection()
    await connection.run_sync(verify)


async def test_saved_messages_require_membership_in_the_exact_channel(conversation_api, session):
    client, _actor = conversation_api
    message = (await client.post(MESSAGES, json={"body": "Private reference"})).json()
    await client.put(f"{MESSAGES}/{message['id']}/save")
    await session.execute(
        delete(ChannelMembershipRecord).where(
            ChannelMembershipRecord.channel_id == CHANNEL_ID,
            ChannelMembershipRecord.user_id == AUTHOR_ID,
        )
    )
    await session.commit()
    assert (await client.get("/api/v1/saved-messages")).json()["items"] == []
    assert (await client.get(f"{PATH}/message-marks")).status_code == 403


@pytest.mark.parametrize(
    "attrs",
    [
        {},
        {"id": "invalid", "label": "alex"},
        {"id": None, "label": "alex"},
        {"id": str(PEER), "label": ""},
        {"id": str(PEER), "label": " "},
        {"id": str(PEER), "label": "x" * 101},
        {"id": str(PEER), "label": "a\nb"},
        {"id": str(PEER), "label": 12},
        {"id": str(PEER), "label": "alex", "extra": True},
    ],
)
async def test_invalid_mention_atoms_are_rejected(conversation_api, attrs):
    client, _ = conversation_api
    document = {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "mention", "attrs": attrs}]}],
    }
    response = await client.post(MESSAGES, json={"body": "@alex", "document": document})
    assert response.status_code == 422


async def test_archive_policies_missing_messages_and_idempotent_removal(conversation_api):
    client, _ = conversation_api
    from uuid import uuid4

    message = (await client.post(MESSAGES, json={"body": "Reference"})).json()
    url = f"{MESSAGES}/{message['id']}"
    assert (await client.put(f"{MESSAGES}/{uuid4()}/pin")).status_code == 404
    await client.put(url + "/save")
    await client.delete(url + "/save")
    assert (await client.delete(url + "/save")).status_code == 204
    assert (await client.delete(url + "/pin")).status_code == 204
    assert (await client.post(PATH + "/archive")).status_code == 200
    assert (await client.put(url + "/pin")).status_code == 409
    assert (await client.put(url + "/save")).status_code == 204
    assert len((await client.get("/api/v1/saved-messages")).json()["items"]) == 1
    assert (await client.put(f"{PATH}/notifications", json={"mode": "all"})).status_code == 200
    assert (await client.put(f"{PATH}/notifications", json={"mode": "all"})).status_code == 200
    assert (await client.put(f"{PATH}/notifications", json={"mode": "wrong"})).status_code == 422
    assert (await client.put("/api/v1/notification-pause", json={"minutes": 13})).status_code == 422

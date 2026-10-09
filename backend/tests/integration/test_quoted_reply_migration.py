"""Verify existing thread records survive conversion on disposable PostgreSQL."""

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from test_global_dm_migration import migration_database as migration_database
from test_messages_postgresql import _message_service, _seed_conversation, _settings

from klack.bootstrap import build_container

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="requires a disposable PostgreSQL test database",
    ),
]


def test_existing_thread_replies_keep_all_record_values(migration_database) -> None:
    del migration_database
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    command.upgrade(config, "20260928_0009")

    async def seed():
        container = build_container(_settings())
        try:
            seeded = await _seed_conversation(container)
            async with container.session_factory() as session:
                root = SimpleNamespace(id=uuid4(), body="Legacy root")
                replies = [SimpleNamespace(id=uuid4(), body=f"Legacy reply {i}") for i in range(3)]
                for index, message in enumerate([root, *replies]):
                    await session.execute(
                        text("""
                        INSERT INTO message_messages(id,workspace_id,channel_id,author_user_id,
                            body,created_at,revision,attachment_count,parent_message_id)
                        VALUES(:id,:workspace,:channel,:author,:body,
                            now()+make_interval(secs=>:index),1,0,:parent)
                    """),
                        {
                            "id": message.id,
                            "workspace": seeded.workspace_id,
                            "channel": seeded.channel_id,
                            "author": seeded.owner_id,
                            "body": message.body,
                            "index": index,
                            "parent": root.id if index else None,
                        },
                    )
                await session.commit()
                return seeded, root, replies
        finally:
            await container.engine.dispose()

    seeded, root, replies = asyncio.run(seed())
    # Inspect actual legacy thread rows before applying newer migrations.

    async def records(legacy: bool):
        connection = await asyncpg.connect(
            _settings().database_url_value().replace("postgresql+asyncpg://", "postgresql://")
        )
        try:
            rows = await connection.fetch(
                "SELECT * FROM message_messages WHERE channel_id=$1 ORDER BY id",
                seeded.channel_id,
            )
            values = [dict(row) for row in rows]
            if legacy:
                for value in values:
                    value["reply_to_message_id"] = value.pop("parent_message_id")
                    value["document"] = None
            return values
        finally:
            await connection.close()

    previous = asyncio.run(records(True))
    command.upgrade(config, "head")
    assert asyncio.run(records(False)) == previous
    command.check(config)

    async def history():
        container = build_container(_settings())
        try:
            async with container.session_factory() as session:
                return await _message_service(container, session).list_messages(
                    actor_user_id=seeded.owner_id,
                    workspace_id=seeded.workspace_id,
                    channel_id=seeded.channel_id,
                )
        finally:
            await container.engine.dispose()

    page = asyncio.run(history())
    assert [message.id for message in page.messages] == [m.id for m in reversed(replies)] + [
        root.id
    ]
    assert all(
        m.quote and m.quote.id == root.id and m.quote.body == root.body
        for m in page.messages
        if m.id != root.id
    )

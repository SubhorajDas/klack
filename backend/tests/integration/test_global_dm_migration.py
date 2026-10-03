"""Check history consolidation against the previous schema in a disposable database."""

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from test_conversations_postgresql import conversation
from test_messages_postgresql import _message_service, _seed_conversation, _settings

from klack.core.container import build_container

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL"),
]


@pytest.fixture
def migration_database(monkeypatch):
    source = _settings().database_url_value().replace("postgresql+asyncpg://", "postgresql://")
    name = f"klack_migration_{uuid4().hex}"
    target = source.rsplit("/", 1)[0] + "/" + name

    async def database(create):
        connection = await asyncpg.connect(source)
        try:
            if create:
                await connection.execute(f'CREATE DATABASE "{name}"')
            else:
                await connection.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        finally:
            await connection.close()

    asyncio.run(database(True))
    monkeypatch.setenv("DATABASE_URL", target.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        yield target
    finally:
        asyncio.run(database(False))


def test_merge_keeps_quotes_files_reactions_reads_and_old_links(migration_database):
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    command.upgrade(config, "20261003_0010")

    async def seed():
        container = build_container(_settings())
        try:
            seeded = await _seed_conversation(container)
            async with container.session_factory() as session:
                dm = await conversation(session).open_direct(
                    seeded.workspace_id, seeded.owner_id, seeded.member_id
                )
                root = await _message_service(container, session).create_message(
                    actor_user_id=seeded.member_id,
                    workspace_id=seeded.workspace_id,
                    channel_id=dm.channel.id,
                    body="First workspace history",
                    client_message_id=uuid4(),
                )
                await conversation(session).read_state(
                    seeded.workspace_id, dm.channel.id, seeded.owner_id, root.id
                )
            return seeded, dm.channel.id, root
        finally:
            await container.engine.dispose()

    seeded, canonical, root = asyncio.run(seed())
    other_workspace, old_dm, other_root, reply, file_id = [uuid4() for _ in range(5)]

    async def legacy():
        connection = await asyncpg.connect(migration_database)
        try:
            await connection.execute(
                """
                INSERT INTO workspace_workspaces
                SELECT $1,'Other workspace',created_by_user_id,created_at,updated_at
                FROM workspace_workspaces WHERE id=$2
            """,
                other_workspace,
                seeded.workspace_id,
            )
            await connection.execute(
                """
                INSERT INTO workspace_memberships
                SELECT $1,user_id,role,joined_at FROM workspace_memberships WHERE workspace_id=$2
            """,
                other_workspace,
                seeded.workspace_id,
            )
            await connection.execute(
                """
                INSERT INTO channel_channels(id,workspace_id,name,visibility,direct_key,
                    created_by_user_id,created_at,updated_at)
                SELECT $1,$2,'legacy-dm','private',direct_key,created_by_user_id,
                    created_at+interval '1 second',updated_at
                FROM channel_channels WHERE id=$3
            """,
                old_dm,
                other_workspace,
                canonical,
            )
            await connection.execute(
                """
                INSERT INTO channel_memberships
                SELECT $1,$2,user_id,added_by_user_id,joined_at FROM channel_memberships
                WHERE channel_id=$3
            """,
                other_workspace,
                old_dm,
                canonical,
            )
            for index, message_id in enumerate([other_root, reply]):
                await connection.execute(
                    """
                    INSERT INTO message_messages(id,workspace_id,channel_id,author_user_id,body,
                        created_at,client_message_id,revision,reply_to_message_id,attachment_count)
                    SELECT $1,$2,$3,author_user_id,$4,created_at+make_interval(secs=>$5),
                        CASE WHEN $6::uuid IS NULL THEN client_message_id ELSE NULL END,1,$6,$7
                    FROM message_messages WHERE id=$8
                """,
                    message_id,
                    other_workspace,
                    old_dm,
                    f"Legacy message {index}",
                    index + 2,
                    other_root if index else None,
                    1 if index else 0,
                    root.id,
                )
            await connection.execute(
                """
                INSERT INTO message_reactions VALUES($1,$2,'👍')
            """,
                reply,
                seeded.owner_id,
            )
            await connection.execute(
                """
                INSERT INTO file_uploads(id,workspace_id,channel_id,uploader_id,message_id,position,
                    filename,size,content_type,storage_key,status,created_at,expires_at)
                VALUES($1,$2,$3,$4,$5,0,'demo.txt',4,'text/plain',$6,'attached',now(),now())
            """,
                file_id,
                other_workspace,
                old_dm,
                seeded.member_id,
                reply,
                str(file_id),
            )
        finally:
            await connection.close()

    asyncio.run(legacy())
    command.upgrade(config, "head")
    command.check(config)

    async def verify():
        container = build_container(_settings())
        try:
            async with container.session_factory() as session:
                service = conversation(session)
                assert (
                    await service.resolve_direct(old_dm, seeded.owner_id)
                ).channel.id == canonical
                assert (
                    await service.open_direct(other_workspace, seeded.member_id, seeded.owner_id)
                ).channel.id == canonical
                page = await _message_service(container, session).list_messages(
                    actor_user_id=seeded.owner_id,
                    workspace_id=seeded.workspace_id,
                    channel_id=canonical,
                )
                assert {m.id for m in page.messages} == {root.id, other_root, reply}
                last = next(m for m in page.messages if m.id == reply)
                assert last.quote.id == other_root
                assert last.attachments[0].id == file_id
                assert last.reactions == (("👍", seeded.owner_id),)
                assert (await service.read_state(seeded.workspace_id, canonical, seeded.owner_id))[
                    1
                ] == 2
                assert len(await service.global_direct(seeded.owner_id)) == 1
        finally:
            await container.engine.dispose()

    asyncio.run(verify())

"""Consolidate personal DMs, preserving messages and old links.

Revision ID: 20261004_0011
Revises: 20261003_0010
"""

import sqlalchemy as sa
from alembic import op

revision = "20261004_0011"
down_revision = "20261003_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The oldest conversation remains the storage home; it is no longer an access scope.
    op.execute(
        "LOCK TABLE channel_channels, channel_memberships, message_messages, "
        "message_read_cursors IN ACCESS EXCLUSIVE MODE"
    )
    op.create_table(
        "message_direct_aliases",
        sa.Column("old_channel_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "channel_id",
            sa.Uuid(),
            sa.ForeignKey("channel_channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.execute("""
        CREATE TEMP TABLE dm_merge ON COMMIT DROP AS
        SELECT id AS old_id, workspace_id AS old_workspace,
               first_value(id) OVER pair AS keep_id,
               first_value(workspace_id) OVER pair AS keep_workspace
        FROM channel_channels WHERE direct_key IS NOT NULL
        WINDOW pair AS (PARTITION BY direct_key ORDER BY created_at, id)
    """)
    # A single cursor can represent only a read prefix. Choose a conservative prefix
    # before the first previously unread incoming message, so none is silently cleared.
    op.execute("""
        CREATE TEMP TABLE dm_read_positions ON COMMIT DROP AS
        WITH people AS (
            SELECT DISTINCT map.keep_id, cm.user_id
            FROM dm_merge map JOIN channel_memberships cm ON cm.channel_id=map.old_id
        ), boundaries AS (
            SELECT p.*, (
                SELECT m.id FROM message_messages m
                JOIN dm_merge map ON map.old_id=m.channel_id
                LEFT JOIN message_read_cursors rc
                  ON rc.channel_id=m.channel_id AND rc.user_id=p.user_id
                LEFT JOIN message_messages prev ON prev.id=rc.message_id
                WHERE map.keep_id=p.keep_id AND m.author_user_id<>p.user_id AND m.deleted_at IS NULL
                  AND (prev.id IS NULL OR (m.created_at,m.id)>(prev.created_at,prev.id))
                ORDER BY m.created_at,m.id LIMIT 1
            ) AS first_unread FROM people p
        )
        SELECT b.keep_id, b.user_id, (
            SELECT m.id FROM message_messages m JOIN dm_merge map ON map.old_id=m.channel_id
            LEFT JOIN message_messages first ON first.id=b.first_unread
            WHERE map.keep_id=b.keep_id
              AND (first.id IS NULL OR (m.created_at,m.id)<(first.created_at,first.id))
            ORDER BY m.created_at DESC,m.id DESC LIMIT 1
        ) AS message_id FROM boundaries b
    """)
    op.drop_constraint(
        op.f("fk_channel_memberships_workspace_id_user_id_workspace_memberships"),
        "channel_memberships",
        type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk_channel_memberships_user_id_identity_users"),
        "channel_memberships",
        "identity_users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.execute("""
        CREATE FUNCTION enforce_workspace_channel_membership() RETURNS trigger AS $$
        BEGIN
          IF EXISTS (SELECT 1 FROM channel_channels
                     WHERE id=NEW.channel_id AND direct_key IS NULL)
             AND NOT EXISTS (SELECT 1 FROM workspace_memberships
                             WHERE workspace_id=NEW.workspace_id AND user_id=NEW.user_id)
          THEN RAISE EXCEPTION 'workspace channel membership requires workspace membership'
               USING ERRCODE='23503'; END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER enforce_workspace_channel_membership
        BEFORE INSERT OR UPDATE ON channel_memberships
        FOR EACH ROW EXECUTE FUNCTION enforce_workspace_channel_membership()
    """)
    op.execute("""
        CREATE FUNCTION revoke_workspace_channels() RETURNS trigger AS $$
        BEGIN
          DELETE FROM channel_memberships cm USING channel_channels c
          WHERE cm.channel_id=c.id AND c.direct_key IS NULL
            AND cm.workspace_id=OLD.workspace_id AND cm.user_id=OLD.user_id;
          RETURN OLD;
        END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER revoke_workspace_channels AFTER DELETE ON workspace_memberships
        FOR EACH ROW EXECUTE FUNCTION revoke_workspace_channels()
    """)
    op.execute("""
        INSERT INTO channel_memberships(workspace_id,channel_id,user_id,added_by_user_id,joined_at)
        SELECT DISTINCT ON (map.keep_id,cm.user_id)
            map.keep_workspace,map.keep_id,cm.user_id,cm.added_by_user_id,cm.joined_at
        FROM channel_memberships cm JOIN dm_merge map ON map.old_id=cm.channel_id
        ORDER BY map.keep_id,cm.user_id,cm.joined_at
        ON CONFLICT (channel_id,user_id) DO NOTHING
    """)
    op.execute("DELETE FROM message_read_cursors WHERE channel_id IN (SELECT old_id FROM dm_merge)")
    op.drop_constraint(
        op.f("fk_message_messages_channel_id_reply_to_message_id_message_messages"),
        "message_messages",
        type_="foreignkey",
    )
    # Retry UUIDs are unique only within an old conversation. Keep all messages if
    # two independent histories reused one; retain the earliest retry identifier.
    op.execute("""
        WITH conflicts AS (
            SELECT m.id, row_number() OVER (
                PARTITION BY map.keep_id,m.author_user_id,m.client_message_id
                ORDER BY m.created_at,m.id
            ) AS position
            FROM message_messages m JOIN dm_merge map ON map.old_id=m.channel_id
            WHERE m.client_message_id IS NOT NULL
        ) UPDATE message_messages SET client_message_id=NULL
        WHERE id IN (SELECT id FROM conflicts WHERE position>1)
    """)
    for table in ("message_messages", "file_uploads", "calling_calls", "realtime_events"):
        op.execute(f"""
            UPDATE {table} row SET channel_id=map.keep_id,workspace_id=map.keep_workspace
            FROM dm_merge map WHERE row.channel_id=map.old_id AND map.old_id<>map.keep_id
        """)
    op.execute("""
        INSERT INTO message_read_cursors(channel_id,user_id,message_id)
        SELECT keep_id,user_id,message_id FROM dm_read_positions WHERE message_id IS NOT NULL
    """)
    op.create_foreign_key(
        op.f("fk_message_messages_channel_id_reply_to_message_id_message_messages"),
        "message_messages",
        "message_messages",
        ["channel_id", "reply_to_message_id"],
        ["channel_id", "id"],
    )
    op.execute(
        "INSERT INTO message_direct_aliases SELECT old_id,keep_id FROM dm_merge "
        "WHERE old_id<>keep_id"
    )
    op.execute(
        "DELETE FROM channel_channels WHERE id IN "
        "(SELECT old_id FROM dm_merge WHERE old_id<>keep_id)"
    )
    op.drop_constraint(
        op.f("uq_channel_channels_workspace_id_direct_key"), "channel_channels", type_="unique"
    )
    op.create_unique_constraint(
        op.f("uq_channel_channels_direct_key"), "channel_channels", ["direct_key"]
    )


def downgrade() -> None:
    # Combining histories is intentionally not reversible: splitting would discard
    # their shared chronology and messages written after the upgrade.
    raise RuntimeError(
        "Global DM consolidation cannot be downgraded. "
        "Restore a pre-upgrade database backup instead."
    )

"""Threads, reactions, read cursors, and private direct conversations.

Revision ID: 20260925_0007
Revises: 20260911_0006
"""

import sqlalchemy as sa
from alembic import op

revision = "20260925_0007"
down_revision = "20260911_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("channel_channels", sa.Column("direct_key", sa.String(65)))
    op.create_unique_constraint(
        op.f("uq_channel_channels_workspace_id_direct_key"),
        "channel_channels",
        ["workspace_id", "direct_key"],
    )
    op.create_check_constraint(
        op.f("ck_channel_channels_direct_is_private"),
        "channel_channels",
        "direct_key IS NULL OR visibility = 'private'",
    )
    op.add_column("message_messages", sa.Column("parent_message_id", sa.Uuid()))
    op.create_unique_constraint(
        op.f("uq_message_messages_channel_id_id"), "message_messages", ["channel_id", "id"]
    )
    op.create_foreign_key(
        op.f("fk_message_messages_channel_id_parent_message_id_message_messages"),
        "message_messages",
        "message_messages",
        ["channel_id", "parent_message_id"],
        ["channel_id", "id"],
    )
    op.create_index(
        "ix_message_messages_parent", "message_messages", ["parent_message_id", "created_at", "id"]
    )
    op.create_table(
        "message_reactions",
        sa.Column(
            "message_id",
            sa.Uuid(),
            sa.ForeignKey("message_messages.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("identity_users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("emoji", sa.Text(), primary_key=True),
    )
    op.create_table(
        "message_read_cursors",
        sa.Column("channel_id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id", "user_id"],
            ["channel_memberships.channel_id", "channel_memberships.user_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["channel_id", "message_id"], ["message_messages.channel_id", "message_messages.id"]
        ),
    )


def downgrade() -> None:
    op.drop_table("message_read_cursors")
    op.drop_table("message_reactions")
    op.drop_index("ix_message_messages_parent", table_name="message_messages")
    op.drop_constraint(
        op.f("fk_message_messages_channel_id_parent_message_id_message_messages"),
        "message_messages",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("uq_message_messages_channel_id_id"), "message_messages", type_="unique"
    )
    op.drop_column("message_messages", "parent_message_id")
    op.drop_constraint(
        op.f("ck_channel_channels_direct_is_private"), "channel_channels", type_="check"
    )
    op.drop_constraint(
        op.f("uq_channel_channels_workspace_id_direct_key"), "channel_channels", type_="unique"
    )
    op.drop_column("channel_channels", "direct_key")

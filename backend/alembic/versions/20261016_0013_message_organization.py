"""Mentions, shared pins, private saved messages, and alert delivery preferences.

Revision ID: 20261016_0013
Revises: 20261004_0012
"""

import sqlalchemy as sa
from alembic import op

revision = "20261016_0013"
down_revision = "20261004_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("message_mentions", "message_saved"):
        columns = [
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
        ]
        if name == "message_saved":
            columns.reverse()
            columns.append(sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
        op.create_table(name, *columns)
    op.create_index(
        "ix_message_mentions_user_id_message_id", "message_mentions", ["user_id", "message_id"]
    )
    op.create_table(
        "message_pins",
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
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "message_notification_preferences",
        sa.Column("channel_id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id", "user_id"],
            ["channel_memberships.channel_id", "channel_memberships.user_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("mode IN ('all', 'mentions', 'muted')", name="supported_mode"),
    )
    op.create_table(
        "message_notification_pauses",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("identity_users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("paused_until", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    for name in (
        "message_notification_pauses",
        "message_notification_preferences",
        "message_pins",
        "message_saved",
        "message_mentions",
    ):
        op.drop_table(name)

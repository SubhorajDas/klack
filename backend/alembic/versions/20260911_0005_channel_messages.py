"""Add durable channel messages.

Revision ID: 20260911_0005
Revises: 20260911_0004
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260911_0005"
down_revision: str | Sequence[str] | None = "20260911_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create durable channel-message state."""
    op.create_table(
        "message_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("author_user_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "((deleted_at IS NULL AND body IS NOT NULL "
            "AND char_length(body) BETWEEN 1 AND 4000 AND trim(body) <> '') "
            "OR (deleted_at IS NOT NULL AND body IS NULL))",
            name=op.f("ck_message_messages_live_body_or_deleted_tombstone"),
        ),
        sa.CheckConstraint(
            "edited_at IS NULL OR edited_at >= created_at",
            name=op.f("ck_message_messages_edit_not_before_creation"),
        ),
        sa.CheckConstraint(
            "deleted_at IS NULL OR deleted_at >= created_at",
            name=op.f("ck_message_messages_deletion_not_before_creation"),
        ),
        sa.ForeignKeyConstraint(
            ["author_user_id"],
            ["identity_users.id"],
            name=op.f("fk_message_messages_author_user_id_identity_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "channel_id"],
            ["channel_channels.workspace_id", "channel_channels.id"],
            name=op.f("fk_message_messages_workspace_id_channel_id_channel_channels"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message_messages")),
    )
    op.create_index(
        "ix_message_messages_channel_id_created_at_id",
        "message_messages",
        ["channel_id", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove channel-message state."""
    op.drop_index(
        "ix_message_messages_channel_id_created_at_id",
        table_name="message_messages",
    )
    op.drop_table("message_messages")

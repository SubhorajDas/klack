"""Convert existing thread relationships to inline quoted replies without copying messages.

Revision ID: 20261003_0010
Revises: 20260928_0009
"""

import sqlalchemy as sa
from alembic import op

revision = "20261003_0010"
down_revision = "20260928_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("fk_message_messages_channel_id_parent_message_id_message_messages"),
        "message_messages",
        type_="foreignkey",
    )
    op.drop_index("ix_message_messages_parent", table_name="message_messages")
    op.alter_column(
        "message_messages",
        "parent_message_id",
        new_column_name="reply_to_message_id",
        existing_type=sa.Uuid(),
    )
    op.create_foreign_key(
        op.f("fk_message_messages_channel_id_reply_to_message_id_message_messages"),
        "message_messages",
        "message_messages",
        ["channel_id", "reply_to_message_id"],
        ["channel_id", "id"],
    )
    op.create_index(
        "ix_message_messages_reply_to",
        "message_messages",
        ["reply_to_message_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_message_messages_channel_id_reply_to_message_id_message_messages"),
        "message_messages",
        type_="foreignkey",
    )
    op.drop_index("ix_message_messages_reply_to", table_name="message_messages")
    op.alter_column(
        "message_messages",
        "reply_to_message_id",
        new_column_name="parent_message_id",
        existing_type=sa.Uuid(),
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

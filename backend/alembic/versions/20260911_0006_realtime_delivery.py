"""Add idempotent message creation and committed realtime signals.

Revision ID: 20260911_0006
Revises: 20260911_0005
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260911_0006"
down_revision: str | Sequence[str] | None = "20260911_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add message revisions, client IDs, and the realtime event outbox."""
    op.add_column(
        "message_messages",
        sa.Column("client_message_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "message_messages",
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_message_messages_positive_revision"),
        "message_messages",
        "revision >= 1",
    )
    op.create_unique_constraint(
        op.f(
            "uq_message_messages_channel_id_author_user_id_client_message_id",
        ),
        "message_messages",
        ["channel_id", "author_user_id", "client_message_id"],
    )
    op.alter_column("message_messages", "revision", server_default=None)

    op.create_table(
        "realtime_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("entity_revision", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "entity_revision >= 1",
            name=op.f("ck_realtime_events_positive_entity_revision"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_realtime_events")),
    )
    op.create_index(
        "ix_realtime_events_occurred_at",
        "realtime_events",
        ["occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove realtime signaling and message retry metadata."""
    op.drop_index("ix_realtime_events_occurred_at", table_name="realtime_events")
    op.drop_table("realtime_events")
    op.drop_constraint(
        op.f("uq_message_messages_channel_id_author_user_id_client_message_id"),
        "message_messages",
        type_="unique",
    )
    op.drop_constraint(
        op.f("ck_message_messages_positive_revision"),
        "message_messages",
        type_="check",
    )
    op.drop_column("message_messages", "revision")
    op.drop_column("message_messages", "client_message_id")

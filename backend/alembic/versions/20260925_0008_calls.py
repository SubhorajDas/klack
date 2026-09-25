"""Durable one-to-one calls.

Revision ID: 20260925_0008
Revises: 20260925_0007
"""

import sqlalchemy as sa
from alembic import op

revision = "20260925_0008"
down_revision = "20260925_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "calling_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("caller_id", sa.Uuid(), sa.ForeignKey("identity_users.id"), nullable=False),
        sa.Column("callee_id", sa.Uuid(), sa.ForeignKey("identity_users.id"), nullable=False),
        sa.Column("caller_session", sa.Uuid(), nullable=False),
        sa.Column("callee_session", sa.Uuid()),
        sa.Column("caller_device", sa.Uuid(), nullable=False),
        sa.Column("callee_device", sa.Uuid()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("caller_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("callee_seen", sa.DateTime(timezone=True)),
        sa.Column("cleanup_after", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["workspace_id", "channel_id"],
            ["channel_channels.workspace_id", "channel_channels.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("caller_id <> callee_id", name="different_participants"),
        sa.CheckConstraint(
            "status IN ('ringing','active','declined','missed','cancelled','ended')",
            name="supported_status",
        ),
    )
    op.create_index(
        "ix_calling_calls_channel_created", "calling_calls", ["channel_id", "created_at"]
    )
    op.create_index("ix_calling_calls_status", "calling_calls", ["status"])
    op.create_index("ix_calling_calls_cleanup_after", "calling_calls", ["cleanup_after"])
    op.create_table(
        "calling_seats",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("identity_users.id"), primary_key=True),
        sa.Column(
            "call_id",
            sa.Uuid(),
            sa.ForeignKey("calling_calls.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index("ix_calling_seats_call_id", "calling_seats", ["call_id"])


def downgrade() -> None:
    op.drop_table("calling_seats")
    op.drop_table("calling_calls")

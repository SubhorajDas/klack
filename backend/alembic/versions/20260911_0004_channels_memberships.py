"""Add channels and channel memberships.

Revision ID: 20260911_0004
Revises: 20260909_0003
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260911_0004"
down_revision: str | Sequence[str] | None = "20260909_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create channel and channel-membership state."""
    op.create_table(
        "channel_channels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_by_user_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "name = trim(name) AND char_length(name) BETWEEN 1 AND 80",
            name=op.f("ck_channel_channels_name_trimmed_nonblank"),
        ),
        sa.CheckConstraint(
            "name = lower(name)",
            name=op.f("ck_channel_channels_name_lowercase"),
        ),
        sa.CheckConstraint(
            "visibility IN ('public', 'private')",
            name=op.f("ck_channel_channels_supported_visibility"),
        ),
        sa.CheckConstraint(
            "(archived_at IS NULL) = (archived_by_user_id IS NULL)",
            name=op.f("ck_channel_channels_archival_timestamp_actor_pair"),
        ),
        sa.ForeignKeyConstraint(
            ["archived_by_user_id"],
            ["identity_users.id"],
            name=op.f(
                "fk_channel_channels_archived_by_user_id_identity_users",
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["identity_users.id"],
            name=op.f(
                "fk_channel_channels_created_by_user_id_identity_users",
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace_workspaces.id"],
            name=op.f(
                "fk_channel_channels_workspace_id_workspace_workspaces",
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_channel_channels")),
        sa.UniqueConstraint(
            "workspace_id",
            "name",
            name=op.f("uq_channel_channels_workspace_id_name"),
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name=op.f("uq_channel_channels_workspace_id_id"),
        ),
    )
    op.create_index(
        "ix_channel_channels_workspace_id_archived_at_name",
        "channel_channels",
        ["workspace_id", "archived_at", "name"],
        unique=False,
    )

    op.create_table(
        "channel_memberships",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("added_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["added_by_user_id"],
            ["identity_users.id"],
            name=op.f(
                "fk_channel_memberships_added_by_user_id_identity_users",
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "channel_id"],
            ["channel_channels.workspace_id", "channel_channels.id"],
            name=op.f(
                "fk_channel_memberships_workspace_id_channel_id_channel_channels",
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name=op.f(
                "fk_channel_memberships_workspace_id_user_id_workspace_memberships",
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "channel_id",
            "user_id",
            name=op.f("pk_channel_memberships"),
        ),
    )
    op.create_index(
        "ix_channel_memberships_workspace_id_user_id_channel_id",
        "channel_memberships",
        ["workspace_id", "user_id", "channel_id"],
        unique=False,
    )
    op.create_index(
        "ix_channel_memberships_channel_id_joined_at_user_id",
        "channel_memberships",
        ["channel_id", "joined_at", "user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove channel state in reverse dependency order."""
    op.drop_index(
        "ix_channel_memberships_channel_id_joined_at_user_id",
        table_name="channel_memberships",
    )
    op.drop_index(
        "ix_channel_memberships_workspace_id_user_id_channel_id",
        table_name="channel_memberships",
    )
    op.drop_table("channel_memberships")
    op.drop_index(
        "ix_channel_channels_workspace_id_archived_at_name",
        table_name="channel_channels",
    )
    op.drop_table("channel_channels")

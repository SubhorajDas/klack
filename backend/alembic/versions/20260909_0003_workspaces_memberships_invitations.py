"""Add workspaces, memberships, and bearer invitations.

Revision ID: 20260909_0003
Revises: 20260824_0002
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_0003"
down_revision: str | Sequence[str] | None = "20260824_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create workspace, membership, and bearer-invitation state."""
    op.create_table(
        "workspace_workspaces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "name = trim(name) AND char_length(name) BETWEEN 1 AND 100",
            name=op.f("ck_workspace_workspaces_name_trimmed_nonblank"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["identity_users.id"],
            name=op.f(
                "fk_workspace_workspaces_created_by_user_id_identity_users",
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_workspaces")),
    )

    op.create_table(
        "workspace_memberships",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'member')",
            name=op.f("ck_workspace_memberships_supported_role"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["identity_users.id"],
            name=op.f("fk_workspace_memberships_user_id_identity_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace_workspaces.id"],
            name=op.f(
                "fk_workspace_memberships_workspace_id_workspace_workspaces",
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "workspace_id",
            "user_id",
            name=op.f("pk_workspace_memberships"),
        ),
    )
    op.create_index(
        "ix_workspace_memberships_user_id_workspace_id",
        "workspace_memberships",
        ["user_id", "workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_workspace_memberships_workspace_id_role",
        "workspace_memberships",
        ["workspace_id", "role"],
        unique=False,
    )

    op.create_table(
        "workspace_invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_workspace_invitations_expiry_after_creation"),
        ),
        sa.CheckConstraint(
            "accepted_at IS NULL OR revoked_at IS NULL",
            name=op.f("ck_workspace_invitations_not_accepted_and_revoked"),
        ),
        sa.CheckConstraint(
            "(accepted_at IS NULL) = (accepted_by_user_id IS NULL)",
            name=op.f(
                "ck_workspace_invitations_acceptance_timestamp_actor_pair",
            ),
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL) = (revoked_by_user_id IS NULL)",
            name=op.f(
                "ck_workspace_invitations_revocation_timestamp_actor_pair",
            ),
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_user_id"],
            ["identity_users.id"],
            name=op.f(
                "fk_workspace_invitations_accepted_by_user_id_identity_users",
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["identity_users.id"],
            name=op.f(
                "fk_workspace_invitations_created_by_user_id_identity_users",
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"],
            ["identity_users.id"],
            name=op.f(
                "fk_workspace_invitations_revoked_by_user_id_identity_users",
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace_workspaces.id"],
            name=op.f(
                "fk_workspace_invitations_workspace_id_workspace_workspaces",
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_invitations")),
        sa.UniqueConstraint(
            "token_hash",
            name=op.f("uq_workspace_invitations_token_hash"),
        ),
    )
    op.create_index(
        "ix_workspace_invitations_workspace_id_created_at",
        "workspace_invitations",
        ["workspace_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_workspace_invitations_expires_at",
        "workspace_invitations",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove workspace state in reverse dependency order."""
    op.drop_index(
        "ix_workspace_invitations_expires_at",
        table_name="workspace_invitations",
    )
    op.drop_index(
        "ix_workspace_invitations_workspace_id_created_at",
        table_name="workspace_invitations",
    )
    op.drop_table("workspace_invitations")
    op.drop_index(
        "ix_workspace_memberships_workspace_id_role",
        table_name="workspace_memberships",
    )
    op.drop_index(
        "ix_workspace_memberships_user_id_workspace_id",
        table_name="workspace_memberships",
    )
    op.drop_table("workspace_memberships")
    op.drop_table("workspace_workspaces")

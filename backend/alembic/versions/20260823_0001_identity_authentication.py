"""Add identity, password credential, session, and refresh-token tables.

Revision ID: 20260823_0001
Revises:
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "20260823_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the durable identity and rotating-session schema."""
    op.create_table(
        "identity_users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "char_length(email) BETWEEN 3 AND 320",
            name=op.f("ck_identity_users_email_length"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_identity_users")),
        sa.UniqueConstraint("email", name=op.f("uq_identity_users_email")),
    )
    op.create_table(
        "identity_password_credentials",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["identity_users.id"],
            name=op.f("fk_identity_password_credentials_user_id_identity_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "user_id",
            name=op.f("pk_identity_password_credentials"),
        ),
    )
    op.create_table(
        "identity_auth_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.String(length=32), nullable=True),
        sa.Column("created_ip", sa.String(length=45), nullable=True),
        sa.Column("last_ip", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_identity_auth_sessions_expiry_after_creation"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["identity_users.id"],
            name=op.f("fk_identity_auth_sessions_user_id_identity_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_identity_auth_sessions")),
    )
    op.create_index(
        op.f("ix_identity_auth_sessions_expires_at"),
        "identity_auth_sessions",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_identity_auth_sessions_user_id"),
        "identity_auth_sessions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_identity_auth_sessions_user_id_expires_at",
        "identity_auth_sessions",
        ["user_id", "expires_at"],
        unique=False,
    )
    op.create_table(
        "identity_refresh_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_token_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_identity_refresh_tokens_expiry_after_creation"),
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_token_id"],
            ["identity_refresh_tokens.id"],
            name=op.f(
                "fk_identity_refresh_tokens_replaced_by_token_id_identity_refresh_tokens",
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["identity_auth_sessions.id"],
            name=op.f("fk_identity_refresh_tokens_session_id_identity_auth_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_identity_refresh_tokens")),
        sa.UniqueConstraint(
            "token_hash",
            name=op.f("uq_identity_refresh_tokens_token_hash"),
        ),
    )
    op.create_index(
        op.f("ix_identity_refresh_tokens_expires_at"),
        "identity_refresh_tokens",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_identity_refresh_tokens_session_id"),
        "identity_refresh_tokens",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        "ix_identity_refresh_tokens_session_id_created_at",
        "identity_refresh_tokens",
        ["session_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove the identity slice in reverse dependency order."""
    op.drop_index(
        "ix_identity_refresh_tokens_session_id_created_at",
        table_name="identity_refresh_tokens",
    )
    op.drop_index(
        op.f("ix_identity_refresh_tokens_session_id"),
        table_name="identity_refresh_tokens",
    )
    op.drop_index(
        op.f("ix_identity_refresh_tokens_expires_at"),
        table_name="identity_refresh_tokens",
    )
    op.drop_table("identity_refresh_tokens")
    op.drop_index(
        "ix_identity_auth_sessions_user_id_expires_at",
        table_name="identity_auth_sessions",
    )
    op.drop_index(
        op.f("ix_identity_auth_sessions_user_id"),
        table_name="identity_auth_sessions",
    )
    op.drop_index(
        op.f("ix_identity_auth_sessions_expires_at"),
        table_name="identity_auth_sessions",
    )
    op.drop_table("identity_auth_sessions")
    op.drop_table("identity_password_credentials")
    op.drop_table("identity_users")

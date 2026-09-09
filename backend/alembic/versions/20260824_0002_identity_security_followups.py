"""Add verification, recovery, shared throttling, and email outbox state.

Revision ID: 20260824_0002
Revises: 20260823_0001
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_0002"
down_revision: str | Sequence[str] | None = "20260823_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create account-action, shared-throttle, and encrypted-outbox tables."""
    op.create_table(
        "identity_email_action_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "purpose IN ('verify_email', 'recover_password')",
            name=op.f("ck_identity_email_action_tokens_supported_purpose"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_identity_email_action_tokens_expiry_after_creation"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["identity_users.id"],
            name=op.f("fk_identity_email_action_tokens_user_id_identity_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_identity_email_action_tokens")),
        sa.UniqueConstraint(
            "token_hash",
            name=op.f("uq_identity_email_action_tokens_token_hash"),
        ),
    )
    op.create_index(
        op.f("ix_identity_email_action_tokens_expires_at"),
        "identity_email_action_tokens",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_identity_email_action_tokens_user_id"),
        "identity_email_action_tokens",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_identity_email_action_tokens_user_purpose_created",
        "identity_email_action_tokens",
        ["user_id", "purpose", "created_at"],
        unique=False,
    )

    op.create_table(
        "identity_auth_rate_limits",
        sa.Column("scope", sa.String(length=48), nullable=False),
        sa.Column("subject_hash", sa.String(length=64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "attempts >= 0",
            name=op.f("ck_identity_auth_rate_limits_attempts_non_negative"),
        ),
        sa.PrimaryKeyConstraint(
            "scope",
            "subject_hash",
            name=op.f("pk_identity_auth_rate_limits"),
        ),
    )
    op.create_index(
        op.f("ix_identity_auth_rate_limits_updated_at"),
        "identity_auth_rate_limits",
        ["updated_at"],
        unique=False,
    )

    op.create_table(
        "identity_email_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("action_token_id", sa.Uuid(), nullable=False),
        sa.Column("recipient", sa.String(length=320), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_id", sa.Uuid(), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_code", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "purpose IN ('verify_email', 'recover_password')",
            name=op.f("ck_identity_email_outbox_supported_purpose"),
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name=op.f("ck_identity_email_outbox_attempts_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["action_token_id"],
            ["identity_email_action_tokens.id"],
            name=op.f(
                "fk_identity_email_outbox_action_token_id_identity_email_action_tokens",
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_identity_email_outbox")),
        sa.UniqueConstraint(
            "action_token_id",
            name=op.f("uq_identity_email_outbox_action_token_id"),
        ),
    )
    op.create_index(
        "ix_identity_email_outbox_delivery",
        "identity_email_outbox",
        ["sent_at", "available_at", "leased_until"],
        unique=False,
    )


def downgrade() -> None:
    """Remove follow-up identity security state in reverse dependency order."""
    op.drop_index("ix_identity_email_outbox_delivery", table_name="identity_email_outbox")
    op.drop_table("identity_email_outbox")
    op.drop_index(
        op.f("ix_identity_auth_rate_limits_updated_at"),
        table_name="identity_auth_rate_limits",
    )
    op.drop_table("identity_auth_rate_limits")
    op.drop_index(
        "ix_identity_email_action_tokens_user_purpose_created",
        table_name="identity_email_action_tokens",
    )
    op.drop_index(
        op.f("ix_identity_email_action_tokens_user_id"),
        table_name="identity_email_action_tokens",
    )
    op.drop_index(
        op.f("ix_identity_email_action_tokens_expires_at"),
        table_name="identity_email_action_tokens",
    )
    op.drop_table("identity_email_action_tokens")

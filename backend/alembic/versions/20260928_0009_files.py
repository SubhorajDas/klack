"""Private attachments and file-only messages.

Revision ID: 20260928_0009
Revises: 20260925_0008
"""

import sqlalchemy as sa
from alembic import op

revision = "20260928_0009"
down_revision = "20260925_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "message_messages",
        sa.Column("attachment_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.drop_constraint("ck_message_messages_live_body_or_deleted_tombstone", "message_messages")
    op.create_check_constraint(
        "live_body_or_deleted_tombstone",
        "message_messages",
        "((deleted_at IS NULL AND body IS NOT NULL AND char_length(body) <= 4000 "
        "AND (trim(body) <> '' OR attachment_count > 0)) OR "
        "(deleted_at IS NOT NULL AND body IS NULL AND attachment_count = 0))",
    )
    op.create_check_constraint(
        "attachment_count_range", "message_messages", "attachment_count BETWEEN 0 AND 5"
    )
    op.create_table(
        "file_uploads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspace_workspaces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "channel_id",
            sa.Uuid(),
            sa.ForeignKey("channel_channels.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "uploader_id",
            sa.Uuid(),
            sa.ForeignKey("identity_users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "message_id", sa.Uuid(), sa.ForeignKey("message_messages.id", ondelete="RESTRICT")
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), unique=True, nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("size > 0", name="positive_size"),
        sa.CheckConstraint(
            "status IN ('pending','uploading','ready','attached','deleting','removed')",
            name="valid_status",
        ),
    )
    op.create_index(
        "ix_file_uploads_channel_created", "file_uploads", ["channel_id", "created_at", "id"]
    )
    op.create_index("ix_file_uploads_cleanup", "file_uploads", ["status", "expires_at"])
    op.create_index("ix_file_uploads_message_id", "file_uploads", ["message_id"])
    op.create_index("ix_file_uploads_workspace_id", "file_uploads", ["workspace_id"])


def downgrade() -> None:
    # Downgrade must not silently erase attachments or invent text for file-only messages.
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT count(*) FROM file_uploads WHERE status != 'removed'")):
        raise RuntimeError("Remove files and run file cleanup before downgrading")
    if connection.scalar(
        sa.text("SELECT count(*) FROM message_messages WHERE attachment_count > 0")
    ):
        raise RuntimeError("Delete attachment messages before downgrading")
    op.drop_table("file_uploads")
    op.drop_constraint("ck_message_messages_live_body_or_deleted_tombstone", "message_messages")
    op.drop_constraint("ck_message_messages_attachment_count_range", "message_messages")
    op.drop_column("message_messages", "attachment_count")
    op.create_check_constraint(
        "live_body_or_deleted_tombstone",
        "message_messages",
        "((deleted_at IS NULL AND body IS NOT NULL AND char_length(body) BETWEEN 1 AND 4000 "
        "AND trim(body) <> '') OR (deleted_at IS NOT NULL AND body IS NULL))",
    )

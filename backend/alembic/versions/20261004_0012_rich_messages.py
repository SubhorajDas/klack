"""Preserve rich message structure and inline attachment positions.

Revision ID: 20261004_0012
Revises: 20261004_0011
"""

import sqlalchemy as sa
from alembic import op

revision = "20261004_0012"
down_revision = "20261004_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("message_messages", sa.Column("document", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("message_messages", "document")

"""SQLAlchemy persistence records owned by the channel module."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from klack.core.db.base import Base


class ChannelRecord(Base):
    """A public or private conversation space within a workspace."""

    __tablename__ = "channel_channels"
    __table_args__ = (
        CheckConstraint(
            "name = trim(name) AND char_length(name) BETWEEN 1 AND 80",
            name="name_trimmed_nonblank",
        ),
        CheckConstraint("name = lower(name)", name="name_lowercase"),
        CheckConstraint(
            "visibility IN ('public', 'private')",
            name="supported_visibility",
        ),
        CheckConstraint(
            "(archived_at IS NULL) = (archived_by_user_id IS NULL)",
            name="archival_timestamp_actor_pair",
        ),
        UniqueConstraint("workspace_id", "name"),
        UniqueConstraint("workspace_id", "id"),
        Index(
            "ix_channel_channels_workspace_id_archived_at_name",
            "workspace_id",
            "archived_at",
            "name",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("workspace_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("identity_users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("identity_users.id", ondelete="RESTRICT"),
    )


class ChannelMembershipRecord(Base):
    """An identity's explicit membership in a workspace channel."""

    __tablename__ = "channel_memberships"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "channel_id"],
            ["channel_channels.workspace_id", "channel_channels.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            ondelete="CASCADE",
        ),
        Index(
            "ix_channel_memberships_workspace_id_user_id_channel_id",
            "workspace_id",
            "user_id",
            "channel_id",
        ),
        Index(
            "ix_channel_memberships_channel_id_joined_at_user_id",
            "channel_id",
            "joined_at",
            "user_id",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    channel_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    added_by_user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("identity_users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

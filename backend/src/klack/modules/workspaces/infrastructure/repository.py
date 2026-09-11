"""Async SQLAlchemy implementation of workspace persistence."""

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from klack.modules.workspaces.application.ports import WorkspaceConflict
from klack.modules.workspaces.domain.entities import (
    Workspace,
    WorkspaceInvitation,
    WorkspaceMembership,
    WorkspaceRole,
)
from klack.modules.workspaces.infrastructure.models import (
    InvitationRecord,
    MembershipRecord,
    WorkspaceRecord,
)


class SqlAlchemyWorkspaceRepository:
    """Persist workspace state in the request-scoped transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_workspace(
        self,
        workspace: Workspace,
        owner_membership: WorkspaceMembership,
    ) -> None:
        self._session.add(self._workspace_record(workspace))
        await self._flush()
        self._session.add(self._membership_record(owner_membership))

    async def list_workspaces(self, *, user_id: UUID) -> list[Workspace]:
        records = (
            await self._session.scalars(
                select(WorkspaceRecord)
                .join(
                    MembershipRecord,
                    MembershipRecord.workspace_id == WorkspaceRecord.id,
                )
                .where(MembershipRecord.user_id == user_id)
                .order_by(WorkspaceRecord.created_at, WorkspaceRecord.id),
            )
        ).all()
        return [self._workspace(record) for record in records]

    async def get_workspace(
        self,
        workspace_id: UUID,
        *,
        for_update: bool = False,
    ) -> Workspace | None:
        statement = select(WorkspaceRecord).where(WorkspaceRecord.id == workspace_id)
        if for_update:
            statement = statement.with_for_update()
        record = await self._session.scalar(statement)
        return None if record is None else self._workspace(record)

    async def update_workspace_name(
        self,
        *,
        workspace_id: UUID,
        name: str,
        updated_at: datetime,
    ) -> None:
        await self._session.execute(
            update(WorkspaceRecord)
            .where(WorkspaceRecord.id == workspace_id)
            .values(name=name, updated_at=updated_at),
        )

    async def get_membership(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        for_update: bool = False,
    ) -> WorkspaceMembership | None:
        statement = select(MembershipRecord).where(
            MembershipRecord.workspace_id == workspace_id,
            MembershipRecord.user_id == user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        record = await self._session.scalar(statement)
        return None if record is None else self._membership(record)

    async def list_memberships(
        self,
        *,
        workspace_id: UUID,
    ) -> list[WorkspaceMembership]:
        records = (
            await self._session.scalars(
                select(MembershipRecord)
                .where(MembershipRecord.workspace_id == workspace_id)
                .order_by(MembershipRecord.joined_at, MembershipRecord.user_id),
            )
        ).all()
        return [self._membership(record) for record in records]

    async def count_memberships_by_role(
        self,
        *,
        workspace_id: UUID,
        role: WorkspaceRole,
    ) -> int:
        count = await self._session.scalar(
            select(func.count())
            .select_from(MembershipRecord)
            .where(
                MembershipRecord.workspace_id == workspace_id,
                MembershipRecord.role == str(role),
            ),
        )
        return 0 if count is None else count

    async def update_membership_role(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        role: WorkspaceRole,
    ) -> None:
        await self._session.execute(
            update(MembershipRecord)
            .where(
                MembershipRecord.workspace_id == workspace_id,
                MembershipRecord.user_id == user_id,
            )
            .values(role=str(role)),
        )

    async def remove_membership(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
    ) -> None:
        await self._session.execute(
            delete(MembershipRecord).where(
                MembershipRecord.workspace_id == workspace_id,
                MembershipRecord.user_id == user_id,
            ),
        )

    async def add_invitation(self, invitation: WorkspaceInvitation) -> None:
        self._session.add(self._invitation_record(invitation))

    async def list_invitations(
        self,
        *,
        workspace_id: UUID,
    ) -> list[WorkspaceInvitation]:
        records = (
            await self._session.scalars(
                select(InvitationRecord)
                .where(InvitationRecord.workspace_id == workspace_id)
                .order_by(InvitationRecord.created_at.desc(), InvitationRecord.id),
            )
        ).all()
        return [self._invitation(record) for record in records]

    async def get_invitation_workspace_id(self, invitation_id: UUID) -> UUID | None:
        return cast(
            UUID | None,
            await self._session.scalar(
                select(InvitationRecord.workspace_id).where(
                    InvitationRecord.id == invitation_id,
                ),
            ),
        )

    async def get_invitation(
        self,
        *,
        workspace_id: UUID,
        invitation_id: UUID,
        for_update: bool = False,
    ) -> WorkspaceInvitation | None:
        statement = select(InvitationRecord).where(
            InvitationRecord.workspace_id == workspace_id,
            InvitationRecord.id == invitation_id,
        )
        if for_update:
            statement = statement.with_for_update()
        record = await self._session.scalar(statement)
        return None if record is None else self._invitation(record)

    async def revoke_invitation(
        self,
        *,
        invitation_id: UUID,
        revoked_at: datetime,
        revoked_by_user_id: UUID,
    ) -> None:
        await self._session.execute(
            update(InvitationRecord)
            .where(InvitationRecord.id == invitation_id)
            .values(
                revoked_at=revoked_at,
                revoked_by_user_id=revoked_by_user_id,
            ),
        )

    async def accept_invitation(
        self,
        *,
        invitation_id: UUID,
        membership: WorkspaceMembership,
        accepted_at: datetime,
    ) -> None:
        self._session.add(self._membership_record(membership))
        await self._flush()
        await self._session.execute(
            update(InvitationRecord)
            .where(InvitationRecord.id == invitation_id)
            .values(
                accepted_at=accepted_at,
                accepted_by_user_id=membership.user_id,
            ),
        )

    async def _flush(self) -> None:
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise WorkspaceConflict from exc

    async def commit(self) -> None:
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise WorkspaceConflict from exc

    async def rollback(self) -> None:
        await self._session.rollback()

    @staticmethod
    def _workspace(record: WorkspaceRecord) -> Workspace:
        return Workspace(
            id=record.id,
            name=record.name,
            created_by_user_id=record.created_by_user_id,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _membership(record: MembershipRecord) -> WorkspaceMembership:
        return WorkspaceMembership(
            workspace_id=record.workspace_id,
            user_id=record.user_id,
            role=WorkspaceRole(record.role),
            joined_at=record.joined_at,
        )

    @staticmethod
    def _invitation(record: InvitationRecord) -> WorkspaceInvitation:
        return WorkspaceInvitation(
            id=record.id,
            workspace_id=record.workspace_id,
            created_by_user_id=record.created_by_user_id,
            token_hash=record.token_hash,
            created_at=record.created_at,
            expires_at=record.expires_at,
            accepted_at=record.accepted_at,
            accepted_by_user_id=record.accepted_by_user_id,
            revoked_at=record.revoked_at,
            revoked_by_user_id=record.revoked_by_user_id,
        )

    @staticmethod
    def _workspace_record(workspace: Workspace) -> WorkspaceRecord:
        return WorkspaceRecord(
            id=workspace.id,
            name=workspace.name,
            created_by_user_id=workspace.created_by_user_id,
            created_at=workspace.created_at,
            updated_at=workspace.updated_at,
        )

    @staticmethod
    def _membership_record(membership: WorkspaceMembership) -> MembershipRecord:
        return MembershipRecord(
            workspace_id=membership.workspace_id,
            user_id=membership.user_id,
            role=str(membership.role),
            joined_at=membership.joined_at,
        )

    @staticmethod
    def _invitation_record(invitation: WorkspaceInvitation) -> InvitationRecord:
        return InvitationRecord(
            id=invitation.id,
            workspace_id=invitation.workspace_id,
            created_by_user_id=invitation.created_by_user_id,
            token_hash=invitation.token_hash,
            created_at=invitation.created_at,
            expires_at=invitation.expires_at,
            accepted_at=invitation.accepted_at,
            accepted_by_user_id=invitation.accepted_by_user_id,
            revoked_at=invitation.revoked_at,
            revoked_by_user_id=invitation.revoked_by_user_id,
        )

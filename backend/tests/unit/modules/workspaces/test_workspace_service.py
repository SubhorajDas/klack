"""Workspace application authorization and transaction contracts."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from klack.modules.workspaces.application.ports import (
    IssuedInvitationToken,
    PresentedInvitationToken,
    WorkspaceConflict,
)
from klack.modules.workspaces.application.service import (
    WorkspacePolicy,
    WorkspaceService,
    utc_now,
)
from klack.modules.workspaces.domain.entities import (
    Workspace,
    WorkspaceInvitation,
    WorkspaceMembership,
    WorkspaceRole,
)
from klack.modules.workspaces.domain.errors import (
    InvalidInvitationToken,
    InvalidWorkspaceName,
    InvitationNotActive,
    InvitationNotFound,
    MembershipAlreadyExists,
    MembershipNotFound,
    OwnerInvariantViolation,
    WorkspaceNotFound,
    WorkspacePermissionDenied,
)

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
ACTOR_ID = UUID(int=1)
OTHER_OWNER_ID = UUID(int=2)
TARGET_ID = UUID(int=3)
OUTSIDER_ID = UUID(int=4)
WORKSPACE_ID = UUID(int=101)
NEW_WORKSPACE_ID = UUID(int=102)
INVITATION_ID = UUID(int=201)
UNKNOWN_INVITATION_ID = UUID(int=202)


class MutableClock:
    """Deterministic service clock whose value a test may advance."""

    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class FakeInvitationTokenManager:
    """Typed deterministic token fake implementing the application token port."""

    def __init__(self) -> None:
        self._next_token_int = 1_000
        self.presentations: dict[str, PresentedInvitationToken] = {}
        self.issue_count = 0
        self.presented_values: list[str | None] = []
        self.match_calls: list[tuple[str, str]] = []

    def issue(self) -> IssuedInvitationToken:
        token_id = UUID(int=self._next_token_int)
        self._next_token_int += 1
        raw = f"{token_id}.test-secret"
        digest = f"digest:{token_id}"
        self.presentations[raw] = PresentedInvitationToken(
            token_id=token_id,
            digest=digest,
        )
        self.issue_count += 1
        return IssuedInvitationToken(token_id=token_id, raw=raw, digest=digest)

    def register(
        self,
        *,
        token_id: UUID,
        digest: str = "presented-digest",
    ) -> str:
        """Register a known presentation for an invitation seeded by a test."""
        raw = f"{token_id}.registered-secret"
        self.presentations[raw] = PresentedInvitationToken(
            token_id=token_id,
            digest=digest,
        )
        return raw

    def present(self, raw_token: str | None) -> PresentedInvitationToken:
        self.presented_values.append(raw_token)
        if raw_token is None or raw_token not in self.presentations:
            raise InvalidInvitationToken
        return self.presentations[raw_token]

    def matches(self, presented_digest: str, stored_digest: str) -> bool:
        self.match_calls.append((presented_digest, stored_digest))
        return presented_digest == stored_digest


class MemoryWorkspaceRepository:
    """Transaction-observable in-memory fake implementing the repository port."""

    def __init__(self) -> None:
        self.workspaces: dict[UUID, Workspace] = {}
        self.memberships: dict[tuple[UUID, UUID], WorkspaceMembership] = {}
        self.invitations: dict[UUID, WorkspaceInvitation] = {}
        self.invitation_workspace_ids: dict[UUID, UUID] = {}
        self.workspace_gets: list[tuple[UUID, bool]] = []
        self.membership_gets: list[tuple[UUID, UUID, bool]] = []
        self.invitation_gets: list[tuple[UUID, UUID, bool]] = []
        self.role_counts: list[tuple[UUID, WorkspaceRole]] = []
        self.commit_count = 0
        self.commit_attempt_count = 0
        self.rollback_count = 0
        self.conflict_operation: str | None = None

    def _maybe_conflict(self, operation: str) -> None:
        if self.conflict_operation == operation:
            raise WorkspaceConflict

    async def add_workspace(
        self,
        workspace: Workspace,
        owner_membership: WorkspaceMembership,
    ) -> None:
        self._maybe_conflict("add_workspace")
        self.workspaces[workspace.id] = workspace
        self.memberships[(workspace.id, owner_membership.user_id)] = owner_membership

    async def list_workspaces(self, *, user_id: UUID) -> list[Workspace]:
        workspace_ids = {
            workspace_id
            for workspace_id, member_user_id in self.memberships
            if member_user_id == user_id
        }
        return [
            workspace
            for workspace_id, workspace in self.workspaces.items()
            if workspace_id in workspace_ids
        ]

    async def get_workspace(
        self,
        workspace_id: UUID,
        *,
        for_update: bool = False,
    ) -> Workspace | None:
        self.workspace_gets.append((workspace_id, for_update))
        return self.workspaces.get(workspace_id)

    async def update_workspace_name(
        self,
        *,
        workspace_id: UUID,
        name: str,
        updated_at: datetime,
    ) -> None:
        current = self.workspaces[workspace_id]
        self.workspaces[workspace_id] = replace(
            current,
            name=name,
            updated_at=updated_at,
        )

    async def get_membership(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        for_update: bool = False,
    ) -> WorkspaceMembership | None:
        self.membership_gets.append((workspace_id, user_id, for_update))
        return self.memberships.get((workspace_id, user_id))

    async def list_memberships(
        self,
        *,
        workspace_id: UUID,
    ) -> list[WorkspaceMembership]:
        return [
            membership
            for (member_workspace_id, _user_id), membership in self.memberships.items()
            if member_workspace_id == workspace_id
        ]

    async def count_memberships_by_role(
        self,
        *,
        workspace_id: UUID,
        role: WorkspaceRole,
    ) -> int:
        self.role_counts.append((workspace_id, role))
        return sum(
            membership.workspace_id == workspace_id and membership.role is role
            for membership in self.memberships.values()
        )

    async def update_membership_role(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        role: WorkspaceRole,
    ) -> None:
        membership = self.memberships[(workspace_id, user_id)]
        self.memberships[(workspace_id, user_id)] = replace(membership, role=role)

    async def remove_membership(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
    ) -> None:
        del self.memberships[(workspace_id, user_id)]

    async def add_invitation(self, invitation: WorkspaceInvitation) -> None:
        self._maybe_conflict("add_invitation")
        self.invitations[invitation.id] = invitation
        self.invitation_workspace_ids[invitation.id] = invitation.workspace_id

    async def list_invitations(
        self,
        *,
        workspace_id: UUID,
    ) -> list[WorkspaceInvitation]:
        return [
            invitation
            for invitation in self.invitations.values()
            if invitation.workspace_id == workspace_id
        ]

    async def get_invitation_workspace_id(self, invitation_id: UUID) -> UUID | None:
        return self.invitation_workspace_ids.get(invitation_id)

    async def get_invitation(
        self,
        *,
        workspace_id: UUID,
        invitation_id: UUID,
        for_update: bool = False,
    ) -> WorkspaceInvitation | None:
        self.invitation_gets.append((workspace_id, invitation_id, for_update))
        invitation = self.invitations.get(invitation_id)
        if invitation is None or invitation.workspace_id != workspace_id:
            return None
        return invitation

    async def revoke_invitation(
        self,
        *,
        invitation_id: UUID,
        revoked_at: datetime,
        revoked_by_user_id: UUID,
    ) -> None:
        invitation = self.invitations[invitation_id]
        self.invitations[invitation_id] = replace(
            invitation,
            revoked_at=revoked_at,
            revoked_by_user_id=revoked_by_user_id,
        )

    async def accept_invitation(
        self,
        *,
        invitation_id: UUID,
        membership: WorkspaceMembership,
        accepted_at: datetime,
    ) -> None:
        self._maybe_conflict("accept_invitation")
        invitation = self.invitations[invitation_id]
        self.invitations[invitation_id] = replace(
            invitation,
            accepted_at=accepted_at,
            accepted_by_user_id=membership.user_id,
        )
        self.memberships[(membership.workspace_id, membership.user_id)] = membership

    async def commit(self) -> None:
        self.commit_attempt_count += 1
        self._maybe_conflict("commit")
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1


class ServiceHarness:
    """Fixture helper for arranging workspace state around one service instance."""

    def __init__(self, *, policy: WorkspacePolicy | None = None) -> None:
        self.clock = MutableClock()
        self.repository = MemoryWorkspaceRepository()
        self.tokens = FakeInvitationTokenManager()
        self.service = WorkspaceService(
            repository=self.repository,
            invitation_tokens=self.tokens,
            policy=policy,
            clock=self.clock,
            uuid_factory=lambda: NEW_WORKSPACE_ID,
        )

    def seed_workspace(
        self,
        *,
        actor_role: WorkspaceRole = WorkspaceRole.OWNER,
        actor_user_id: UUID = ACTOR_ID,
    ) -> Workspace:
        workspace = Workspace(
            id=WORKSPACE_ID,
            name="Existing Workspace",
            created_by_user_id=ACTOR_ID,
            created_at=NOW - timedelta(days=1),
            updated_at=NOW - timedelta(days=1),
        )
        self.repository.workspaces[workspace.id] = workspace
        self.seed_membership(user_id=actor_user_id, role=actor_role)
        if actor_role is not WorkspaceRole.OWNER:
            self.seed_membership(user_id=OTHER_OWNER_ID, role=WorkspaceRole.OWNER)
        return workspace

    def seed_membership(
        self,
        *,
        user_id: UUID,
        role: WorkspaceRole,
        workspace_id: UUID = WORKSPACE_ID,
    ) -> WorkspaceMembership:
        membership = WorkspaceMembership(
            workspace_id=workspace_id,
            user_id=user_id,
            role=role,
            joined_at=NOW - timedelta(hours=1),
        )
        self.repository.memberships[(workspace_id, user_id)] = membership
        return membership

    def seed_invitation(
        self,
        *,
        invitation_id: UUID = INVITATION_ID,
        token_hash: str = "presented-digest",
        expires_at: datetime = NOW + timedelta(days=7),
        accepted_at: datetime | None = None,
        revoked_at: datetime | None = None,
    ) -> tuple[WorkspaceInvitation, str]:
        invitation = WorkspaceInvitation(
            id=invitation_id,
            workspace_id=WORKSPACE_ID,
            created_by_user_id=ACTOR_ID,
            token_hash=token_hash,
            created_at=NOW - timedelta(hours=1),
            expires_at=expires_at,
            accepted_at=accepted_at,
            accepted_by_user_id=TARGET_ID if accepted_at is not None else None,
            revoked_at=revoked_at,
            revoked_by_user_id=ACTOR_ID if revoked_at is not None else None,
        )
        self.repository.invitations[invitation.id] = invitation
        self.repository.invitation_workspace_ids[invitation.id] = invitation.workspace_id
        raw_token = self.tokens.register(token_id=invitation.id)
        return invitation, raw_token


@pytest.fixture
def harness() -> ServiceHarness:
    return ServiceHarness()


def test_default_workspace_clock_returns_aware_utc_time() -> None:
    current = utc_now()

    assert current.tzinfo is UTC


def test_workspace_policy_validates_all_bounds() -> None:
    policy = WorkspacePolicy(
        invitation_ttl=timedelta(minutes=15),
        minimum_name_length=2,
        maximum_name_length=20,
    )

    assert policy.invitation_ttl == timedelta(minutes=15)
    assert policy.minimum_name_length == 2
    assert policy.maximum_name_length == 20

    with pytest.raises(ValueError, match="invitation_ttl must be positive"):
        WorkspacePolicy(invitation_ttl=timedelta(0))
    with pytest.raises(ValueError, match="minimum_name_length must be positive"):
        WorkspacePolicy(minimum_name_length=0)
    with pytest.raises(ValueError, match="maximum_name_length"):
        WorkspacePolicy(minimum_name_length=5, maximum_name_length=4)


async def test_create_workspace_normalizes_name_and_creates_owner_atomically(
    harness: ServiceHarness,
) -> None:
    workspace = await harness.service.create_workspace(
        actor_user_id=ACTOR_ID,
        name="  Team 🐙  ",
    )

    assert workspace == Workspace(
        id=NEW_WORKSPACE_ID,
        name="Team 🐙",
        created_by_user_id=ACTOR_ID,
        created_at=NOW,
        updated_at=NOW,
    )
    assert harness.repository.memberships[(NEW_WORKSPACE_ID, ACTOR_ID)] == (
        WorkspaceMembership(
            workspace_id=NEW_WORKSPACE_ID,
            user_id=ACTOR_ID,
            role=WorkspaceRole.OWNER,
            joined_at=NOW,
        )
    )
    assert harness.repository.commit_count == 1
    assert harness.repository.rollback_count == 0


@pytest.mark.parametrize("name", ["", "   ", "x" * 101])
async def test_create_workspace_rejects_invalid_names_without_a_transaction(
    harness: ServiceHarness,
    name: str,
) -> None:
    with pytest.raises(InvalidWorkspaceName):
        await harness.service.create_workspace(actor_user_id=ACTOR_ID, name=name)

    assert harness.repository.workspaces == {}
    assert harness.repository.commit_attempt_count == 0
    assert harness.repository.rollback_count == 0


async def test_create_workspace_honors_custom_name_boundaries() -> None:
    harness = ServiceHarness(
        policy=WorkspacePolicy(minimum_name_length=2, maximum_name_length=3),
    )

    with pytest.raises(InvalidWorkspaceName):
        await harness.service.create_workspace(actor_user_id=ACTOR_ID, name="x")
    created = await harness.service.create_workspace(actor_user_id=ACTOR_ID, name=" xyz ")

    assert created.name == "xyz"


@pytest.mark.parametrize("operation", ["add_workspace", "commit"])
async def test_create_workspace_rolls_back_repository_conflicts(operation: str) -> None:
    harness = ServiceHarness()
    harness.repository.conflict_operation = operation

    with pytest.raises(WorkspaceConflict):
        await harness.service.create_workspace(actor_user_id=ACTOR_ID, name="Team")

    assert harness.repository.rollback_count == 1


async def test_list_and_read_workspace_methods_return_only_visible_state(
    harness: ServiceHarness,
) -> None:
    workspace = harness.seed_workspace()
    member = harness.seed_membership(user_id=TARGET_ID, role=WorkspaceRole.MEMBER)
    other = Workspace(
        id=UUID(int=999),
        name="Hidden",
        created_by_user_id=OUTSIDER_ID,
        created_at=NOW,
        updated_at=NOW,
    )
    harness.repository.workspaces[other.id] = other

    listed = await harness.service.list_workspaces(actor_user_id=ACTOR_ID)
    loaded = await harness.service.get_workspace(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
    )
    memberships = await harness.service.list_memberships(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
    )
    current = await harness.service.get_current_membership(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
    )

    assert listed == [workspace]
    assert loaded == workspace
    assert set(memberships) == {
        harness.repository.memberships[(WORKSPACE_ID, ACTOR_ID)],
        member,
    }
    assert current.user_id == ACTOR_ID
    assert all(not for_update for _workspace_id, for_update in harness.repository.workspace_gets)
    assert harness.repository.rollback_count == 0


@pytest.mark.parametrize("failure", ["missing", "outsider"])
async def test_read_operations_mask_absent_and_outsider_workspaces_without_rollback(
    failure: str,
) -> None:
    harness = ServiceHarness()
    if failure == "outsider":
        harness.seed_workspace()

    with pytest.raises(WorkspaceNotFound):
        await harness.service.get_workspace(
            actor_user_id=OUTSIDER_ID,
            workspace_id=WORKSPACE_ID,
        )

    assert harness.repository.rollback_count == 0


@pytest.mark.parametrize("failure", ["missing", "outsider"])
async def test_mutations_mask_absent_and_outsider_workspaces_with_rollback(
    failure: str,
) -> None:
    harness = ServiceHarness()
    if failure == "outsider":
        harness.seed_workspace()

    with pytest.raises(WorkspaceNotFound):
        await harness.service.rename_workspace(
            actor_user_id=OUTSIDER_ID,
            workspace_id=WORKSPACE_ID,
            name="Renamed",
        )

    assert harness.repository.workspace_gets[-1] == (WORKSPACE_ID, True)
    assert harness.repository.rollback_count == 1


async def test_owner_can_rename_workspace(harness: ServiceHarness) -> None:
    original = harness.seed_workspace()

    renamed = await harness.service.rename_workspace(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        name="  Renamed Workspace  ",
    )

    assert renamed == replace(original, name="Renamed Workspace", updated_at=NOW)
    assert harness.repository.workspaces[WORKSPACE_ID] == renamed
    assert harness.repository.commit_count == 1


async def test_rename_noop_rolls_back_without_writing(harness: ServiceHarness) -> None:
    original = harness.seed_workspace()

    result = await harness.service.rename_workspace(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        name=" Existing Workspace ",
    )

    assert result is original
    assert harness.repository.commit_count == 0
    assert harness.repository.rollback_count == 1


@pytest.mark.parametrize("role", [WorkspaceRole.ADMIN, WorkspaceRole.MEMBER])
async def test_non_owners_cannot_rename_workspace(role: WorkspaceRole) -> None:
    harness = ServiceHarness()
    harness.seed_workspace(actor_role=role)

    with pytest.raises(WorkspacePermissionDenied):
        await harness.service.rename_workspace(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            name="Renamed",
        )

    assert harness.repository.rollback_count == 1
    assert harness.repository.commit_count == 0


async def test_owner_can_change_member_role_and_noop_same_role(
    harness: ServiceHarness,
) -> None:
    harness.seed_workspace()
    member = harness.seed_membership(user_id=TARGET_ID, role=WorkspaceRole.MEMBER)

    unchanged = await harness.service.change_membership_role(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        target_user_id=TARGET_ID,
        role=WorkspaceRole.MEMBER,
    )
    changed = await harness.service.change_membership_role(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        target_user_id=TARGET_ID,
        role=WorkspaceRole.ADMIN,
    )

    assert unchanged == member
    assert changed == replace(member, role=WorkspaceRole.ADMIN)
    assert harness.repository.memberships[(WORKSPACE_ID, TARGET_ID)] == changed
    assert harness.repository.rollback_count == 1
    assert harness.repository.commit_count == 1
    assert harness.repository.role_counts == []


@pytest.mark.parametrize("role", [WorkspaceRole.ADMIN, WorkspaceRole.MEMBER])
async def test_non_owners_cannot_change_roles(role: WorkspaceRole) -> None:
    harness = ServiceHarness()
    harness.seed_workspace(actor_role=role)
    harness.seed_membership(user_id=TARGET_ID, role=WorkspaceRole.MEMBER)

    with pytest.raises(WorkspacePermissionDenied):
        await harness.service.change_membership_role(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            target_user_id=TARGET_ID,
            role=WorkspaceRole.ADMIN,
        )

    assert harness.repository.rollback_count == 1


async def test_change_role_masks_a_missing_target_after_locking_actor(
    harness: ServiceHarness,
) -> None:
    harness.seed_workspace()

    with pytest.raises(MembershipNotFound):
        await harness.service.change_membership_role(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            target_user_id=TARGET_ID,
            role=WorkspaceRole.ADMIN,
        )

    assert harness.repository.membership_gets[-1] == (WORKSPACE_ID, TARGET_ID, True)
    assert harness.repository.rollback_count == 1


async def test_final_owner_cannot_demote_themselves(harness: ServiceHarness) -> None:
    owner = harness.seed_workspace()

    with pytest.raises(OwnerInvariantViolation):
        await harness.service.change_membership_role(
            actor_user_id=ACTOR_ID,
            workspace_id=owner.id,
            target_user_id=ACTOR_ID,
            role=WorkspaceRole.ADMIN,
        )

    assert harness.repository.role_counts == [(WORKSPACE_ID, WorkspaceRole.OWNER)]
    assert harness.repository.rollback_count == 1


async def test_owner_can_demote_themselves_when_another_owner_remains(
    harness: ServiceHarness,
) -> None:
    harness.seed_workspace()
    harness.seed_membership(user_id=OTHER_OWNER_ID, role=WorkspaceRole.OWNER)

    changed = await harness.service.change_membership_role(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        target_user_id=ACTOR_ID,
        role=WorkspaceRole.MEMBER,
    )

    assert changed.role is WorkspaceRole.MEMBER
    assert harness.repository.commit_count == 1


@pytest.mark.parametrize(
    "target_role",
    [WorkspaceRole.MEMBER, WorkspaceRole.ADMIN, WorkspaceRole.OWNER],
)
async def test_owner_can_remove_members_of_every_role(target_role: WorkspaceRole) -> None:
    harness = ServiceHarness()
    harness.seed_workspace()
    harness.seed_membership(user_id=TARGET_ID, role=target_role)

    await harness.service.remove_membership(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        target_user_id=TARGET_ID,
    )

    assert (WORKSPACE_ID, TARGET_ID) not in harness.repository.memberships
    assert harness.repository.commit_count == 1


async def test_admin_can_remove_only_ordinary_members() -> None:
    allowed = ServiceHarness()
    allowed.seed_workspace(actor_role=WorkspaceRole.ADMIN)
    allowed.seed_membership(user_id=TARGET_ID, role=WorkspaceRole.MEMBER)

    await allowed.service.remove_membership(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        target_user_id=TARGET_ID,
    )

    assert (WORKSPACE_ID, TARGET_ID) not in allowed.repository.memberships
    assert allowed.repository.commit_count == 1

    for target_role in (WorkspaceRole.ADMIN, WorkspaceRole.OWNER):
        denied = ServiceHarness()
        denied.seed_workspace(actor_role=WorkspaceRole.ADMIN)
        denied.seed_membership(user_id=TARGET_ID, role=target_role)

        with pytest.raises(WorkspacePermissionDenied):
            await denied.service.remove_membership(
                actor_user_id=ACTOR_ID,
                workspace_id=WORKSPACE_ID,
                target_user_id=TARGET_ID,
            )

        assert denied.repository.rollback_count == 1


async def test_member_cannot_remove_another_member() -> None:
    harness = ServiceHarness()
    harness.seed_workspace(actor_role=WorkspaceRole.MEMBER)
    harness.seed_membership(user_id=TARGET_ID, role=WorkspaceRole.MEMBER)

    with pytest.raises(WorkspacePermissionDenied):
        await harness.service.remove_membership(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            target_user_id=TARGET_ID,
        )

    assert harness.repository.rollback_count == 1


async def test_remove_membership_rejects_missing_target_and_final_owner(
    harness: ServiceHarness,
) -> None:
    harness.seed_workspace()

    with pytest.raises(MembershipNotFound):
        await harness.service.remove_membership(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            target_user_id=TARGET_ID,
        )
    with pytest.raises(OwnerInvariantViolation):
        await harness.service.remove_membership(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            target_user_id=ACTOR_ID,
        )

    assert harness.repository.rollback_count == 2


@pytest.mark.parametrize("role", [WorkspaceRole.ADMIN, WorkspaceRole.MEMBER])
async def test_admins_and_members_can_leave(role: WorkspaceRole) -> None:
    harness = ServiceHarness()
    harness.seed_workspace(actor_role=role)

    await harness.service.leave_workspace(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
    )

    assert (WORKSPACE_ID, ACTOR_ID) not in harness.repository.memberships
    assert harness.repository.role_counts == []
    assert harness.repository.commit_count == 1


async def test_owner_leave_requires_another_owner(harness: ServiceHarness) -> None:
    harness.seed_workspace()

    with pytest.raises(OwnerInvariantViolation):
        await harness.service.leave_workspace(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
        )

    assert harness.repository.rollback_count == 1

    harness.seed_membership(user_id=OTHER_OWNER_ID, role=WorkspaceRole.OWNER)
    await harness.service.leave_workspace(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
    )

    assert (WORKSPACE_ID, ACTOR_ID) not in harness.repository.memberships
    assert harness.repository.commit_count == 1


@pytest.mark.parametrize("role", [WorkspaceRole.OWNER, WorkspaceRole.ADMIN])
async def test_owner_and_admin_can_create_member_only_invitations(
    role: WorkspaceRole,
) -> None:
    policy = WorkspacePolicy(invitation_ttl=timedelta(hours=2))
    harness = ServiceHarness(policy=policy)
    harness.seed_workspace(actor_role=role)

    creation = await harness.service.create_invitation(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
    )

    assert creation.raw_token in harness.tokens.presentations
    assert creation.invitation.workspace_id == WORKSPACE_ID
    assert creation.invitation.created_by_user_id == ACTOR_ID
    assert creation.invitation.created_at == NOW
    assert creation.invitation.expires_at == NOW + timedelta(hours=2)
    assert creation.invitation.is_active(NOW)
    assert creation.invitation.accepted_at is None
    assert creation.invitation.revoked_at is None
    assert harness.repository.invitations[creation.invitation.id] == creation.invitation
    assert harness.repository.commit_count == 1


async def test_member_cannot_create_an_invitation() -> None:
    harness = ServiceHarness()
    harness.seed_workspace(actor_role=WorkspaceRole.MEMBER)

    with pytest.raises(WorkspacePermissionDenied):
        await harness.service.create_invitation(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
        )

    assert harness.tokens.issue_count == 0
    assert harness.repository.rollback_count == 1


@pytest.mark.parametrize("operation", ["add_invitation", "commit"])
async def test_create_invitation_rolls_back_repository_conflicts(operation: str) -> None:
    harness = ServiceHarness()
    harness.seed_workspace()
    harness.repository.conflict_operation = operation

    with pytest.raises(WorkspaceConflict):
        await harness.service.create_invitation(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
        )

    assert harness.repository.rollback_count == 1


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        (WorkspaceRole.OWNER, True),
        (WorkspaceRole.ADMIN, True),
        (WorkspaceRole.MEMBER, False),
    ],
)
async def test_invitation_listing_role_matrix(
    role: WorkspaceRole,
    allowed: bool,
) -> None:
    harness = ServiceHarness()
    harness.seed_workspace(actor_role=role)
    invitation, _raw_token = harness.seed_invitation()

    if allowed:
        assert await harness.service.list_invitations(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
        ) == [invitation]
    else:
        with pytest.raises(WorkspacePermissionDenied):
            await harness.service.list_invitations(
                actor_user_id=ACTOR_ID,
                workspace_id=WORKSPACE_ID,
            )

    assert harness.repository.workspace_gets[-1] == (WORKSPACE_ID, False)
    assert harness.repository.rollback_count == 0


async def test_rotate_invitation_revokes_current_and_returns_new_token(
    harness: ServiceHarness,
) -> None:
    harness.seed_workspace()
    current, _raw_token = harness.seed_invitation()

    creation = await harness.service.rotate_invitation(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        invitation_id=current.id,
    )

    revoked = harness.repository.invitations[current.id]
    assert revoked.revoked_at == NOW
    assert revoked.revoked_by_user_id == ACTOR_ID
    assert creation.invitation.id != current.id
    assert creation.invitation.workspace_id == current.workspace_id
    assert creation.raw_token in harness.tokens.presentations
    assert harness.repository.invitation_gets[-1] == (WORKSPACE_ID, current.id, True)
    assert harness.repository.commit_count == 1


@pytest.mark.parametrize("state", ["missing", "expired", "accepted", "revoked"])
async def test_rotate_invitation_rejects_missing_or_inactive_state(state: str) -> None:
    harness = ServiceHarness()
    harness.seed_workspace()
    if state == "expired":
        harness.seed_invitation(expires_at=NOW)
    elif state == "accepted":
        harness.seed_invitation(accepted_at=NOW - timedelta(minutes=1))
    elif state == "revoked":
        harness.seed_invitation(revoked_at=NOW - timedelta(minutes=1))

    expected = InvitationNotFound if state == "missing" else InvitationNotActive
    with pytest.raises(expected):
        await harness.service.rotate_invitation(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            invitation_id=INVITATION_ID,
        )

    assert harness.tokens.issue_count == 0
    assert harness.repository.rollback_count == 1


@pytest.mark.parametrize("operation", ["add_invitation", "commit"])
async def test_rotate_invitation_rolls_back_repository_conflicts(operation: str) -> None:
    harness = ServiceHarness()
    harness.seed_workspace()
    harness.seed_invitation()
    harness.repository.conflict_operation = operation

    with pytest.raises(WorkspaceConflict):
        await harness.service.rotate_invitation(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            invitation_id=INVITATION_ID,
        )

    assert harness.repository.rollback_count == 1


async def test_revoke_invitation_marks_an_active_invitation(
    harness: ServiceHarness,
) -> None:
    harness.seed_workspace(actor_role=WorkspaceRole.ADMIN)
    harness.seed_invitation()

    await harness.service.revoke_invitation(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        invitation_id=INVITATION_ID,
    )

    revoked = harness.repository.invitations[INVITATION_ID]
    assert revoked.revoked_at == NOW
    assert revoked.revoked_by_user_id == ACTOR_ID
    assert harness.repository.commit_count == 1


@pytest.mark.parametrize("state", ["missing", "expired", "accepted", "revoked"])
async def test_revoke_invitation_rejects_missing_or_inactive_state(state: str) -> None:
    harness = ServiceHarness()
    harness.seed_workspace()
    if state == "expired":
        harness.seed_invitation(expires_at=NOW)
    elif state == "accepted":
        harness.seed_invitation(accepted_at=NOW - timedelta(minutes=1))
    elif state == "revoked":
        harness.seed_invitation(revoked_at=NOW - timedelta(minutes=1))

    expected = InvitationNotFound if state == "missing" else InvitationNotActive
    with pytest.raises(expected):
        await harness.service.revoke_invitation(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            invitation_id=INVITATION_ID,
        )

    assert harness.repository.rollback_count == 1


async def test_accept_invitation_consumes_token_and_creates_member(
    harness: ServiceHarness,
) -> None:
    harness.seed_workspace()
    invitation, raw_token = harness.seed_invitation()

    membership = await harness.service.accept_invitation(
        actor_user_id=TARGET_ID,
        raw_token=raw_token,
    )

    assert membership == WorkspaceMembership(
        workspace_id=WORKSPACE_ID,
        user_id=TARGET_ID,
        role=WorkspaceRole.MEMBER,
        joined_at=NOW,
    )
    assert harness.repository.memberships[(WORKSPACE_ID, TARGET_ID)] == membership
    accepted = harness.repository.invitations[invitation.id]
    assert accepted.accepted_at == NOW
    assert accepted.accepted_by_user_id == TARGET_ID
    assert harness.tokens.match_calls == [("presented-digest", "presented-digest")]
    assert harness.repository.workspace_gets[-1] == (WORKSPACE_ID, True)
    assert harness.repository.invitation_gets[-1] == (WORKSPACE_ID, INVITATION_ID, True)
    assert harness.repository.membership_gets[-1] == (WORKSPACE_ID, TARGET_ID, True)
    assert harness.repository.commit_count == 1


@pytest.mark.parametrize("raw_token", [None, "", "malformed"])
async def test_accept_invitation_rejects_malformed_presentations_before_transaction(
    harness: ServiceHarness,
    raw_token: str | None,
) -> None:
    with pytest.raises(InvalidInvitationToken):
        await harness.service.accept_invitation(
            actor_user_id=TARGET_ID,
            raw_token=raw_token,
        )

    assert harness.repository.workspace_gets == []
    assert harness.repository.rollback_count == 0


async def test_accept_invitation_masks_unknown_selector(harness: ServiceHarness) -> None:
    raw_token = harness.tokens.register(token_id=UNKNOWN_INVITATION_ID)

    with pytest.raises(InvalidInvitationToken):
        await harness.service.accept_invitation(
            actor_user_id=TARGET_ID,
            raw_token=raw_token,
        )

    assert harness.repository.workspace_gets == []
    assert harness.repository.rollback_count == 1


async def test_accept_invitation_masks_missing_workspace(harness: ServiceHarness) -> None:
    raw_token = harness.tokens.register(token_id=INVITATION_ID)
    harness.repository.invitation_workspace_ids[INVITATION_ID] = WORKSPACE_ID

    with pytest.raises(InvalidInvitationToken):
        await harness.service.accept_invitation(
            actor_user_id=TARGET_ID,
            raw_token=raw_token,
        )

    assert harness.repository.workspace_gets == [(WORKSPACE_ID, True)]
    assert harness.repository.rollback_count == 1


@pytest.mark.parametrize("state", ["missing", "expired", "accepted", "revoked", "mismatch"])
async def test_accept_invitation_masks_missing_inactive_and_mismatched_tokens(
    state: str,
) -> None:
    harness = ServiceHarness()
    harness.seed_workspace()
    if state == "missing":
        raw_token = harness.tokens.register(token_id=INVITATION_ID)
        harness.repository.invitation_workspace_ids[INVITATION_ID] = WORKSPACE_ID
    elif state == "expired":
        _invitation, raw_token = harness.seed_invitation(expires_at=NOW)
    elif state == "accepted":
        _invitation, raw_token = harness.seed_invitation(
            accepted_at=NOW - timedelta(minutes=1),
        )
    elif state == "revoked":
        _invitation, raw_token = harness.seed_invitation(
            revoked_at=NOW - timedelta(minutes=1),
        )
    else:
        _invitation, raw_token = harness.seed_invitation(token_hash="different-digest")

    with pytest.raises(InvalidInvitationToken):
        await harness.service.accept_invitation(
            actor_user_id=TARGET_ID,
            raw_token=raw_token,
        )

    assert harness.repository.rollback_count == 1
    assert harness.repository.commit_count == 0


async def test_accept_invitation_rejects_existing_membership(
    harness: ServiceHarness,
) -> None:
    harness.seed_workspace()
    harness.seed_membership(user_id=TARGET_ID, role=WorkspaceRole.ADMIN)
    _invitation, raw_token = harness.seed_invitation()

    with pytest.raises(MembershipAlreadyExists):
        await harness.service.accept_invitation(
            actor_user_id=TARGET_ID,
            raw_token=raw_token,
        )

    assert harness.repository.memberships[(WORKSPACE_ID, TARGET_ID)].role is WorkspaceRole.ADMIN
    assert harness.repository.rollback_count == 1


@pytest.mark.parametrize("operation", ["accept_invitation", "commit"])
async def test_accept_invitation_translates_repository_conflicts(operation: str) -> None:
    harness = ServiceHarness()
    harness.seed_workspace()
    _invitation, raw_token = harness.seed_invitation()
    harness.repository.conflict_operation = operation

    with pytest.raises(MembershipAlreadyExists):
        await harness.service.accept_invitation(
            actor_user_id=TARGET_ID,
            raw_token=raw_token,
        )

    assert harness.repository.rollback_count == 1

"""Channel application authorization, visibility, and transaction contracts."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from klack.modules.channels.application.ports import ChannelConflict
from klack.modules.channels.application.service import (
    ChannelPolicy,
    ChannelService,
    utc_now,
)
from klack.modules.channels.domain.entities import (
    Channel,
    ChannelMembership,
    ChannelVisibility,
)
from klack.modules.channels.domain.errors import (
    ChannelArchived,
    ChannelMembershipNotFound,
    ChannelNameConflict,
    ChannelNotFound,
    ChannelPermissionDenied,
    InvalidChannelName,
    TargetWorkspaceMembershipNotFound,
)
from klack.modules.workspaces.domain.entities import WorkspaceMembership, WorkspaceRole
from klack.modules.workspaces.domain.errors import WorkspaceNotFound

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
ACTOR_ID = UUID(int=1)
OTHER_OWNER_ID = UUID(int=2)
TARGET_ID = UUID(int=3)
OTHER_ADMIN_ID = UUID(int=4)
OTHER_MEMBER_ID = UUID(int=5)
OUTSIDER_ID = UUID(int=6)
WORKSPACE_ID = UUID(int=101)
OTHER_WORKSPACE_ID = UUID(int=102)
CHANNEL_ID = UUID(int=201)
OTHER_CHANNEL_ID = UUID(int=202)
NEW_CHANNEL_ID = UUID(int=203)


class MutableClock:
    """Deterministic service clock whose value a test may advance."""

    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class MemoryWorkspaceAccess:
    """Workspace authorization fake with observable durable-lock requests."""

    def __init__(self, events: list[str]) -> None:
        self.memberships: dict[tuple[UUID, UUID], WorkspaceMembership] = {}
        self.events = events
        self.require_calls: list[tuple[UUID, UUID, bool]] = []
        self.get_calls: list[tuple[UUID, UUID, bool]] = []

    async def require_membership(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        for_update: bool,
    ) -> WorkspaceMembership:
        self.require_calls.append((actor_user_id, workspace_id, for_update))
        self.events.extend(["workspace", f"actor-workspace:{actor_user_id.int}"])
        membership = self.memberships.get((workspace_id, actor_user_id))
        if membership is None:
            raise WorkspaceNotFound
        return membership

    async def get_membership(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        for_update: bool,
    ) -> WorkspaceMembership | None:
        self.get_calls.append((workspace_id, user_id, for_update))
        self.events.append(f"target-workspace:{user_id.int}")
        return self.memberships.get((workspace_id, user_id))


class MemoryChannelRepository:
    """Transaction-observable in-memory fake implementing the channel repository port."""

    def __init__(self, events: list[str]) -> None:
        self.channels: dict[UUID, Channel] = {}
        self.memberships: dict[tuple[UUID, UUID], ChannelMembership] = {}
        self.events = events
        self.channel_gets: list[tuple[UUID, UUID, bool]] = []
        self.membership_gets: list[tuple[UUID, UUID, bool]] = []
        self.list_channel_calls: list[tuple[UUID, UUID, bool, bool]] = []
        self.list_membership_calls: list[UUID] = []
        self.channel_updates: list[tuple[UUID, str, ChannelVisibility, datetime]] = []
        self.archive_updates: list[tuple[UUID, datetime | None, UUID | None, datetime]] = []
        self.membership_removals: list[tuple[UUID, UUID]] = []
        self.commit_count = 0
        self.commit_attempt_count = 0
        self.rollback_count = 0
        self.conflict_operation: str | None = None
        self.race_membership: ChannelMembership | None = None
        self._pending_channel_add: tuple[Channel, ChannelMembership] | None = None
        self._pending_channel_update: (
            tuple[
                UUID,
                str,
                ChannelVisibility,
                datetime,
            ]
            | None
        ) = None
        self._pending_archive_update: (
            tuple[
                UUID,
                datetime | None,
                UUID | None,
                datetime,
            ]
            | None
        ) = None
        self._pending_membership_add: ChannelMembership | None = None
        self._pending_membership_removal: tuple[UUID, UUID] | None = None

    def _raise_conflict(self, operation: str) -> None:
        if self.conflict_operation != operation:
            return
        if operation == "add_membership" and self.race_membership is not None:
            race = self.race_membership
            self.memberships[(race.channel_id, race.user_id)] = race
        raise ChannelConflict

    async def add_channel(
        self,
        channel: Channel,
        creator_membership: ChannelMembership,
    ) -> None:
        self._raise_conflict("add_channel")
        self._pending_channel_add = (channel, creator_membership)

    async def list_channels(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        can_view_private: bool,
        include_archived: bool,
    ) -> list[tuple[Channel, ChannelMembership | None]]:
        self.list_channel_calls.append(
            (workspace_id, actor_user_id, can_view_private, include_archived),
        )
        rows: list[tuple[Channel, ChannelMembership | None]] = []
        for channel in sorted(self.channels.values(), key=lambda item: (item.name, item.id.int)):
            if channel.workspace_id != workspace_id:
                continue
            membership = self.memberships.get((channel.id, actor_user_id))
            if channel.visibility is ChannelVisibility.PRIVATE and not (
                can_view_private or membership is not None
            ):
                continue
            if channel.is_archived and not include_archived:
                continue
            rows.append((channel, membership))
        return rows

    async def get_channel(
        self,
        *,
        workspace_id: UUID,
        channel_id: UUID,
        for_update: bool = False,
    ) -> Channel | None:
        self.channel_gets.append((workspace_id, channel_id, for_update))
        self.events.append(f"channel:{channel_id.int}")
        channel = self.channels.get(channel_id)
        if channel is None or channel.workspace_id != workspace_id:
            return None
        return channel

    async def update_channel(
        self,
        *,
        channel_id: UUID,
        name: str,
        visibility: ChannelVisibility,
        updated_at: datetime,
    ) -> None:
        self._raise_conflict("update_channel")
        update = (channel_id, name, visibility, updated_at)
        self.channel_updates.append(update)
        self._pending_channel_update = update

    async def set_channel_archived(
        self,
        *,
        channel_id: UUID,
        archived_at: datetime | None,
        archived_by_user_id: UUID | None,
        updated_at: datetime,
    ) -> None:
        update = (channel_id, archived_at, archived_by_user_id, updated_at)
        self.archive_updates.append(update)
        self._pending_archive_update = update

    async def get_membership(
        self,
        *,
        channel_id: UUID,
        user_id: UUID,
        for_update: bool = False,
    ) -> ChannelMembership | None:
        self.membership_gets.append((channel_id, user_id, for_update))
        self.events.append(f"channel-membership:{user_id.int}")
        return self.memberships.get((channel_id, user_id))

    async def list_memberships(self, *, channel_id: UUID) -> list[ChannelMembership]:
        self.list_membership_calls.append(channel_id)
        return sorted(
            (
                membership
                for (member_channel_id, _user_id), membership in self.memberships.items()
                if member_channel_id == channel_id
            ),
            key=lambda item: (item.joined_at, item.user_id.int),
        )

    async def add_membership(self, membership: ChannelMembership) -> None:
        self._raise_conflict("add_membership")
        self._pending_membership_add = membership

    async def remove_membership(self, *, channel_id: UUID, user_id: UUID) -> None:
        removal = (channel_id, user_id)
        self.membership_removals.append(removal)
        self._pending_membership_removal = removal

    async def commit(self) -> None:
        self.commit_attempt_count += 1
        if self.conflict_operation == "commit":
            if self._pending_membership_add is not None and self.race_membership is not None:
                race = self.race_membership
                self.memberships[(race.channel_id, race.user_id)] = race
            self._clear_pending()
            raise ChannelConflict
        if self._pending_channel_add is not None:
            channel, creator_membership = self._pending_channel_add
            self.channels[channel.id] = channel
            self.memberships[(channel.id, creator_membership.user_id)] = creator_membership
        if self._pending_channel_update is not None:
            channel_id, name, visibility, updated_at = self._pending_channel_update
            self.channels[channel_id] = replace(
                self.channels[channel_id],
                name=name,
                visibility=visibility,
                updated_at=updated_at,
            )
        if self._pending_archive_update is not None:
            channel_id, archived_at, archived_by_user_id, updated_at = self._pending_archive_update
            self.channels[channel_id] = replace(
                self.channels[channel_id],
                archived_at=archived_at,
                archived_by_user_id=archived_by_user_id,
                updated_at=updated_at,
            )
        if self._pending_membership_add is not None:
            membership = self._pending_membership_add
            self.memberships[(membership.channel_id, membership.user_id)] = membership
        if self._pending_membership_removal is not None:
            self.memberships.pop(self._pending_membership_removal, None)
        self._clear_pending()
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1
        self._clear_pending()

    def _clear_pending(self) -> None:
        self._pending_channel_add = None
        self._pending_channel_update = None
        self._pending_archive_update = None
        self._pending_membership_add = None
        self._pending_membership_removal = None


class ServiceHarness:
    """Fixture helper for arranging channel and workspace state around one service."""

    def __init__(self, *, policy: ChannelPolicy | None = None) -> None:
        self.events: list[str] = []
        self.clock = MutableClock()
        self.repository = MemoryChannelRepository(self.events)
        self.workspace_access = MemoryWorkspaceAccess(self.events)
        self.service = ChannelService(
            repository=self.repository,
            workspace_access=self.workspace_access,
            policy=policy,
            clock=self.clock,
            uuid_factory=lambda: NEW_CHANNEL_ID,
        )

    def seed_workspace_membership(
        self,
        *,
        user_id: UUID = ACTOR_ID,
        role: WorkspaceRole = WorkspaceRole.OWNER,
        workspace_id: UUID = WORKSPACE_ID,
    ) -> WorkspaceMembership:
        membership = WorkspaceMembership(
            workspace_id=workspace_id,
            user_id=user_id,
            role=role,
            joined_at=NOW - timedelta(days=2),
        )
        self.workspace_access.memberships[(workspace_id, user_id)] = membership
        return membership

    def seed_channel(
        self,
        *,
        channel_id: UUID = CHANNEL_ID,
        workspace_id: UUID = WORKSPACE_ID,
        name: str = "general",
        visibility: ChannelVisibility = ChannelVisibility.PUBLIC,
        archived: bool = False,
        created_by_user_id: UUID = ACTOR_ID,
    ) -> Channel:
        channel = Channel(
            id=channel_id,
            workspace_id=workspace_id,
            name=name,
            visibility=visibility,
            created_by_user_id=created_by_user_id,
            created_at=NOW - timedelta(days=1),
            updated_at=NOW - timedelta(days=1),
            archived_at=NOW - timedelta(hours=2) if archived else None,
            archived_by_user_id=OTHER_OWNER_ID if archived else None,
        )
        self.repository.channels[channel.id] = channel
        return channel

    def seed_channel_membership(
        self,
        *,
        user_id: UUID = ACTOR_ID,
        channel_id: UUID = CHANNEL_ID,
        workspace_id: UUID = WORKSPACE_ID,
        added_by_user_id: UUID = ACTOR_ID,
        joined_at: datetime = NOW - timedelta(hours=1),
    ) -> ChannelMembership:
        membership = ChannelMembership(
            workspace_id=workspace_id,
            channel_id=channel_id,
            user_id=user_id,
            added_by_user_id=added_by_user_id,
            joined_at=joined_at,
        )
        self.repository.memberships[(channel_id, user_id)] = membership
        return membership


@pytest.fixture
def harness() -> ServiceHarness:
    return ServiceHarness()


def test_default_channel_clock_returns_aware_utc_time() -> None:
    current = utc_now()

    assert current.tzinfo is UTC


def test_channel_policy_validates_name_bounds() -> None:
    policy = ChannelPolicy(minimum_name_length=2, maximum_name_length=20)

    assert policy.minimum_name_length == 2
    assert policy.maximum_name_length == 20

    with pytest.raises(ValueError, match="minimum_name_length must be positive"):
        ChannelPolicy(minimum_name_length=0)
    with pytest.raises(ValueError, match="maximum_name_length"):
        ChannelPolicy(minimum_name_length=5, maximum_name_length=4)


@pytest.mark.parametrize("role", [WorkspaceRole.OWNER, WorkspaceRole.ADMIN])
async def test_managers_create_normalized_channel_and_creator_membership_atomically(
    role: WorkspaceRole,
) -> None:
    harness = ServiceHarness()
    harness.seed_workspace_membership(role=role)

    view = await harness.service.create_channel(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        name="  Product-API  ",
        visibility=ChannelVisibility.PRIVATE,
    )

    expected_channel = Channel(
        id=NEW_CHANNEL_ID,
        workspace_id=WORKSPACE_ID,
        name="product-api",
        visibility=ChannelVisibility.PRIVATE,
        created_by_user_id=ACTOR_ID,
        created_at=NOW,
        updated_at=NOW,
        archived_at=None,
        archived_by_user_id=None,
    )
    assert view.channel == expected_channel
    assert view.is_member is True
    assert harness.repository.channels[NEW_CHANNEL_ID] == expected_channel
    assert harness.repository.memberships[(NEW_CHANNEL_ID, ACTOR_ID)] == ChannelMembership(
        workspace_id=WORKSPACE_ID,
        channel_id=NEW_CHANNEL_ID,
        user_id=ACTOR_ID,
        added_by_user_id=ACTOR_ID,
        joined_at=NOW,
    )
    assert harness.workspace_access.require_calls == [(ACTOR_ID, WORKSPACE_ID, True)]
    assert harness.repository.commit_count == 1
    assert harness.repository.rollback_count == 0


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        "-general",
        "general-",
        "two--words",
        "two words",
        "snake_case",
        "café",
        "x" * 81,
    ],
)
async def test_create_rejects_invalid_slug_before_authorization(name: str) -> None:
    harness = ServiceHarness()

    with pytest.raises(InvalidChannelName):
        await harness.service.create_channel(
            actor_user_id=OUTSIDER_ID,
            workspace_id=WORKSPACE_ID,
            name=name,
            visibility=ChannelVisibility.PUBLIC,
        )

    assert harness.workspace_access.require_calls == []
    assert harness.repository.commit_attempt_count == 0
    assert harness.repository.rollback_count == 0


async def test_create_honors_custom_name_boundaries() -> None:
    harness = ServiceHarness(policy=ChannelPolicy(minimum_name_length=3, maximum_name_length=5))
    harness.seed_workspace_membership()

    with pytest.raises(InvalidChannelName):
        await harness.service.create_channel(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            name="ab",
            visibility=ChannelVisibility.PUBLIC,
        )
    created = await harness.service.create_channel(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        name=" ABCDE ",
        visibility=ChannelVisibility.PUBLIC,
    )

    assert created.channel.name == "abcde"


async def test_member_cannot_create_channel_and_failure_rolls_back(
    harness: ServiceHarness,
) -> None:
    harness.seed_workspace_membership(role=WorkspaceRole.MEMBER)

    with pytest.raises(ChannelPermissionDenied):
        await harness.service.create_channel(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            name="general",
            visibility=ChannelVisibility.PUBLIC,
        )

    assert harness.repository.channels == {}
    assert harness.repository.rollback_count == 1
    assert harness.repository.commit_attempt_count == 0


@pytest.mark.parametrize("operation", ["add_channel", "commit"])
async def test_create_translates_repository_conflicts(operation: str) -> None:
    harness = ServiceHarness()
    harness.seed_workspace_membership()
    harness.repository.conflict_operation = operation

    with pytest.raises(ChannelNameConflict):
        await harness.service.create_channel(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            name="general",
            visibility=ChannelVisibility.PUBLIC,
        )

    assert harness.repository.rollback_count == 1


async def test_workspace_outsider_failure_is_propagated_before_channel_state(
    harness: ServiceHarness,
) -> None:
    with pytest.raises(WorkspaceNotFound):
        await harness.service.list_channels(
            actor_user_id=OUTSIDER_ID,
            workspace_id=WORKSPACE_ID,
        )

    assert harness.repository.list_channel_calls == []


async def test_member_list_includes_public_and_joined_private_but_masks_other_private(
    harness: ServiceHarness,
) -> None:
    harness.seed_workspace_membership(role=WorkspaceRole.MEMBER)
    public = harness.seed_channel(name="announcements")
    joined_private = harness.seed_channel(
        channel_id=OTHER_CHANNEL_ID,
        name="leadership",
        visibility=ChannelVisibility.PRIVATE,
    )
    hidden_private = harness.seed_channel(
        channel_id=NEW_CHANNEL_ID,
        name="security",
        visibility=ChannelVisibility.PRIVATE,
    )
    joined = harness.seed_channel_membership(channel_id=joined_private.id)

    views = await harness.service.list_channels(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
    )

    assert [(view.channel, view.is_member) for view in views] == [
        (public, False),
        (joined_private, True),
    ]
    assert hidden_private not in [view.channel for view in views]
    assert joined == harness.repository.memberships[(joined_private.id, ACTOR_ID)]
    assert harness.repository.list_channel_calls == [
        (WORKSPACE_ID, ACTOR_ID, False, False),
    ]


@pytest.mark.parametrize("role", [WorkspaceRole.OWNER, WorkspaceRole.ADMIN])
async def test_manager_list_includes_unjoined_private_and_requested_archived(
    role: WorkspaceRole,
) -> None:
    harness = ServiceHarness()
    harness.seed_workspace_membership(role=role)
    private = harness.seed_channel(visibility=ChannelVisibility.PRIVATE)
    archived = harness.seed_channel(
        channel_id=OTHER_CHANNEL_ID,
        name="old-room",
        visibility=ChannelVisibility.PRIVATE,
        archived=True,
    )

    active_views = await harness.service.list_channels(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
    )
    all_views = await harness.service.list_channels(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        include_archived=True,
    )

    assert [(view.channel, view.is_member) for view in active_views] == [(private, False)]
    assert [(view.channel, view.is_member) for view in all_views] == [
        (private, False),
        (archived, False),
    ]
    assert harness.repository.list_channel_calls == [
        (WORKSPACE_ID, ACTOR_ID, True, False),
        (WORKSPACE_ID, ACTOR_ID, True, True),
    ]
    assert all(not call[2] for call in harness.workspace_access.require_calls)


@pytest.mark.parametrize(
    ("role", "visibility", "joined", "expected_visible", "expected_member"),
    [
        (WorkspaceRole.MEMBER, ChannelVisibility.PUBLIC, False, True, False),
        (WorkspaceRole.MEMBER, ChannelVisibility.PRIVATE, False, False, False),
        (WorkspaceRole.MEMBER, ChannelVisibility.PRIVATE, True, True, True),
        (WorkspaceRole.ADMIN, ChannelVisibility.PRIVATE, False, True, False),
        (WorkspaceRole.OWNER, ChannelVisibility.PRIVATE, False, True, False),
    ],
)
async def test_get_channel_applies_visibility_matrix(
    role: WorkspaceRole,
    visibility: ChannelVisibility,
    joined: bool,
    expected_visible: bool,
    expected_member: bool,
) -> None:
    harness = ServiceHarness()
    harness.seed_workspace_membership(role=role)
    channel = harness.seed_channel(visibility=visibility)
    if joined:
        harness.seed_channel_membership()

    if not expected_visible:
        with pytest.raises(ChannelNotFound):
            await harness.service.get_channel(
                actor_user_id=ACTOR_ID,
                workspace_id=WORKSPACE_ID,
                channel_id=CHANNEL_ID,
            )
    else:
        view = await harness.service.get_channel(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
        )
        assert view.channel == channel
        assert view.is_member is expected_member

    assert harness.repository.channel_gets == [(WORKSPACE_ID, CHANNEL_ID, False)]
    assert harness.repository.membership_gets == [(CHANNEL_ID, ACTOR_ID, False)]
    assert harness.repository.rollback_count == 0


async def test_read_of_missing_or_cross_workspace_channel_is_masked_without_rollback(
    harness: ServiceHarness,
) -> None:
    harness.seed_workspace_membership(role=WorkspaceRole.MEMBER)
    harness.seed_channel(workspace_id=OTHER_WORKSPACE_ID)

    with pytest.raises(ChannelNotFound):
        await harness.service.get_channel(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
        )

    assert harness.repository.membership_gets == []
    assert harness.repository.rollback_count == 0


@pytest.mark.parametrize("role", [WorkspaceRole.OWNER, WorkspaceRole.ADMIN])
async def test_managers_update_name_and_visibility_without_rewriting_memberships(
    role: WorkspaceRole,
) -> None:
    harness = ServiceHarness()
    harness.seed_workspace_membership(role=role)
    channel = harness.seed_channel(visibility=ChannelVisibility.PUBLIC)
    actor_membership = harness.seed_channel_membership()
    target_membership = harness.seed_channel_membership(user_id=TARGET_ID)

    view = await harness.service.update_channel(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        name="  Leadership-Team ",
        visibility=ChannelVisibility.PRIVATE,
    )

    assert view.channel == replace(
        channel,
        name="leadership-team",
        visibility=ChannelVisibility.PRIVATE,
        updated_at=NOW,
    )
    assert view.is_member is True
    assert harness.repository.channels[CHANNEL_ID] == view.channel
    assert harness.repository.memberships == {
        (CHANNEL_ID, ACTOR_ID): actor_membership,
        (CHANNEL_ID, TARGET_ID): target_membership,
    }
    assert harness.repository.channel_gets == [(WORKSPACE_ID, CHANNEL_ID, True)]
    assert harness.repository.membership_gets == [(CHANNEL_ID, ACTOR_ID, False)]
    assert harness.repository.commit_count == 1


async def test_update_without_changes_is_idempotent_and_rolls_back(
    harness: ServiceHarness,
) -> None:
    harness.seed_workspace_membership()
    channel = harness.seed_channel()

    view = await harness.service.update_channel(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        name=None,
        visibility=None,
    )

    assert view.channel == channel
    assert view.is_member is False
    assert harness.repository.channel_updates == []
    assert harness.repository.commit_attempt_count == 0
    assert harness.repository.rollback_count == 1


@pytest.mark.parametrize("role", [WorkspaceRole.MEMBER])
async def test_non_manager_cannot_update_channel(role: WorkspaceRole) -> None:
    harness = ServiceHarness()
    harness.seed_workspace_membership(role=role)
    harness.seed_channel()

    with pytest.raises(ChannelPermissionDenied):
        await harness.service.update_channel(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            name="renamed",
            visibility=None,
        )

    assert harness.repository.channel_gets == []
    assert harness.repository.rollback_count == 1


async def test_update_validates_name_before_authorization(harness: ServiceHarness) -> None:
    harness.seed_workspace_membership(role=WorkspaceRole.MEMBER)

    with pytest.raises(InvalidChannelName):
        await harness.service.update_channel(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            name="invalid name",
            visibility=None,
        )

    assert harness.workspace_access.require_calls == []
    assert harness.repository.rollback_count == 0


async def test_update_rejects_archived_channel(harness: ServiceHarness) -> None:
    harness.seed_workspace_membership()
    original = harness.seed_channel(archived=True)

    with pytest.raises(ChannelArchived):
        await harness.service.update_channel(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            name="renamed",
            visibility=None,
        )

    assert harness.repository.channels[CHANNEL_ID] == original
    assert harness.repository.rollback_count == 1
    assert harness.repository.commit_attempt_count == 0


async def test_update_missing_channel_rolls_back_locked_transaction(
    harness: ServiceHarness,
) -> None:
    harness.seed_workspace_membership()

    with pytest.raises(ChannelNotFound):
        await harness.service.update_channel(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            name=None,
            visibility=ChannelVisibility.PRIVATE,
        )

    assert harness.repository.channel_gets == [(WORKSPACE_ID, CHANNEL_ID, True)]
    assert harness.repository.rollback_count == 1


@pytest.mark.parametrize("operation", ["update_channel", "commit"])
async def test_update_translates_name_conflicts_and_rolls_back(operation: str) -> None:
    harness = ServiceHarness()
    harness.seed_workspace_membership()
    original = harness.seed_channel()
    harness.repository.conflict_operation = operation

    with pytest.raises(ChannelNameConflict):
        await harness.service.update_channel(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            name="renamed",
            visibility=None,
        )

    assert harness.repository.channels[CHANNEL_ID] == original
    assert harness.repository.rollback_count == 1


async def test_archive_and_unarchive_are_reversible_and_preserve_memberships(
    harness: ServiceHarness,
) -> None:
    harness.seed_workspace_membership()
    original = harness.seed_channel()
    actor_membership = harness.seed_channel_membership()
    target_membership = harness.seed_channel_membership(user_id=TARGET_ID)

    archived = await harness.service.archive_channel(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
    )

    assert archived.channel == replace(
        original,
        updated_at=NOW,
        archived_at=NOW,
        archived_by_user_id=ACTOR_ID,
    )
    assert archived.is_member is True
    assert harness.repository.memberships == {
        (CHANNEL_ID, ACTOR_ID): actor_membership,
        (CHANNEL_ID, TARGET_ID): target_membership,
    }

    restored_at = NOW + timedelta(hours=1)
    harness.clock.value = restored_at
    restored = await harness.service.unarchive_channel(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
    )

    assert restored.channel == replace(
        original,
        updated_at=restored_at,
        archived_at=None,
        archived_by_user_id=None,
    )
    assert restored.is_member is True
    assert harness.repository.memberships == {
        (CHANNEL_ID, ACTOR_ID): actor_membership,
        (CHANNEL_ID, TARGET_ID): target_membership,
    }
    assert harness.repository.commit_count == 2


@pytest.mark.parametrize("operation", ["archive", "unarchive"])
async def test_archive_transitions_are_idempotent(operation: str) -> None:
    harness = ServiceHarness()
    harness.seed_workspace_membership()
    channel = harness.seed_channel(archived=operation == "archive")

    if operation == "archive":
        view = await harness.service.archive_channel(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
        )
    else:
        view = await harness.service.unarchive_channel(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
        )

    assert view.channel == channel
    assert harness.repository.archive_updates == []
    assert harness.repository.commit_attempt_count == 0
    assert harness.repository.rollback_count == 1


@pytest.mark.parametrize("operation", ["archive", "unarchive"])
async def test_non_manager_cannot_change_archive_state(operation: str) -> None:
    harness = ServiceHarness()
    harness.seed_workspace_membership(role=WorkspaceRole.MEMBER)
    harness.seed_channel(archived=operation == "unarchive")

    with pytest.raises(ChannelPermissionDenied):
        if operation == "archive":
            await harness.service.archive_channel(
                actor_user_id=ACTOR_ID,
                workspace_id=WORKSPACE_ID,
                channel_id=CHANNEL_ID,
            )
        else:
            await harness.service.unarchive_channel(
                actor_user_id=ACTOR_ID,
                workspace_id=WORKSPACE_ID,
                channel_id=CHANNEL_ID,
            )

    assert harness.repository.channel_gets == []
    assert harness.repository.rollback_count == 1


@pytest.mark.parametrize("operation", ["archive", "unarchive"])
async def test_archive_mutations_mask_missing_channel_and_roll_back(operation: str) -> None:
    harness = ServiceHarness()
    harness.seed_workspace_membership()

    with pytest.raises(ChannelNotFound):
        if operation == "archive":
            await harness.service.archive_channel(
                actor_user_id=ACTOR_ID,
                workspace_id=WORKSPACE_ID,
                channel_id=CHANNEL_ID,
            )
        else:
            await harness.service.unarchive_channel(
                actor_user_id=ACTOR_ID,
                workspace_id=WORKSPACE_ID,
                channel_id=CHANNEL_ID,
            )

    assert harness.repository.rollback_count == 1


@pytest.mark.parametrize(
    ("visibility", "joined", "role", "expected_error"),
    [
        (ChannelVisibility.PUBLIC, False, WorkspaceRole.MEMBER, None),
        (ChannelVisibility.PRIVATE, True, WorkspaceRole.MEMBER, None),
        (ChannelVisibility.PRIVATE, False, WorkspaceRole.ADMIN, None),
        (ChannelVisibility.PRIVATE, False, WorkspaceRole.MEMBER, ChannelNotFound),
    ],
)
async def test_list_memberships_requires_channel_visibility(
    visibility: ChannelVisibility,
    joined: bool,
    role: WorkspaceRole,
    expected_error: type[Exception] | None,
) -> None:
    harness = ServiceHarness()
    harness.seed_workspace_membership(role=role)
    harness.seed_channel(visibility=visibility)
    actor_membership = harness.seed_channel_membership() if joined else None
    target_membership = harness.seed_channel_membership(
        user_id=TARGET_ID,
        joined_at=NOW - timedelta(hours=2),
    )

    if expected_error is not None:
        with pytest.raises(expected_error):
            await harness.service.list_memberships(
                actor_user_id=ACTOR_ID,
                workspace_id=WORKSPACE_ID,
                channel_id=CHANNEL_ID,
            )
        assert harness.repository.list_membership_calls == []
        return

    memberships = await harness.service.list_memberships(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
    )

    expected = [target_membership]
    if actor_membership is not None:
        expected.append(actor_membership)
    assert memberships == expected
    assert harness.repository.list_membership_calls == [CHANNEL_ID]
    assert harness.repository.channel_gets == [(WORKSPACE_ID, CHANNEL_ID, False)]


async def test_get_current_membership_returns_joined_actor(harness: ServiceHarness) -> None:
    harness.seed_workspace_membership(role=WorkspaceRole.MEMBER)
    harness.seed_channel(visibility=ChannelVisibility.PRIVATE)
    expected = harness.seed_channel_membership()

    membership = await harness.service.get_current_membership(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
    )

    assert membership == expected


@pytest.mark.parametrize(
    ("visibility", "expected_error"),
    [
        (ChannelVisibility.PUBLIC, ChannelMembershipNotFound),
        (ChannelVisibility.PRIVATE, ChannelNotFound),
    ],
)
async def test_get_current_membership_distinguishes_visible_absence_from_hidden_channel(
    visibility: ChannelVisibility,
    expected_error: type[Exception],
) -> None:
    harness = ServiceHarness()
    harness.seed_workspace_membership(role=WorkspaceRole.MEMBER)
    harness.seed_channel(visibility=visibility)

    with pytest.raises(expected_error):
        await harness.service.get_current_membership(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
        )

    assert harness.repository.rollback_count == 0


@pytest.mark.parametrize(
    ("role", "visibility"),
    [
        (WorkspaceRole.MEMBER, ChannelVisibility.PUBLIC),
        (WorkspaceRole.ADMIN, ChannelVisibility.PRIVATE),
        (WorkspaceRole.OWNER, ChannelVisibility.PRIVATE),
    ],
)
async def test_join_creates_membership_for_public_member_or_private_manager(
    role: WorkspaceRole,
    visibility: ChannelVisibility,
) -> None:
    harness = ServiceHarness()
    harness.seed_workspace_membership(role=role)
    harness.seed_channel(visibility=visibility)

    membership = await harness.service.join_channel(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
    )

    assert membership == ChannelMembership(
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        user_id=ACTOR_ID,
        added_by_user_id=ACTOR_ID,
        joined_at=NOW,
    )
    assert harness.repository.memberships[(CHANNEL_ID, ACTOR_ID)] == membership
    assert harness.workspace_access.require_calls == [(ACTOR_ID, WORKSPACE_ID, True)]
    assert harness.repository.channel_gets == [(WORKSPACE_ID, CHANNEL_ID, True)]
    assert harness.repository.membership_gets == [(CHANNEL_ID, ACTOR_ID, True)]
    assert harness.events == [
        "workspace",
        f"actor-workspace:{ACTOR_ID.int}",
        f"channel:{CHANNEL_ID.int}",
        f"channel-membership:{ACTOR_ID.int}",
    ]
    assert harness.repository.commit_count == 1


async def test_join_masks_private_channel_from_ordinary_nonmember(
    harness: ServiceHarness,
) -> None:
    harness.seed_workspace_membership(role=WorkspaceRole.MEMBER)
    harness.seed_channel(visibility=ChannelVisibility.PRIVATE)

    with pytest.raises(ChannelNotFound):
        await harness.service.join_channel(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
        )

    assert harness.repository.rollback_count == 1
    assert harness.repository.commit_attempt_count == 0


async def test_join_existing_membership_is_idempotent_even_when_archived(
    harness: ServiceHarness,
) -> None:
    harness.seed_workspace_membership(role=WorkspaceRole.MEMBER)
    harness.seed_channel(visibility=ChannelVisibility.PRIVATE, archived=True)
    existing = harness.seed_channel_membership()

    membership = await harness.service.join_channel(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
    )

    assert membership == existing
    assert harness.repository.commit_attempt_count == 0
    assert harness.repository.rollback_count == 1


async def test_join_rejects_archived_channel_for_new_membership(
    harness: ServiceHarness,
) -> None:
    harness.seed_workspace_membership(role=WorkspaceRole.MEMBER)
    harness.seed_channel(archived=True)

    with pytest.raises(ChannelArchived):
        await harness.service.join_channel(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
        )

    assert harness.repository.rollback_count == 1


async def test_join_missing_channel_rolls_back_without_membership_lookup(
    harness: ServiceHarness,
) -> None:
    harness.seed_workspace_membership(role=WorkspaceRole.MEMBER)

    with pytest.raises(ChannelNotFound):
        await harness.service.join_channel(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
        )

    assert harness.repository.membership_gets == []
    assert harness.repository.rollback_count == 1


@pytest.mark.parametrize("operation", ["add_membership", "commit"])
async def test_join_recovers_idempotently_when_concurrent_put_wins(operation: str) -> None:
    harness = ServiceHarness()
    harness.seed_workspace_membership(role=WorkspaceRole.MEMBER)
    harness.seed_channel()
    race_winner = ChannelMembership(
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        user_id=ACTOR_ID,
        added_by_user_id=OTHER_OWNER_ID,
        joined_at=NOW - timedelta(microseconds=1),
    )
    harness.repository.conflict_operation = operation
    harness.repository.race_membership = race_winner

    membership = await harness.service.join_channel(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
    )

    assert membership == race_winner
    assert harness.repository.membership_gets == [
        (CHANNEL_ID, ACTOR_ID, True),
        (CHANNEL_ID, ACTOR_ID, False),
    ]
    assert harness.repository.rollback_count == 1
    assert harness.repository.commit_count == 0


@pytest.mark.parametrize("operation", ["add_membership", "commit"])
async def test_join_re_raises_unexplained_membership_conflict(operation: str) -> None:
    harness = ServiceHarness()
    harness.seed_workspace_membership(role=WorkspaceRole.MEMBER)
    harness.seed_channel()
    harness.repository.conflict_operation = operation

    with pytest.raises(ChannelConflict):
        await harness.service.join_channel(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
        )

    assert harness.repository.rollback_count == 1
    assert harness.repository.membership_gets[-1] == (CHANNEL_ID, ACTOR_ID, False)


@pytest.mark.parametrize("archived", [False, True])
async def test_leave_removes_own_membership_including_from_archived_channel(
    archived: bool,
) -> None:
    harness = ServiceHarness()
    harness.seed_workspace_membership(role=WorkspaceRole.MEMBER)
    harness.seed_channel(visibility=ChannelVisibility.PRIVATE, archived=archived)
    harness.seed_channel_membership()

    await harness.service.leave_channel(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
    )

    assert (CHANNEL_ID, ACTOR_ID) not in harness.repository.memberships
    assert harness.repository.membership_removals == [(CHANNEL_ID, ACTOR_ID)]
    assert harness.events == [
        "workspace",
        f"actor-workspace:{ACTOR_ID.int}",
        f"channel:{CHANNEL_ID.int}",
        f"channel-membership:{ACTOR_ID.int}",
    ]
    assert harness.repository.commit_count == 1


@pytest.mark.parametrize(
    ("role", "visibility", "expected_error"),
    [
        (WorkspaceRole.MEMBER, ChannelVisibility.PUBLIC, ChannelMembershipNotFound),
        (WorkspaceRole.MEMBER, ChannelVisibility.PRIVATE, ChannelNotFound),
        (WorkspaceRole.ADMIN, ChannelVisibility.PRIVATE, ChannelMembershipNotFound),
    ],
)
async def test_leave_absent_membership_respects_visibility_masking(
    role: WorkspaceRole,
    visibility: ChannelVisibility,
    expected_error: type[Exception],
) -> None:
    harness = ServiceHarness()
    harness.seed_workspace_membership(role=role)
    harness.seed_channel(visibility=visibility)

    with pytest.raises(expected_error):
        await harness.service.leave_channel(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
        )

    assert harness.repository.rollback_count == 1
    assert harness.repository.commit_attempt_count == 0


@pytest.mark.parametrize("role", [WorkspaceRole.OWNER, WorkspaceRole.ADMIN])
async def test_managers_add_current_workspace_member_with_stable_lock_order(
    role: WorkspaceRole,
) -> None:
    harness = ServiceHarness()
    harness.seed_workspace_membership(role=role)
    harness.seed_workspace_membership(user_id=TARGET_ID, role=WorkspaceRole.MEMBER)
    harness.seed_channel(visibility=ChannelVisibility.PRIVATE)

    membership = await harness.service.add_membership(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        target_user_id=TARGET_ID,
    )

    assert membership == ChannelMembership(
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        user_id=TARGET_ID,
        added_by_user_id=ACTOR_ID,
        joined_at=NOW,
    )
    assert harness.repository.memberships[(CHANNEL_ID, TARGET_ID)] == membership
    assert harness.workspace_access.require_calls == [(ACTOR_ID, WORKSPACE_ID, True)]
    assert harness.workspace_access.get_calls == [(WORKSPACE_ID, TARGET_ID, True)]
    assert harness.repository.channel_gets == [(WORKSPACE_ID, CHANNEL_ID, True)]
    assert harness.repository.membership_gets == [(CHANNEL_ID, TARGET_ID, True)]
    assert harness.events == [
        "workspace",
        f"actor-workspace:{ACTOR_ID.int}",
        f"channel:{CHANNEL_ID.int}",
        f"target-workspace:{TARGET_ID.int}",
        f"channel-membership:{TARGET_ID.int}",
    ]
    assert harness.repository.commit_count == 1


async def test_manager_can_add_self_without_relocking_workspace_membership(
    harness: ServiceHarness,
) -> None:
    harness.seed_workspace_membership(role=WorkspaceRole.ADMIN)
    harness.seed_channel(visibility=ChannelVisibility.PRIVATE)

    membership = await harness.service.add_membership(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        target_user_id=ACTOR_ID,
    )

    assert membership.user_id == ACTOR_ID
    assert membership.added_by_user_id == ACTOR_ID
    assert harness.workspace_access.get_calls == []
    assert harness.events == [
        "workspace",
        f"actor-workspace:{ACTOR_ID.int}",
        f"channel:{CHANNEL_ID.int}",
        f"channel-membership:{ACTOR_ID.int}",
    ]


async def test_non_manager_cannot_add_channel_membership(harness: ServiceHarness) -> None:
    harness.seed_workspace_membership(role=WorkspaceRole.MEMBER)
    harness.seed_workspace_membership(user_id=TARGET_ID, role=WorkspaceRole.MEMBER)
    harness.seed_channel()

    with pytest.raises(ChannelPermissionDenied):
        await harness.service.add_membership(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            target_user_id=TARGET_ID,
        )

    assert harness.repository.channel_gets == []
    assert harness.workspace_access.get_calls == []
    assert harness.repository.rollback_count == 1


async def test_add_membership_rejects_archived_channel_before_target_lock(
    harness: ServiceHarness,
) -> None:
    harness.seed_workspace_membership()
    harness.seed_workspace_membership(user_id=TARGET_ID, role=WorkspaceRole.MEMBER)
    harness.seed_channel(archived=True)

    with pytest.raises(ChannelArchived):
        await harness.service.add_membership(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            target_user_id=TARGET_ID,
        )

    assert harness.workspace_access.get_calls == []
    assert harness.repository.membership_gets == []
    assert harness.repository.rollback_count == 1


async def test_add_membership_rejects_non_workspace_target(harness: ServiceHarness) -> None:
    harness.seed_workspace_membership()
    harness.seed_channel()

    with pytest.raises(TargetWorkspaceMembershipNotFound):
        await harness.service.add_membership(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            target_user_id=OUTSIDER_ID,
        )

    assert harness.workspace_access.get_calls == [(WORKSPACE_ID, OUTSIDER_ID, True)]
    assert harness.repository.membership_gets == []
    assert harness.repository.rollback_count == 1


async def test_add_existing_membership_is_idempotent(harness: ServiceHarness) -> None:
    harness.seed_workspace_membership()
    harness.seed_workspace_membership(user_id=TARGET_ID, role=WorkspaceRole.MEMBER)
    harness.seed_channel()
    existing = harness.seed_channel_membership(
        user_id=TARGET_ID,
        added_by_user_id=OTHER_OWNER_ID,
    )

    membership = await harness.service.add_membership(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        target_user_id=TARGET_ID,
    )

    assert membership == existing
    assert harness.repository.commit_attempt_count == 0
    assert harness.repository.rollback_count == 1


@pytest.mark.parametrize("operation", ["add_membership", "commit"])
async def test_add_membership_recovers_when_concurrent_put_wins(operation: str) -> None:
    harness = ServiceHarness()
    harness.seed_workspace_membership()
    harness.seed_workspace_membership(user_id=TARGET_ID, role=WorkspaceRole.MEMBER)
    harness.seed_channel()
    race_winner = ChannelMembership(
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        user_id=TARGET_ID,
        added_by_user_id=OTHER_ADMIN_ID,
        joined_at=NOW - timedelta(microseconds=1),
    )
    harness.repository.conflict_operation = operation
    harness.repository.race_membership = race_winner

    membership = await harness.service.add_membership(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        target_user_id=TARGET_ID,
    )

    assert membership == race_winner
    assert harness.repository.membership_gets == [
        (CHANNEL_ID, TARGET_ID, True),
        (CHANNEL_ID, TARGET_ID, False),
    ]
    assert harness.repository.rollback_count == 1


async def test_add_membership_masks_missing_channel_before_target_lookup(
    harness: ServiceHarness,
) -> None:
    harness.seed_workspace_membership()
    harness.seed_workspace_membership(user_id=TARGET_ID, role=WorkspaceRole.MEMBER)

    with pytest.raises(ChannelNotFound):
        await harness.service.add_membership(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            target_user_id=TARGET_ID,
        )

    assert harness.workspace_access.get_calls == []
    assert harness.repository.rollback_count == 1


@pytest.mark.parametrize(
    "target_role",
    [WorkspaceRole.OWNER, WorkspaceRole.ADMIN, WorkspaceRole.MEMBER],
)
async def test_owner_can_remove_any_channel_member(target_role: WorkspaceRole) -> None:
    harness = ServiceHarness()
    harness.seed_workspace_membership(role=WorkspaceRole.OWNER)
    harness.seed_workspace_membership(user_id=TARGET_ID, role=target_role)
    harness.seed_channel(archived=True)
    harness.seed_channel_membership(user_id=TARGET_ID)

    await harness.service.remove_membership(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        target_user_id=TARGET_ID,
    )

    assert (CHANNEL_ID, TARGET_ID) not in harness.repository.memberships
    assert harness.repository.commit_count == 1
    assert harness.events == [
        "workspace",
        f"actor-workspace:{ACTOR_ID.int}",
        f"channel:{CHANNEL_ID.int}",
        f"target-workspace:{TARGET_ID.int}",
        f"channel-membership:{TARGET_ID.int}",
    ]


async def test_admin_can_remove_ordinary_member(harness: ServiceHarness) -> None:
    harness.seed_workspace_membership(role=WorkspaceRole.ADMIN)
    harness.seed_workspace_membership(user_id=TARGET_ID, role=WorkspaceRole.MEMBER)
    harness.seed_channel()
    harness.seed_channel_membership(user_id=TARGET_ID)

    await harness.service.remove_membership(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        target_user_id=TARGET_ID,
    )

    assert (CHANNEL_ID, TARGET_ID) not in harness.repository.memberships
    assert harness.repository.commit_count == 1


@pytest.mark.parametrize("target_role", [WorkspaceRole.OWNER, WorkspaceRole.ADMIN])
async def test_admin_cannot_remove_owner_or_admin_channel_membership(
    target_role: WorkspaceRole,
) -> None:
    harness = ServiceHarness()
    harness.seed_workspace_membership(role=WorkspaceRole.ADMIN)
    harness.seed_workspace_membership(user_id=TARGET_ID, role=target_role)
    harness.seed_channel()
    original = harness.seed_channel_membership(user_id=TARGET_ID)

    with pytest.raises(ChannelPermissionDenied):
        await harness.service.remove_membership(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            target_user_id=TARGET_ID,
        )

    assert harness.repository.memberships[(CHANNEL_ID, TARGET_ID)] == original
    assert harness.repository.membership_gets == []
    assert harness.repository.rollback_count == 1


async def test_admin_privileged_self_removal_is_denied_but_leave_remains_available(
    harness: ServiceHarness,
) -> None:
    harness.seed_workspace_membership(role=WorkspaceRole.ADMIN)
    harness.seed_channel(visibility=ChannelVisibility.PRIVATE)
    harness.seed_channel_membership()

    with pytest.raises(ChannelPermissionDenied):
        await harness.service.remove_membership(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            target_user_id=ACTOR_ID,
        )

    await harness.service.leave_channel(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
    )

    assert (CHANNEL_ID, ACTOR_ID) not in harness.repository.memberships
    assert harness.workspace_access.get_calls == []
    assert harness.repository.rollback_count == 1
    assert harness.repository.commit_count == 1


async def test_non_manager_cannot_remove_channel_membership(harness: ServiceHarness) -> None:
    harness.seed_workspace_membership(role=WorkspaceRole.MEMBER)
    harness.seed_workspace_membership(user_id=TARGET_ID, role=WorkspaceRole.MEMBER)
    harness.seed_channel()
    harness.seed_channel_membership(user_id=TARGET_ID)

    with pytest.raises(ChannelPermissionDenied):
        await harness.service.remove_membership(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            target_user_id=TARGET_ID,
        )

    assert harness.repository.channel_gets == []
    assert harness.workspace_access.get_calls == []
    assert harness.repository.rollback_count == 1


async def test_remove_membership_rejects_non_workspace_target(
    harness: ServiceHarness,
) -> None:
    harness.seed_workspace_membership()
    harness.seed_channel()

    with pytest.raises(TargetWorkspaceMembershipNotFound):
        await harness.service.remove_membership(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            target_user_id=OUTSIDER_ID,
        )

    assert harness.repository.membership_gets == []
    assert harness.repository.rollback_count == 1


async def test_remove_membership_rejects_missing_channel_membership(
    harness: ServiceHarness,
) -> None:
    harness.seed_workspace_membership()
    harness.seed_workspace_membership(user_id=TARGET_ID, role=WorkspaceRole.MEMBER)
    harness.seed_channel()

    with pytest.raises(ChannelMembershipNotFound):
        await harness.service.remove_membership(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            target_user_id=TARGET_ID,
        )

    assert harness.repository.membership_gets == [(CHANNEL_ID, TARGET_ID, True)]
    assert harness.repository.rollback_count == 1


async def test_remove_membership_masks_missing_channel_before_target_lock(
    harness: ServiceHarness,
) -> None:
    harness.seed_workspace_membership()
    harness.seed_workspace_membership(user_id=TARGET_ID, role=WorkspaceRole.MEMBER)

    with pytest.raises(ChannelNotFound):
        await harness.service.remove_membership(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            target_user_id=TARGET_ID,
        )

    assert harness.workspace_access.get_calls == []
    assert harness.repository.rollback_count == 1

"""Message application authorization and transaction contracts."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from klack.modules.channels.application.service import ChannelContentAccessService
from klack.modules.channels.domain.entities import (
    Channel,
    ChannelMembership,
    ChannelVisibility,
)
from klack.modules.channels.domain.errors import (
    ChannelArchived,
    ChannelNotFound,
    ChannelPermissionDenied,
)
from klack.modules.messaging.application.service import (
    MessagePolicy,
    MessageService,
    utc_now,
)
from klack.modules.messaging.domain.entities import Message
from klack.modules.messaging.domain.errors import (
    InvalidMessageBody,
    InvalidMessageCursor,
    MessageDeleted,
    MessageNotFound,
    MessagePermissionDenied,
)
from klack.modules.workspaces.domain.entities import WorkspaceMembership, WorkspaceRole

NOW = datetime(2026, 9, 11, 16, 0, tzinfo=UTC)
ACTOR_ID = UUID(int=1)
OTHER_ID = UUID(int=2)
WORKSPACE_ID = UUID(int=10)
CHANNEL_ID = UUID(int=20)
MESSAGE_ID = UUID(int=30)
SECOND_MESSAGE_ID = UUID(int=31)


def make_channel(*, archived: bool = False) -> Channel:
    return Channel(
        id=CHANNEL_ID,
        workspace_id=WORKSPACE_ID,
        name="general",
        visibility=ChannelVisibility.PUBLIC,
        created_by_user_id=ACTOR_ID,
        created_at=NOW - timedelta(days=1),
        updated_at=NOW,
        archived_at=NOW if archived else None,
        archived_by_user_id=ACTOR_ID if archived else None,
    )


def make_message(
    message_id: UUID = MESSAGE_ID,
    *,
    author_id: UUID = ACTOR_ID,
    body: str | None = "hello",
    offset: int = 0,
    edited_at: datetime | None = None,
    deleted_at: datetime | None = None,
) -> Message:
    return Message(
        id=message_id,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        author_user_id=author_id,
        body=body,
        created_at=NOW + timedelta(seconds=offset),
        edited_at=edited_at,
        deleted_at=deleted_at,
    )


class FakeChannelAccess:
    def __init__(self, channel: Channel | None = None) -> None:
        self.channel = channel or make_channel()
        self.calls: list[tuple[UUID, UUID, UUID, bool]] = []

    async def require_access(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
        for_update: bool,
    ) -> Channel:
        self.calls.append((actor_user_id, workspace_id, channel_id, for_update))
        return self.channel


class MemoryMessageRepository:
    def __init__(self) -> None:
        self.messages: dict[UUID, Message] = {}
        self.commit_count = 0
        self.rollback_count = 0
        self.list_calls: list[tuple[UUID, datetime | None, UUID | None, int]] = []

    async def add_message(self, message: Message) -> None:
        self.messages[message.id] = message

    async def get_message(
        self,
        *,
        workspace_id: UUID,
        channel_id: UUID,
        message_id: UUID,
        for_update: bool = False,
    ) -> Message | None:
        del for_update
        message = self.messages.get(message_id)
        if message is None or (message.workspace_id, message.channel_id) != (
            workspace_id,
            channel_id,
        ):
            return None
        return message

    async def list_messages(
        self,
        *,
        channel_id: UUID,
        before_created_at: datetime | None,
        before_message_id: UUID | None,
        limit: int,
    ) -> list[Message]:
        self.list_calls.append((channel_id, before_created_at, before_message_id, limit))
        rows = [message for message in self.messages.values() if message.channel_id == channel_id]
        rows.sort(key=lambda message: (message.created_at, message.id.int), reverse=True)
        if before_created_at is not None and before_message_id is not None:
            rows = [
                message
                for message in rows
                if (message.created_at, message.id.int) < (before_created_at, before_message_id.int)
            ]
        return rows[:limit]

    async def update_message(
        self,
        *,
        message_id: UUID,
        body: str | None,
        edited_at: datetime | None,
        deleted_at: datetime | None,
    ) -> None:
        self.messages[message_id] = replace(
            self.messages[message_id],
            body=body,
            edited_at=edited_at,
            deleted_at=deleted_at,
        )

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1


def service(
    repository: MemoryMessageRepository,
    access: FakeChannelAccess | None = None,
) -> MessageService:
    return MessageService(
        repository=repository,
        channel_access=access or FakeChannelAccess(),
        clock=lambda: NOW,
        uuid_factory=lambda: MESSAGE_ID,
    )


def test_policy_and_clock_contracts() -> None:
    assert utc_now().tzinfo is UTC
    assert MessagePolicy().default_page_size == 50
    with pytest.raises(ValueError, match="maximum_body_length"):
        MessagePolicy(maximum_body_length=0)
    with pytest.raises(ValueError, match="default_page_size"):
        MessagePolicy(default_page_size=101)


@pytest.mark.parametrize("body", ["", " \n\t", "x" * 4_001])
async def test_create_rejects_invalid_body_before_authorization(body: str) -> None:
    repository = MemoryMessageRepository()
    access = FakeChannelAccess()
    with pytest.raises(InvalidMessageBody):
        await service(repository, access).create_message(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            body=body,
        )
    assert access.calls == []


async def test_create_preserves_body_and_commits_after_locked_access() -> None:
    repository = MemoryMessageRepository()
    access = FakeChannelAccess()
    message = await service(repository, access).create_message(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        body="  hello\nworld  ",
    )
    assert message.body == "  hello\nworld  "
    assert message.created_at == NOW
    assert repository.messages[MESSAGE_ID] == message
    assert repository.commit_count == 1
    assert access.calls == [(ACTOR_ID, WORKSPACE_ID, CHANNEL_ID, True)]


async def test_create_rejects_archived_channel_and_rolls_back() -> None:
    repository = MemoryMessageRepository()
    with pytest.raises(ChannelArchived):
        await service(repository, FakeChannelAccess(make_channel(archived=True))).create_message(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            body="closed",
        )
    assert repository.rollback_count == 1
    assert repository.messages == {}


async def test_history_uses_cursor_and_returns_continuation() -> None:
    repository = MemoryMessageRepository()
    repository.messages = {
        UUID(int=index): make_message(UUID(int=index), offset=index) for index in range(1, 5)
    }
    first = await service(repository).list_messages(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        limit=2,
    )
    assert [message.id.int for message in first.messages] == [4, 3]
    assert first.next_before == UUID(int=3)
    second = await service(repository).list_messages(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        before=first.next_before,
        limit=2,
    )
    assert [message.id.int for message in second.messages] == [2, 1]
    assert second.next_before is None
    assert repository.list_calls[-1] == (CHANNEL_ID, NOW + timedelta(seconds=3), UUID(int=3), 3)


async def test_history_uses_default_limit_and_rejects_bad_inputs() -> None:
    repository = MemoryMessageRepository()
    page = await service(repository).list_messages(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
    )
    assert page.messages == []
    assert repository.list_calls[-1][-1] == 51
    with pytest.raises(InvalidMessageCursor):
        await service(repository).list_messages(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            before=UUID(int=999),
        )
    with pytest.raises(ValueError, match="limit"):
        await service(repository).list_messages(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            limit=101,
        )


async def test_edit_updates_owned_live_message_and_handles_noop() -> None:
    repository = MemoryMessageRepository()
    repository.messages[MESSAGE_ID] = make_message()
    edited = await service(repository).edit_message(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        message_id=MESSAGE_ID,
        body="updated",
    )
    assert edited.body == "updated"
    assert edited.edited_at == NOW
    assert repository.commit_count == 1
    same = await service(repository).edit_message(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        message_id=MESSAGE_ID,
        body="updated",
    )
    assert same == edited
    assert repository.rollback_count == 1


async def test_edit_rejects_archived_deleted_missing_and_foreign_messages() -> None:
    repository = MemoryMessageRepository()
    repository.messages[MESSAGE_ID] = make_message()
    with pytest.raises(ChannelArchived):
        await service(repository, FakeChannelAccess(make_channel(archived=True))).edit_message(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            message_id=MESSAGE_ID,
            body="updated",
        )
    repository.messages[MESSAGE_ID] = make_message(body=None, deleted_at=NOW)
    with pytest.raises(MessageDeleted):
        await service(repository).edit_message(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            message_id=MESSAGE_ID,
            body="updated",
        )
    repository.messages[MESSAGE_ID] = make_message(author_id=OTHER_ID)
    with pytest.raises(MessagePermissionDenied):
        await service(repository).edit_message(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            message_id=MESSAGE_ID,
            body="updated",
        )
    with pytest.raises(MessageNotFound):
        await service(repository).edit_message(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            message_id=SECOND_MESSAGE_ID,
            body="updated",
        )


async def test_delete_soft_deletes_and_is_idempotent_even_when_archived() -> None:
    repository = MemoryMessageRepository()
    repository.messages[MESSAGE_ID] = make_message(edited_at=NOW - timedelta(seconds=1))
    subject = service(repository, FakeChannelAccess(make_channel(archived=True)))
    await subject.delete_message(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        message_id=MESSAGE_ID,
    )
    deleted = repository.messages[MESSAGE_ID]
    assert deleted.body is None
    assert deleted.deleted_at == NOW
    assert deleted.edited_at == NOW - timedelta(seconds=1)
    assert repository.commit_count == 1
    await subject.delete_message(
        actor_user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        message_id=MESSAGE_ID,
    )
    assert repository.commit_count == 1
    assert repository.rollback_count == 1


async def test_delete_requires_an_existing_owned_message() -> None:
    repository = MemoryMessageRepository()
    with pytest.raises(MessageNotFound):
        await service(repository).delete_message(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            message_id=MESSAGE_ID,
        )
    repository.messages[MESSAGE_ID] = make_message(author_id=OTHER_ID)
    with pytest.raises(MessagePermissionDenied):
        await service(repository).delete_message(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            message_id=MESSAGE_ID,
        )


class WorkspaceAccessFake:
    def __init__(self, role: WorkspaceRole) -> None:
        self.role = role

    async def require_membership(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        for_update: bool,
    ) -> WorkspaceMembership:
        del for_update
        return WorkspaceMembership(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            role=self.role,
            joined_at=NOW,
        )


class ChannelRepositoryFake:
    def __init__(self, channel: Channel | None, membership: ChannelMembership | None) -> None:
        self.channel = channel
        self.membership = membership
        self.rollback_count = 0
        self.calls: list[str] = []

    async def get_channel(self, **_kwargs: object) -> Channel | None:
        self.calls.append("channel")
        return self.channel

    async def get_membership(self, **_kwargs: object) -> ChannelMembership | None:
        self.calls.append("membership")
        return self.membership

    async def rollback(self) -> None:
        self.rollback_count += 1


def membership() -> ChannelMembership:
    return ChannelMembership(
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        user_id=ACTOR_ID,
        added_by_user_id=ACTOR_ID,
        joined_at=NOW,
    )


@pytest.mark.parametrize("for_update", [False, True])
async def test_content_access_returns_channel_only_for_explicit_member(for_update: bool) -> None:
    repository = ChannelRepositoryFake(make_channel(), membership())
    access = ChannelContentAccessService(
        repository=repository,  # type: ignore[arg-type]
        workspace_access=WorkspaceAccessFake(WorkspaceRole.MEMBER),
    )
    assert (
        await access.require_access(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            for_update=for_update,
        )
        == make_channel()
    )
    assert repository.calls == ["channel", "membership"]


async def test_content_access_masks_hidden_private_channel() -> None:
    private = replace(make_channel(), visibility=ChannelVisibility.PRIVATE)
    repository = ChannelRepositoryFake(private, None)
    access = ChannelContentAccessService(
        repository=repository,  # type: ignore[arg-type]
        workspace_access=WorkspaceAccessFake(WorkspaceRole.MEMBER),
    )
    with pytest.raises(ChannelNotFound):
        await access.require_access(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            for_update=True,
        )
    assert repository.rollback_count == 1


@pytest.mark.parametrize("role", [WorkspaceRole.MEMBER, WorkspaceRole.ADMIN, WorkspaceRole.OWNER])
async def test_visible_nonmember_cannot_access_content(role: WorkspaceRole) -> None:
    repository = ChannelRepositoryFake(make_channel(), None)
    access = ChannelContentAccessService(
        repository=repository,  # type: ignore[arg-type]
        workspace_access=WorkspaceAccessFake(role),
    )
    with pytest.raises(ChannelPermissionDenied):
        await access.require_access(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            for_update=False,
        )
    assert repository.rollback_count == 0


async def test_content_access_masks_missing_channel_and_rolls_back_locked_read() -> None:
    repository = ChannelRepositoryFake(None, None)
    access = ChannelContentAccessService(
        repository=repository,  # type: ignore[arg-type]
        workspace_access=WorkspaceAccessFake(WorkspaceRole.OWNER),
    )
    with pytest.raises(ChannelNotFound):
        await access.require_access(
            actor_user_id=ACTOR_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            for_update=True,
        )
    assert repository.calls == ["channel"]
    assert repository.rollback_count == 1

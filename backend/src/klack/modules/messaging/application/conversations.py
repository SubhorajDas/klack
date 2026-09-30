"""Authorized conversation interactions over durable channel membership."""

from uuid import UUID

from klack.modules.channels.application.ports import WorkspaceAccessGateway
from klack.modules.channels.application.service import ChannelView
from klack.modules.channels.domain.errors import ChannelArchived, TargetWorkspaceMembershipNotFound
from klack.modules.messaging.application.ports import (
    ChannelContentAccessGateway,
    ConversationRepository,
    MessageEventWriter,
    MessageRepository,
)
from klack.modules.messaging.domain.entities import Message
from klack.modules.messaging.domain.errors import (
    InvalidMessageBody,
    MessageDeleted,
    MessageNotFound,
)

REACTIONS = frozenset({"👍", "❤️", "😂", "🎉", "👀", "✅"})


class ConversationService:
    def __init__(
        self,
        repository: ConversationRepository,
        messages: MessageRepository,
        access: ChannelContentAccessGateway,
        workspaces: WorkspaceAccessGateway,
        events: MessageEventWriter,
    ) -> None:
        self.repository = repository
        self.messages = messages
        self.access = access
        self.workspaces = workspaces
        self.events = events

    async def open_direct(self, workspace_id: UUID, actor: UUID, target: UUID) -> ChannelView:
        # The workspace lock serializes pair creation with removal and reverse-order requests.
        await self.workspaces.require_membership(
            actor_user_id=actor, workspace_id=workspace_id, for_update=True
        )
        if actor == target:
            raise InvalidMessageBody
        if (
            await self.workspaces.get_membership(
                workspace_id=workspace_id, user_id=target, for_update=True
            )
            is None
        ):
            raise TargetWorkspaceMembershipNotFound
        channel = await self.repository.direct(workspace_id, actor, target)
        await self.repository.commit()
        return ChannelView(channel=channel, is_member=True)

    async def list_direct(self, workspace_id: UUID, actor: UUID) -> list[ChannelView]:
        await self.workspaces.require_membership(
            actor_user_id=actor, workspace_id=workspace_id, for_update=False
        )
        return [
            ChannelView(channel=c, is_member=True)
            for c in await self.repository.list_direct(workspace_id, actor)
        ]

    async def alerts(
        self, workspace_id: UUID, actor: UUID
    ) -> list[tuple[ChannelView, Message, int]]:
        await self.workspaces.require_membership(
            actor_user_id=actor, workspace_id=workspace_id, for_update=False
        )
        return [
            (ChannelView(channel=channel, is_member=True), message, count)
            for channel, message, count in await self.repository.alerts(workspace_id, actor)
        ]

    async def react(
        self,
        workspace_id: UUID,
        channel_id: UUID,
        actor: UUID,
        message_id: UUID,
        emoji: str,
        add: bool,
    ) -> Message:
        channel = await self.access.require_access(
            actor_user_id=actor, workspace_id=workspace_id, channel_id=channel_id, for_update=True
        )
        if channel.is_archived:
            raise ChannelArchived
        if emoji not in REACTIONS:
            raise InvalidMessageBody
        message = await self.messages.get_message(
            workspace_id=workspace_id, channel_id=channel_id, message_id=message_id, for_update=True
        )
        if message is None:
            raise MessageNotFound
        if message.is_deleted:
            raise MessageDeleted
        if await self.repository.reaction(message_id, actor, emoji, add):
            await self.messages.update_message(
                message_id=message_id,
                body=message.body,
                edited_at=message.edited_at,
                deleted_at=None,
                revision=message.revision + 1,
            )
            message = await self.messages.get_message(
                workspace_id=workspace_id, channel_id=channel_id, message_id=message_id
            )
            assert message is not None
            await self.events.append_message_changed(message)
        await self.repository.commit()
        return message

    async def read_state(
        self, workspace_id: UUID, channel_id: UUID, actor: UUID, message_id: UUID | None = None
    ) -> tuple[UUID | None, int]:
        await self.access.require_access(
            actor_user_id=actor,
            workspace_id=workspace_id,
            channel_id=channel_id,
            for_update=message_id is not None,
        )
        if message_id is not None:
            message = await self.messages.get_message(
                workspace_id=workspace_id, channel_id=channel_id, message_id=message_id
            )
            if message is None:
                raise MessageNotFound
            await self.repository.advance(channel_id, actor, message_id)
        state = await self.repository.read_state(channel_id, actor)
        if message_id is not None:
            await self.repository.commit()
        return state

"""Strict inbound realtime protocol messages."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class RealtimeCommand(BaseModel):
    """Reject protocol extensions that were not negotiated."""

    model_config = ConfigDict(extra="forbid")


class SubscribeCommand(RealtimeCommand):
    type: Literal["subscribe"]
    request_id: UUID
    workspace_id: UUID
    channel_id: UUID


class UnsubscribeCommand(RealtimeCommand):
    type: Literal["unsubscribe"]
    request_id: UUID
    channel_id: UUID


class PongCommand(RealtimeCommand):
    type: Literal["pong"]


InboundCommand = Annotated[
    SubscribeCommand | UnsubscribeCommand | PongCommand,
    Field(discriminator="type"),
]
inbound_command_adapter: TypeAdapter[InboundCommand] = TypeAdapter(InboundCommand)

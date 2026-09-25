"""Persistence-independent realtime event values."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RealtimeEvent:
    """A committed signal pointing at current durable entity state."""

    id: UUID
    event_type: str
    workspace_id: UUID
    channel_id: UUID
    entity_id: UUID
    entity_revision: int
    occurred_at: datetime

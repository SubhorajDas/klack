"""Realtime wire command validation."""

import pytest
from pydantic import ValidationError

from klack.modules.realtime.api.schemas import SubscribeCommand, inbound_command_adapter


def test_subscribe_command_is_strict_and_typed() -> None:
    command = inbound_command_adapter.validate_json(
        '{"type":"subscribe","request_id":"00000000-0000-0000-0000-000000000001",'
        '"workspace_id":"00000000-0000-0000-0000-000000000002",'
        '"channel_id":"00000000-0000-0000-0000-000000000003"}',
    )
    assert isinstance(command, SubscribeCommand)

    with pytest.raises(ValidationError):
        inbound_command_adapter.validate_json(
            '{"type":"subscribe","request_id":"bad","workspace_id":"bad",'
            '"channel_id":"bad","extra":true}',
        )

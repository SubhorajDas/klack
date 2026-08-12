"""Structured logging behavior."""

import json

import structlog

from klack.core.config import LogFormat, Settings
from klack.core.logging import configure_logging


def test_json_logging_has_common_context(settings: Settings, capsys: object) -> None:
    configure_logging(settings)
    structlog.get_logger("test.logger").info("example_event", answer=42)

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    event = json.loads(captured.out.strip())

    assert event["event"] == "example_event"
    assert event["answer"] == 42
    assert event["level"] == "info"
    assert event["logger"] == "test.logger"
    assert event["service"] == settings.app_name
    assert event["environment"] == settings.app_env.value
    assert event["release"] == settings.app_version
    assert event["timestamp"].endswith("Z")


def test_logging_configuration_is_idempotent(settings: Settings, capsys: object) -> None:
    configure_logging(settings)
    configure_logging(settings)
    structlog.get_logger("test.logger").info("one_event")

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    lines = [line for line in captured.out.splitlines() if line]
    assert len(lines) == 1


def test_console_logging_is_human_readable(settings: Settings, capsys: object) -> None:
    console_settings = settings.model_copy(update={"log_format": LogFormat.CONSOLE})
    configure_logging(console_settings)
    structlog.get_logger("test.logger").info("console_event")

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "console_event" in captured.out
    assert not captured.out.lstrip().startswith("{")

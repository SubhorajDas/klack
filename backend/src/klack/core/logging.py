"""Application-wide structured logging configuration."""

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

from klack.core.config import LogFormat, Settings

EventDict = MutableMapping[str, Any]


def configure_logging(settings: Settings) -> None:
    """Configure structlog and standard-library logs through one renderer.

    The function is intentionally idempotent because test suites and process factories may
    construct more than one application in the same interpreter.
    """

    def add_application_context(
        _logger: logging.Logger,
        _method_name: str,
        event_dict: EventDict,
    ) -> EventDict:
        event_dict.setdefault("service", settings.app_name)
        event_dict.setdefault("environment", settings.app_env.value)
        event_dict.setdefault("release", settings.app_version)
        return event_dict

    common_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        add_application_context,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
    ]

    renderer: structlog.types.Processor
    if settings.log_format is LogFormat.JSON:
        renderer = structlog.processors.JSONRenderer(sort_keys=True)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=common_processors,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(settings.log_level.value)

    for logger_name in ("uvicorn", "uvicorn.error", "sqlalchemy"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = True

    logging.getLogger("uvicorn.access").disabled = True

    structlog.configure(
        processors=[
            *common_processors,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

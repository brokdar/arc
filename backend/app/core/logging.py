"""structlog configuration: pretty console output in dev, JSON in production."""

import logging

import structlog

from app.core.config import get_settings


def configure_logging() -> None:
    """Configure structlog and the stdlib root logger."""
    settings = get_settings()
    level = getattr(logging, settings.log.level)

    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if settings.environment == "production"
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=level, format="%(message)s")


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a named bound logger."""
    return structlog.get_logger(name)

"""ARQ worker entrypoint: ``arq app.worker.main.WorkerSettings``."""

from typing import Any

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.worker.tasks import example_task

logger = get_logger(__name__)


async def startup(_ctx: dict[str, Any]) -> None:
    """Worker startup hook."""
    configure_logging()
    logger.info("worker_started")


async def shutdown(_ctx: dict[str, Any]) -> None:
    """Worker shutdown hook."""
    logger.info("worker_stopped")


class WorkerSettings:
    """ARQ worker configuration."""

    functions = [example_task]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis.url)

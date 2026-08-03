"""Background tasks executed by the ARQ worker.

Add a function here, register it in ``WorkerSettings.functions`` in
``app/worker/main.py``, and enqueue it from the API with
``await redis.enqueue_job("example_task", ...)``.
"""

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


async def example_task(ctx: dict[str, Any], name: str) -> str:
    """Example task — replace with your first real background job."""
    logger.info("example_task_ran", name=name, job_id=ctx.get("job_id"))
    return f"hello {name}"

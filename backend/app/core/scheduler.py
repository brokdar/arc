"""In-process job scheduler.

Scheduling deliberately runs inside the API process (APScheduler's
``AsyncIOScheduler`` on the app's own event loop) rather than in a separate
worker backed by Redis or Celery. This is a single-user application: the
scheduled workload is periodic ingest and maintenance, which needs neither a
distributed queue, multiple consumers, nor cross-process durability. See
``docs/decisions.md`` (D5).

No jobs are registered yet — they arrive with the work packages that need
them: ingest polling in WP-4 and the backup job in WP-9. Each of those adds
its own ``scheduler.add_job(...)`` call rather than registering everything
here.
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.logging import get_logger

logger = get_logger(__name__)


def create_scheduler() -> AsyncIOScheduler:
    """Build and start the application scheduler.

    Returns:
        A started ``AsyncIOScheduler`` bound to the running event loop.
    """
    scheduler = AsyncIOScheduler()
    scheduler.start()
    logger.info("scheduler_started", jobs=len(scheduler.get_jobs()))
    return scheduler

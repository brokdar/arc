"""In-process job scheduler.

Scheduling deliberately runs inside the API process (APScheduler's
``AsyncIOScheduler`` on the app's own event loop) rather than in a separate
worker backed by Redis or Celery. This is a single-user application: the
scheduled workload is periodic ingest and maintenance, which needs neither a
distributed queue, multiple consumers, nor cross-process durability. An
in-process scheduler removes a whole stateful service from Compose, from
deployment and from the failure surface, while covering every job this
application actually schedules.

Jobs are **not** registered here. The module that owns the work exposes a
``register_*_job(scheduler)`` function making its own ``add_job`` call, and
``app.main`` calls them all during startup — inbox polling
(``app.ingest.inbox``), the missed-session sweep (``app.services.matching``),
prompt expiry (``app.services.scoring``) and proposal expiry
(``app.services.proposals``). A job list here would have to import every one
of those, inverting the dependency direction the layering exists to keep:
``core`` would reach up into ``services`` and ``ingest``. Only ``app.main``,
the composition root, is allowed to know the whole set.
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

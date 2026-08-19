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
from apscheduler.schedulers.base import BaseScheduler

from app.core.logging import get_logger

logger = get_logger(__name__)

#: The scheduler this process is running, recorded by :func:`create_scheduler`.
#:
#: A module global for the reason `app.persistence.db`'s session factory is
#: one: the handle is otherwise only on `app.state`, which a service cannot
#: reach — `app.services` and `app.ingest` may not import `app.api`, and a
#: FastAPI request object would be the wrong way to find a process-wide object
#: anyway. Written once at startup and read by whoever needs to re-time a job
#: it did not register (`app.ingest.inbox.reschedule_inbox_job`).
_scheduler: BaseScheduler | None = None


def create_scheduler() -> AsyncIOScheduler:
    """Build and start the application scheduler.

    Returns:
        A started ``AsyncIOScheduler`` bound to the running event loop.
    """
    global _scheduler  # noqa: PLW0603 — one process-wide handle, set at startup
    scheduler = AsyncIOScheduler()
    scheduler.start()
    _scheduler = scheduler
    logger.info("scheduler_started", jobs=len(scheduler.get_jobs()))
    return scheduler


def current_scheduler() -> BaseScheduler | None:
    """The scheduler currently running in this process, or ``None``.

    A scheduler that has been shut down is reported as ``None`` rather than
    handed back: the callers of this function want to change a job that is
    going to run, and the shutdown path in `app.main` does not clear the
    handle. This also keeps a test that boots a second application from
    re-timing the first one's corpse.
    """
    return _scheduler if _scheduler is not None and _scheduler.running else None

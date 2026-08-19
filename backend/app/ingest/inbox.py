"""The watched folder: what the scheduler sweeps, and what it leaves alone.

Build-plan WP-4.3. Every ``INGEST__SCAN_INTERVAL_SECONDS`` the job lists
``data/inbox/`` and hands each settled file to `app.ingest.pipeline`. Files
leave the inbox by being moved — into ``originals/`` or ``quarantine/`` — so
the directory is empty again between sweeps and nothing is processed twice.

**Two conditions, and both are about half-written files.** A file is taken
only when it has been unmodified for ``INGEST__SETTLE_SECONDS`` **and** was
seen at this exact size on the previous sweep. The second is not a refinement
of the first: `rsync -t`, `cp -p` and Syncthing all preserve the *source's*
modification time, so a file that is still arriving can present an mtime from
last Tuesday. Two sightings at one size is the only evidence this job has that
nothing is still writing, which is why a file is never taken the first time it
is seen — at thirty-second sweeps that costs a drop half a minute and buys
never quarantining a complete ride as truncated.

Dotfiles are skipped outright: they are the `.DS_Store`/`.syncthing.*.tmp`
traffic every sync tool leaves behind. So is anything that is not a **regular
file**: a symbolic link's target is somebody else's file, and copying a
pointer into ``originals/`` would make that tree's backup a set of dangling
links.

**One poisoned file may not stop the sweep.** Each file is ingested inside its
own try: the pipeline has its own catch-all, but the failures that escape it
are the interesting ones (a name the filesystem refuses, a full disk), and
without this guard the first such file would take every later file in the
directory down with it, on this sweep and on every sweep after it.

:func:`scan_inbox` is an ordinary coroutine taking its own paths, so a test
drives it against a temporary directory directly — no scheduler, no sleeping.
"""

import asyncio
import stat
import time
from collections.abc import Sequence
from pathlib import Path

from apscheduler.schedulers.base import BaseScheduler
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.scheduler import current_scheduler
from app.domain.actor import Actor
from app.ingest.pipeline import IngestPaths, IngestPipeline, IngestReport
from app.persistence.db import session_scope
from app.services.ingest_settings import IngestSettingsService, LocalDropSettings

logger = get_logger(__name__)

#: Job id under which the sweep is registered with APScheduler.
INBOX_JOB_ID = "ingest_inbox_scan"

#: Sizes seen on the previous sweep, by path. A file is taken only when this
#: says it was already here at the same size: a file seen for the first time
#: may be mid-copy however old its mtime claims to be, because some copiers
#: preserve the source's modification time.
_last_seen: dict[Path, int] = {}


def settled_files(
    inbox: Path, *, settle_seconds: float, now: float | None = None
) -> list[Path]:
    """The inbox files that look finished, oldest first.

    A file must be a regular file (not a link, not a directory, not a fifo),
    unmodified for ``settle_seconds``, **and** already recorded at this size by
    the previous call. A first sighting is always skipped — it is what makes
    the size comparison mean anything.

    Args:
        inbox: Directory to list. A missing directory yields nothing rather
            than raising: the tree is created at startup, and a sweep racing
            that is not an error.
        settle_seconds: How long a file must have been unmodified.
        now: The current time as a Unix timestamp; injected by tests so they
            need not sleep.

    Returns:
        Paths in modification order, so a backlog is ingested in the order it
        arrived.
    """
    if not inbox.is_dir():
        return []
    moment = time.time() if now is None else now
    settled: list[tuple[float, Path]] = []
    seen: dict[Path, int] = {}
    for path in sorted(inbox.iterdir()):
        if path.name.startswith("."):
            continue
        entry = path.lstat()  # lstat: a link is described, not followed
        if not stat.S_ISREG(entry.st_mode):
            continue
        seen[path] = entry.st_size
        if _last_seen.get(path) != entry.st_size:
            continue  # first sighting, or still growing
        if moment - entry.st_mtime < settle_seconds:
            continue
        settled.append((entry.st_mtime, path))
    _last_seen.clear()
    _last_seen.update(seen)
    return [path for _, path in sorted(settled)]


def forget_seen_files() -> None:
    """Drop the remembered sizes.

    Between tests, and whenever the inbox is repointed: the sizes are a cache
    keyed by path, and a fresh temporary directory reusing a path would
    otherwise be compared against another test's file.
    """
    _last_seen.clear()


async def scan_inbox(
    *,
    paths: IngestPaths | None = None,
    settle_seconds: float | None = None,
    now: float | None = None,
) -> Sequence[IngestReport]:
    """Ingest every settled file in the inbox, one transaction per file.

    One session per file rather than one for the sweep: a file that fails must
    not roll back the ones already ingested, and the pipeline's own catch-all
    needs a usable session to write its quarantine record on. One *try* per
    file for the same reason, one level up: whatever the pipeline could not
    handle stops at that file and the sweep carries on to the next.

    The `sleep(0)` between files is not politeness. A backfill is a hundred
    files in one call, and the health check the container is judged by has five
    seconds to answer; this hands the loop back between them, on top of the
    threads the pipeline already does its heavy work in.

    Args:
        paths: Data tree to sweep; read from settings when omitted.
        settle_seconds: Override the configured settle window.
        now: Current Unix timestamp, for tests.

    Returns:
        One report per file the sweep processed.
    """
    settings = get_settings()
    tree = paths or IngestPaths.from_settings()
    window = (
        settings.ingest.settle_seconds if settle_seconds is None else settle_seconds
    )
    files = settled_files(tree.inbox, settle_seconds=window, now=now)
    if not files:
        return []

    reports: list[IngestReport] = []
    actor = Actor.system()
    for path in files:
        try:
            async with session_scope() as session:
                pipeline = IngestPipeline.from_session(session)
                reports.append(await pipeline.ingest_file(path, actor=actor))
        except Exception:  # noqa: BLE001 — one file may not end the sweep
            logger.exception("ingest_file_failed", path=str(path))
        await asyncio.sleep(0)
    logger.info(
        "inbox_scanned",
        files=len(reports),
        outcomes=[report.outcome.value for report in reports],
    )
    return reports


async def set_scan_interval(
    session: AsyncSession, seconds: int, *, actor: Actor
) -> LocalDropSettings:
    """Store how often the drop folder is swept, and re-time the running sweep.

    The whole use-case, in one call, because the two halves are worthless
    apart: a stored interval nothing re-times is a number the athlete sets and
    the sweep ignores until the next deploy, and a re-timed job with nothing
    stored is a change that disappears at the next restart.

    It lives in `app.ingest` rather than beside the rest of the use-case in
    `app.services.ingest_settings` because re-timing the sweep is knowledge of
    the sweep's job — :data:`INBOX_JOB_ID`, registered by this module — and
    `app.services` may not import `app.ingest`. The storage half stays in the
    service, where the bounds, the audit row and the commit are.

    The scheduler is touched **after** the commit: a job re-timed for a change
    that then failed to persist would sweep on an interval nothing recorded.
    """
    applied = await IngestSettingsService.from_session(session).set_scan_interval(
        seconds, actor=actor
    )
    reschedule_inbox_job(applied.scan_interval_seconds)
    return applied


def reschedule_inbox_job(seconds: int) -> bool:
    """Point the running sweep at a new interval, without a restart.

    Returns:
        Whether a running job was re-timed. ``False`` is normal rather than an
        error: a test that never booted a lifespan, and a management command
        run against the same database from another process, both have a
        perfectly good reason to store an interval with no scheduler in front
        of them. The stored row is the durable half; this is the live one.
    """
    scheduler = current_scheduler()
    job = None if scheduler is None else scheduler.get_job(INBOX_JOB_ID)
    if scheduler is None or job is None:
        logger.info("inbox_job_not_running", seconds=seconds)
        return False
    scheduler.reschedule_job(INBOX_JOB_ID, trigger="interval", seconds=seconds)
    logger.info("inbox_job_rescheduled", seconds=seconds)
    return True


async def apply_stored_scan_interval() -> None:
    """Re-time the sweep onto the stored interval, if there is one.

    Called at the top of every sweep rather than during startup, and that is
    the point: `app.main`'s lifespan reads no database on purpose, so a boot
    still does not depend on one. The cost is that an instance whose athlete
    chose an hour runs one sweep on the environment's thirty seconds after a
    restart before settling; the benefit is that the setting survives restarts
    at all, and that a row changed out of band is picked up without one.
    """
    async with session_scope() as session:
        stored = await IngestSettingsService.from_session(
            session
        ).stored_scan_interval()
    if stored is not None:
        reschedule_inbox_job(stored)


async def run_inbox_job() -> None:
    """The scheduled sweep. Never raises — a failed sweep must not kill the job.

    Two `try`s, not one. Reconciling the interval reads the database and the
    sweep does not need it to have worked: folded into one block, a hiccup
    reading one small row would skip the sweep entirely, which is the failure
    this job exists to avoid dressed up as a settings problem.
    """
    try:
        await apply_stored_scan_interval()
    except Exception:  # noqa: BLE001 — the interval is not worth a missed sweep
        logger.exception("inbox_interval_reconcile_failed")
    try:
        await scan_inbox()
    except Exception:  # noqa: BLE001 — a scheduler job that raises stops running
        logger.exception("inbox_scan_failed")


def register_inbox_job(scheduler: BaseScheduler) -> None:
    """Register the sweep on the application scheduler.

    The job lives here rather than in `app.core.scheduler`, which owns no jobs
    of its own (see its module docstring): the module that owns the work
    registers it. The first run is one interval away — a boot is busy enough
    — and ``max_instances=1`` plus ``coalesce`` mean a slow sweep delays the
    next one instead of running two over the same directory.

    Registered on the **environment's** interval, which is the seed: a stored
    one overrides it, and the sweep applies that itself on its first run
    (:func:`apply_stored_scan_interval`) so that startup still touches no
    database.
    """
    interval = get_settings().ingest.scan_interval_seconds
    scheduler.add_job(
        run_inbox_job,
        "interval",
        seconds=interval,
        id=INBOX_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("inbox_job_registered", seconds=interval)

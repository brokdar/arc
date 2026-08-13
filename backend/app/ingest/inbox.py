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

from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.actor import Actor
from app.ingest.pipeline import IngestPaths, IngestPipeline, IngestReport
from app.persistence.db import session_scope

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


async def run_inbox_job() -> None:
    """The scheduled sweep. Never raises — a failed sweep must not kill the job."""
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

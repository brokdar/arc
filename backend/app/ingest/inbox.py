"""The watched folder: what the scheduler sweeps, and what it leaves alone.

Build-plan WP-4.3. Every ``INGEST__SCAN_INTERVAL_SECONDS`` the job lists
``data/inbox/`` and hands each settled file to `app.ingest.pipeline`. Files
leave the inbox by being moved — into ``originals/`` or ``quarantine/`` — so
the directory is empty again between sweeps and nothing is processed twice.

**Two skips, and both are about half-written files.** A file whose last
modification is more recent than ``INGEST__SETTLE_SECONDS`` may still be
arriving; so may one whose size changed since the previous sweep. Reading
either would quarantine a perfectly good ride as truncated, so the file waits
for the next sweep instead. Dotfiles are skipped outright: they are the
`.DS_Store`/`.syncthing.*.tmp` traffic every sync tool leaves behind.

:func:`scan_inbox` is an ordinary coroutine taking its own paths, so a test
drives it against a temporary directory directly — no scheduler, no sleeping.
"""

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

#: Sizes seen on the previous sweep, by path. A file whose size changed since
#: then is still being written, however old its mtime claims to be — some
#: copiers preserve the source's modification time.
_last_seen: dict[Path, int] = {}


def settled_files(
    inbox: Path, *, settle_seconds: float, now: float | None = None
) -> list[Path]:
    """The inbox files that look finished, oldest first.

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
        if not path.is_file() or path.name.startswith("."):
            continue
        stat = path.stat()
        seen[path] = stat.st_size
        grew = _last_seen.get(path) not in (None, stat.st_size)
        if grew or moment - stat.st_mtime < settle_seconds:
            continue
        settled.append((stat.st_mtime, path))
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
    needs a usable session to write its quarantine record on.

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
        async with session_scope() as session:
            pipeline = IngestPipeline.from_session(session)
            reports.append(await pipeline.ingest_file(path, actor=actor))
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
    of its own (see its module docstring): each work package registers what it
    needs. The first run is one interval away — a boot is busy enough already
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

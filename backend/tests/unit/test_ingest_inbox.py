"""The watched folder's sweep: what it picks up, and what it waits for.

The job function is called directly against a temporary inbox — no scheduler,
no sleeping. The conditions it applies (unmodified for long enough, seen at
this size once before, a regular file) are all about a file still being
written, and all asserted by controlling the clock rather than watching it.
"""

import asyncio
import contextlib
import os
import time
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.domain.activity import IngestOutcome
from app.domain.actor import Actor
from app.ingest.inbox import (
    INBOX_JOB_ID,
    forget_seen_files,
    register_inbox_job,
    run_inbox_job,
    scan_inbox,
    settled_files,
)
from app.ingest.pipeline import IngestPaths, IngestPipeline, IngestReport
from app.persistence.activity import SessionRow
from app.persistence.audit import AuditLogEntry
from tests.unit.golden_fit import golden

SETTLE_S = 2.0


def age(path: Path, seconds: float) -> None:
    """Pretend the file was last written ``seconds`` ago."""
    old = time.time() - seconds
    os.utime(path, (old, old))


def sighted(inbox: Path) -> list[Path]:
    """One sweep's worth of files, after the sighting sweep that precedes it.

    A file is never taken the first time it is seen (that is what makes the
    size comparison evidence of anything), so every test that wants a file
    *taken* has to sweep twice.
    """
    settled_files(inbox, settle_seconds=SETTLE_S)
    return settled_files(inbox, settle_seconds=SETTLE_S)


async def swept() -> list[IngestOutcome]:
    """Run the sighting sweep and then the one that does the work."""
    await scan_inbox()
    return [report.outcome for report in await scan_inbox()]


def test_a_settled_file_is_picked_up_and_a_fresh_one_waits(tmp_path: Path) -> None:
    settled = tmp_path / "old.fit"
    settled.write_bytes(b"x")
    age(settled, 60)
    fresh = tmp_path / "new.fit"
    fresh.write_bytes(b"x")

    assert sighted(tmp_path) == [settled]
    forget_seen_files()


def test_a_file_is_never_taken_the_first_time_it_is_seen(tmp_path: Path) -> None:
    # `rsync -t`, `cp -p` and Syncthing all preserve the source's modification
    # time, so a half-copied file can present an mtime from last week. Two
    # sightings at one size is the only evidence this job has.
    copying = tmp_path / "copying.fit"
    copying.write_bytes(b"half")
    age(copying, 60)

    assert settled_files(tmp_path, settle_seconds=SETTLE_S) == []

    copying.write_bytes(b"half and the rest")
    age(copying, 60)

    assert settled_files(tmp_path, settle_seconds=SETTLE_S) == [], "the size changed"
    assert settled_files(tmp_path, settle_seconds=SETTLE_S) == [copying]
    forget_seen_files()


def test_dotfiles_and_directories_are_never_swept(tmp_path: Path) -> None:
    # `.DS_Store`, `.syncthing.*.tmp` — the traffic every sync tool leaves.
    hidden = tmp_path / ".syncthing.ride.fit.tmp"
    hidden.write_bytes(b"x")
    age(hidden, 60)
    (tmp_path / "subdir").mkdir()

    assert sighted(tmp_path) == []
    forget_seen_files()


def test_a_symlink_is_never_swept(tmp_path: Path) -> None:
    # A link's target is somebody else's file: copying the pointer into
    # `originals/` would make a backup of that tree a set of dangling links.
    target = tmp_path / "elsewhere.fit"
    target.write_bytes(b"x")
    link = tmp_path / "inbox-link.fit"
    link.symlink_to(target)
    age(link, 60)

    assert sighted(tmp_path) == [target]
    forget_seen_files()


def test_a_missing_inbox_is_not_an_error(tmp_path: Path) -> None:
    assert settled_files(tmp_path / "nothing-here", settle_seconds=SETTLE_S) == []


async def test_the_sweep_ingests_every_settled_file_as_the_system_actor(
    data_root: Path,
    session_factory: async_sessionmaker[AsyncSession],
    db_session: AsyncSession,
) -> None:
    for name, golden_name in (
        ("ride.fit", "outdoor_ride.fit"),
        ("trainer.fit", "indoor_trainer.fit"),
    ):
        dropped = data_root / "inbox" / name
        dropped.write_bytes(golden(golden_name).read_bytes())
        age(dropped, 60)

    assert await swept() == [IngestOutcome.INGESTED, IngestOutcome.INGESTED]

    sessions = (await db_session.execute(sa.select(SessionRow))).scalars().all()
    assert len(sessions) == 2
    assert not list((data_root / "inbox").iterdir()), "the inbox is emptied"
    actors = (await db_session.execute(sa.select(AuditLogEntry.actor))).scalars().all()
    assert set(actors) == {"system"}


async def test_the_sweep_skips_a_file_that_is_still_arriving(
    data_root: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    arriving = data_root / "inbox" / "ride.fit"
    arriving.write_bytes(golden("outdoor_ride.fit").read_bytes())

    assert await scan_inbox() == []
    assert arriving.exists(), "it is left for the next sweep, not consumed"


async def test_a_second_sweep_over_the_same_files_ingests_nothing_new(
    data_root: Path,
    session_factory: async_sessionmaker[AsyncSession],
    db_session: AsyncSession,
) -> None:
    dropped = data_root / "inbox" / "ride.fit"
    dropped.write_bytes(golden("outdoor_ride.fit").read_bytes())
    age(dropped, 60)
    await swept()

    # The file was filed and the inbox cleaned, so there is nothing to see; and
    # re-dropping the same bytes is a duplicate rather than a second session.
    assert await scan_inbox() == []
    again = data_root / "inbox" / "ride-again.fit"
    again.write_bytes(golden("outdoor_ride.fit").read_bytes())
    age(again, 60)

    assert await swept() == [IngestOutcome.DUPLICATE_FILE]
    sessions = (await db_session.execute(sa.select(SessionRow))).scalars().all()
    assert len(sessions) == 1


async def test_one_unusable_file_does_not_stop_the_sweep(
    data_root: Path,
    session_factory: async_sessionmaker[AsyncSession],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The pipeline has its own catch-all; what escapes it (a name the
    # filesystem refuses, a full disk) used to take every later file in the
    # directory down with it — on this sweep and on every sweep after it.
    ingest_file = IngestPipeline.ingest_file

    async def explode_on_the_first(
        self: IngestPipeline, path: Path, *, actor: Actor, **kwargs: Any
    ) -> IngestReport:
        if path.name.startswith("poison"):
            raise OSError(36, "File name too long")
        return await ingest_file(self, path, actor=actor, **kwargs)

    monkeypatch.setattr(IngestPipeline, "ingest_file", explode_on_the_first)
    for name in ("poison.fit", "ride.fit"):
        dropped = data_root / "inbox" / name
        dropped.write_bytes(golden("outdoor_ride.fit").read_bytes())
        age(dropped, 60)

    assert await swept() == [IngestOutcome.INGESTED], "the good file behind it"

    sessions = (await db_session.execute(sa.select(SessionRow))).scalars().all()
    assert len(sessions) == 1
    assert (data_root / "inbox" / "poison.fit").exists(), "and nothing is lost"


async def test_a_file_whose_extension_is_a_payload_does_not_wedge_the_sweep(
    data_root: Path,
    session_factory: async_sessionmaker[AsyncSession],
    db_session: AsyncSession,
) -> None:
    # A 202-character extension makes `<64 hex>.<ext>` longer than any
    # filesystem accepts, and the ENAMETOOLONG that follows used to escape the
    # loop — leaving the file in the inbox for the next sweep to die on too.
    poison = data_root / "inbox" / f"ride.{'f' * 202}"
    poison.write_text("not a ride")
    good = data_root / "inbox" / "ride.fit"
    good.write_bytes(golden("outdoor_ride.fit").read_bytes())
    for path in (poison, good):
        age(path, 60)

    outcomes = await swept()

    assert IngestOutcome.INGESTED in outcomes, "the good file was still ingested"
    assert IngestOutcome.ERROR not in outcomes
    assert not list((data_root / "inbox").iterdir()), "and the inbox is empty again"


async def test_the_sweep_lets_the_event_loop_run_between_files(
    data_root: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # A season's backfill is 150 files in one call. The health check the
    # container is judged by has five seconds to answer, so the sweep must
    # yield — the whole reason the heavy work happens in a thread.
    for name in ("one.fit", "two.fit", "three.fit"):
        dropped = data_root / "inbox" / name
        dropped.write_bytes(golden("outdoor_ride.fit").read_bytes())
        age(dropped, 60)
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0)

    ticker = asyncio.create_task(tick())
    outcomes = await swept()
    ticker.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await ticker

    assert len(outcomes) == 3
    assert ticks > len(outcomes), "another task got the loop while files ingested"


async def test_the_job_swallows_a_failure_rather_than_stopping(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A scheduler job that raises stops being scheduled, which would silence
    # the watched folder for the lifetime of the process.
    async def explode() -> None:
        raise RuntimeError("the database is gone")

    monkeypatch.setattr("app.ingest.inbox.scan_inbox", explode)

    await run_inbox_job()


def test_the_job_is_registered_on_the_configured_interval(data_root: Path) -> None:
    registered: list[dict[str, object]] = []

    class Recorder:
        def add_job(self, func: object, trigger: str, **kwargs: object) -> None:
            registered.append({"func": func, "trigger": trigger} | kwargs)

    register_inbox_job(Recorder())  # pyrefly: ignore[bad-argument-type]

    [job] = registered
    assert job["id"] == INBOX_JOB_ID
    assert job["trigger"] == "interval"
    assert job["seconds"] == get_settings().ingest.scan_interval_seconds
    # A slow sweep must delay the next one, not run beside it over the same
    # directory.
    assert job["max_instances"] == 1


def test_the_data_tree_the_sweep_reads_comes_from_settings(data_root: Path) -> None:
    assert IngestPaths.from_settings().inbox == data_root / "inbox"

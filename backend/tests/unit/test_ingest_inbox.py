"""The watched folder's sweep: what it picks up, and what it waits for.

The job function is called directly against a temporary inbox — no scheduler,
no sleeping. The two skips it implements (a file too recently modified, a file
whose size changed since the last sweep) are both about a file still being
copied, and both are asserted by controlling the clock rather than watching
it.
"""

import os
import time
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.domain.activity import IngestOutcome
from app.ingest.inbox import (
    INBOX_JOB_ID,
    forget_seen_files,
    register_inbox_job,
    run_inbox_job,
    scan_inbox,
    settled_files,
)
from app.ingest.pipeline import IngestPaths
from app.persistence.activity import SessionRow
from app.persistence.audit import AuditLogEntry
from tests.unit.golden_fit import golden

SETTLE_S = 2.0


def age(path: Path, seconds: float) -> None:
    """Pretend the file was last written ``seconds`` ago."""
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_a_settled_file_is_picked_up_and_a_fresh_one_waits(tmp_path: Path) -> None:
    settled = tmp_path / "old.fit"
    settled.write_bytes(b"x")
    age(settled, 60)
    fresh = tmp_path / "new.fit"
    fresh.write_bytes(b"x")

    assert settled_files(tmp_path, settle_seconds=SETTLE_S) == [settled]


def test_dotfiles_and_directories_are_never_swept(tmp_path: Path) -> None:
    # `.DS_Store`, `.syncthing.*.tmp` — the traffic every sync tool leaves.
    hidden = tmp_path / ".syncthing.ride.fit.tmp"
    hidden.write_bytes(b"x")
    age(hidden, 60)
    (tmp_path / "subdir").mkdir()

    assert settled_files(tmp_path, settle_seconds=SETTLE_S) == []


def test_a_file_that_grew_since_the_last_sweep_waits_for_the_next(
    tmp_path: Path,
) -> None:
    # Some copiers preserve the source's modification time, so an old mtime is
    # not proof the file is complete. A changed size is proof it is not.
    growing = tmp_path / "copying.fit"
    growing.write_bytes(b"half")
    age(growing, 60)
    assert settled_files(tmp_path, settle_seconds=SETTLE_S) == [growing]

    growing.write_bytes(b"half and more")
    age(growing, 60)
    assert settled_files(tmp_path, settle_seconds=SETTLE_S) == []

    # Unchanged on the sweep after that, so it is taken.
    assert settled_files(tmp_path, settle_seconds=SETTLE_S) == [growing]
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

    reports = await scan_inbox()

    assert [report.outcome for report in reports] == [
        IngestOutcome.INGESTED,
        IngestOutcome.INGESTED,
    ]
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
    await scan_inbox()

    # The file was moved out, so there is nothing to see; and re-dropping the
    # same bytes is a duplicate rather than a second session.
    assert await scan_inbox() == []
    again = data_root / "inbox" / "ride-again.fit"
    again.write_bytes(golden("outdoor_ride.fit").read_bytes())
    age(again, 60)

    [report] = await scan_inbox()

    assert report.outcome is IngestOutcome.DUPLICATE_FILE
    sessions = (await db_session.execute(sa.select(SessionRow))).scalars().all()
    assert len(sessions) == 1


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

"""AC-11 to AC-17: the scheduled poll that makes a Dropbox folder deliver.

Everything here drives `app.ingest.feeds` against the fake upstream in
`tests/unit/dropbox_fake.py` and the real pipeline on in-memory SQLite. Nothing
is stubbed between the poll and the session row: the claim the feature is built
on is "a file in Dropbox becomes a session with no hands", and only an
end-to-end run of the real pipeline can fail when it stops being true.

Two shapes recur:

* assertions about **traffic** (how many downloads, which entries were never
  fetched) read `FakeDropbox.calls`, because "arc did not download the
  screenshot" is invisible in the database and visible only in the requests;
* assertions about **state** read the rows — the session, the recording's
  `source`/`external_id`, the ingest event, the feed's cursor — never the
  poll's return value, which is a log line and not the artefact.
"""

import datetime as dt
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog.testing import capture_logs

from app.connectors import dropbox
from app.connectors.dropbox import ACCOUNT_NO_ACCESS, READ_SCOPES
from app.core.config import get_settings
from app.domain.activity import IngestOutcome, IngestSource
from app.domain.actor import Actor
from app.domain.connections import (
    ConnectionProvider,
    ConnectionStatus,
    FeedDeliveryState,
)
from app.domain.integrations import (
    DataKind,
    IntegrationKind,
    IntegrationSpec,
    StorageProvider,
    TransportKind,
    TransportSpec,
)
from app.ingest import feeds
from app.ingest.inbox import scan_inbox
from app.ingest.pipeline import IngestPipeline
from app.ingest.service import MAX_UPLOAD_BYTES
from app.persistence.activity import RecordingRow, SessionRow
from app.persistence.audit import AuditLogEntry
from app.persistence.connections import (
    ConnectionRow,
    EncryptedCredentials,
    FeedRow,
)
from app.persistence.db import session_scope
from app.persistence.ingest_log import IngestEventRow, QuarantineRecordRow
from app.persistence.integrations import IntegrationRow
from app.services.connections import ConnectionService
from app.services.integrations import IntegrationService
from tests.unit.dropbox_fake import (
    DOWNLOAD_PATH,
    LIST_FOLDER_CONTINUE_PATH,
    LIST_FOLDER_PATH,
    TOKEN_PATH,
    FakeDropbox,
    deleted_entry,
    expired_access_token,
    file_entry,
    folder_entry,
    missing_scope,
    no_access,
    page,
    path_not_found,
    rate_limited,
    server_error,
)
from tests.unit.golden_fit import golden

pytestmark = pytest.mark.usefixtures("dropbox_env", "data_root", "session_factory")

#: The folder every feed here watches.
WATCHED = "/apps/wahoofitness"

#: A second folder on the same connection, polled **before** :data:`WATCHED`.
#:
#: `ConnectionRow.feeds` is ordered by `remote_path`, so "h" before "w" is what
#: makes "the first feed of the cycle" a fact rather than a coincidence — the
#: two-feed tests below are entirely about what the *second* one does after the
#: first has flipped the row.
ALSO_WATCHED = "/apps/healthfit"


@pytest.fixture(autouse=True)
def fake() -> Iterator[FakeDropbox]:
    """Dropbox, faked, for every test in this module.

    Autouse: a test that forgot to ask for it would poll dropbox.com from the
    unit suite.
    """
    upstream = FakeDropbox()
    dropbox.set_transport(upstream.transport)
    yield upstream
    dropbox.set_transport(None)


def ride_bytes() -> bytes:
    """A real FIT file the pipeline can parse into one cycling session."""
    return golden("outdoor_ride.fit").read_bytes()


def other_ride_bytes() -> bytes:
    """A second real FIT file — different bytes, different session."""
    return golden("indoor_trainer.fit").read_bytes()


async def connect(
    session: AsyncSession,
    *,
    status: ConnectionStatus = ConnectionStatus.CONNECTED,
    credentials: bytes | None = None,
) -> ConnectionRow:
    """Store a Dropbox connection holding a usable sealed credential."""
    row = ConnectionRow(
        provider=ConnectionProvider.DROPBOX,
        status=status,
        account_label="Ada Lovelace (ada@example.com)",
        scopes=sorted(READ_SCOPES),
        credentials=credentials
        if credentials is not None
        else EncryptedCredentials.seal(
            {"access_token": "access-token-0", "refresh_token": "refresh-token-0"}
        ),
        access_token_expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
    )
    session.add(row)
    await session.commit()
    return row


async def watch(
    session: AsyncSession,
    connection: ConnectionRow,
    *,
    remote_path: str = WATCHED,
    enabled: bool = True,
) -> FeedRow:
    """Point a feed at a folder."""
    row = FeedRow(connection_id=connection.id, remote_path=remote_path, enabled=enabled)
    session.add(row)
    await session.commit()
    return row


async def rows_of(session: AsyncSession, model: Any) -> list[Any]:
    """Every row of one table, as the database now has them.

    ``populate_existing`` rather than ``expire_all``: the poll commits on other
    sessions, so anything this one already holds is stale and has to be
    overwritten by the query — but *expiring* it instead would leave every
    other object in the test holding attributes that reload themselves
    synchronously, which an async session cannot do.
    """
    statement = select(model).execution_options(populate_existing=True)
    return list((await session.execute(statement)).scalars())


async def reread(session: AsyncSession, feed: FeedRow) -> FeedRow:
    """The feed as the database now has it."""
    statement = (
        select(FeedRow)
        .where(FeedRow.id == feed.id)
        .execution_options(populate_existing=True)
    )
    fetched = (await session.execute(statement)).scalars().first()
    assert fetched is not None
    return fetched


def downloads(fake: FakeDropbox) -> list[Any]:
    """Every download request arc issued."""
    return fake.calls_to(DOWNLOAD_PATH)


# --- AC-11: the job, and the feeds it declines to poll ------------------------------


async def test_a_raising_poll_leaves_the_job_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-11: an APScheduler job that raises is removed; this one may not be."""

    async def explode() -> None:
        raise RuntimeError("dropbox fell over")

    monkeypatch.setattr(feeds, "poll_feeds", explode)

    await feeds.run_feed_poll_job()  # must not raise


async def test_with_no_connection_the_poll_does_nothing_and_asks_dropbox_nothing(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    await feeds.poll_feeds()

    assert fake.calls == []
    assert await rows_of(db_session, SessionRow) == []


async def test_a_disabled_feed_is_skipped(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    connection = await connect(db_session)
    await watch(db_session, connection, enabled=False)

    await feeds.poll_feeds()

    assert fake.calls == []


async def test_a_connection_needing_reauth_is_skipped_without_a_request(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    # Spending a request to be told what the row already says is a request the
    # rate limit will want later.
    connection = await connect(db_session, status=ConnectionStatus.NEEDS_REAUTH)
    await watch(db_session, connection)

    await feeds.poll_feeds()

    assert fake.calls == []


# --- AC-12: a file in the folder becomes a session ---------------------------------


async def test_a_fit_in_the_watched_folder_becomes_a_session(
    fake: FakeDropbox, db_session: AsyncSession, data_root: Path
) -> None:
    connection = await connect(db_session)
    feed = await watch(db_session, connection)
    entry = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    fake.by_cursor = {None: page(entry, cursor="cursor-1")}
    fake.files[entry["id"]] = ride_bytes()
    before = dt.datetime.now(dt.UTC)

    await feeds.poll_feeds()

    [session_row] = await rows_of(db_session, SessionRow)
    [recording] = await rows_of(db_session, RecordingRow)
    assert recording.session_id == session_row.id
    assert recording.source == IngestSource.DROPBOX.value == "dropbox"
    assert recording.external_id == entry["id"]
    [event] = [
        row
        for row in await rows_of(db_session, IngestEventRow)
        if row.outcome is IngestOutcome.INGESTED
    ]
    assert "ride.fit" in event.filename
    stored = await reread(db_session, feed)
    assert stored.last_delivery_at is not None
    assert stored.last_delivery_at >= before
    assert stored.cursor == "cursor-1"
    # No local drop was involved: the staging copy is gone and the watched
    # folder never held a settled file for the sweep to find.
    assert list((data_root / "inbox").iterdir()) == []


async def test_an_awkward_dropbox_name_is_sanitised_and_keeps_its_extension(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    connection = await connect(db_session)
    await watch(db_session, connection)
    entry = file_entry(
        "Morning Ride, Zürich.fit", f"{WATCHED}/morning ride, zürich.fit"
    )
    fake.by_cursor = {None: page(entry, cursor="cursor-1")}
    fake.files[entry["id"]] = ride_bytes()

    await feeds.poll_feeds()

    # It parsed — which is the claim: the extension survived sanitising, so
    # dispatch found the FIT parser.
    [recording] = await rows_of(db_session, RecordingRow)
    assert recording.original_ext == "fit"
    [event] = [
        row
        for row in await rows_of(db_session, IngestEventRow)
        if row.outcome is IngestOutcome.INGESTED
    ]
    assert event.filename.endswith(".fit")
    assert " " not in event.filename
    assert "," not in event.filename


async def test_a_name_with_no_extension_is_skipped_rather_than_staged_as_bin(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    connection = await connect(db_session)
    await watch(db_session, connection)
    entry = file_entry("activity", f"{WATCHED}/activity")
    fake.by_cursor = {None: page(entry, cursor="cursor-1")}
    fake.files[entry["id"]] = ride_bytes()

    await feeds.poll_feeds()

    assert downloads(fake) == []
    assert await rows_of(db_session, SessionRow) == []
    assert await rows_of(db_session, QuarantineRecordRow) == []


async def test_two_distinct_rides_in_one_batch_become_two_sessions(
    fake: FakeDropbox, db_session: AsyncSession, data_root: Path
) -> None:
    """AC-12: a batch is not a file — every ride in one page is delivered.

    The quarantine case below proves the loop survives a *bad* entry; this
    proves it does not stop after a *good* one. A poll that returned after the
    first success would still pass every other test in this module — the folder
    would drain one ride per interval and the second ride of a double day would
    arrive two minutes late, or, if the cursor moved with it, never.
    """
    first = file_entry("outdoor.fit", f"{WATCHED}/outdoor.fit")
    second = file_entry("trainer.fit", f"{WATCHED}/trainer.fit")
    fake.by_cursor = {None: page(first, second, cursor="cursor-1")}
    fake.files[first["id"]] = ride_bytes()
    fake.files[second["id"]] = other_ride_bytes()
    connection = await connect(db_session)
    feed = await watch(db_session, connection)

    await feeds.poll_feeds()

    assert len(await rows_of(db_session, SessionRow)) == 2
    recordings = await rows_of(db_session, RecordingRow)
    assert len(recordings) == 2
    # Two sessions, not one session with two recordings: distinct rides.
    assert len({recording.session_id for recording in recordings}) == 2
    assert {recording.external_id for recording in recordings} == {
        first["id"],
        second["id"],
    }
    assert {recording.source for recording in recordings} == {"dropbox"}
    outcomes = [row.outcome for row in await rows_of(db_session, IngestEventRow)]
    assert outcomes == [IngestOutcome.INGESTED, IngestOutcome.INGESTED]
    assert (await reread(db_session, feed)).cursor == "cursor-1"
    # Neither ride was uploaded: nothing was left staged for a later sweep.
    assert list((data_root / "inbox").iterdir()) == []


async def test_two_files_in_one_batch_both_ingest_when_the_first_quarantines(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    broken = file_entry("broken.fit", f"{WATCHED}/broken.fit")
    good = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    fake.by_cursor = {None: page(broken, good, cursor="cursor-1")}
    fake.files[broken["id"]] = b"this is not a FIT file at all"
    fake.files[good["id"]] = ride_bytes()
    connection = await connect(db_session)
    feed = await watch(db_session, connection)

    await feeds.poll_feeds()

    assert len(await rows_of(db_session, SessionRow)) == 1
    assert len(await rows_of(db_session, QuarantineRecordRow)) == 1
    assert (await reread(db_session, feed)).cursor == "cursor-1"


async def test_corrupt_bytes_quarantine_and_the_cursor_still_advances(
    fake: FakeDropbox, db_session: AsyncSession, data_root: Path
) -> None:
    entry = file_entry("truncated.fit", f"{WATCHED}/truncated.fit")
    fake.by_cursor = {None: page(entry, cursor="cursor-1")}
    # A real FIT file with its tail cut off: the header parses, the records
    # do not.
    fake.files[entry["id"]] = ride_bytes()[:200]
    connection = await connect(db_session)
    feed = await watch(db_session, connection)

    await feeds.poll_feeds()

    [event] = await rows_of(db_session, IngestEventRow)
    assert event.outcome is IngestOutcome.QUARANTINED
    [record] = await rows_of(db_session, QuarantineRecordRow)
    assert Path(record.quarantined_path).parent == (data_root / "quarantine")
    assert Path(record.quarantined_path).is_file()
    assert (await reread(db_session, feed)).cursor == "cursor-1"
    assert (await reread(db_session, feed)).cursor_attempts == 0


# --- AC-13: what is downloaded, and what is refused from the listing ----------------


async def test_only_activity_files_are_downloaded(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    ride = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    fake.by_cursor = {
        None: page(
            file_entry("notes.txt", f"{WATCHED}/notes.txt"),
            file_entry("photo.jpg", f"{WATCHED}/photo.jpg"),
            ride,
            cursor="cursor-1",
        )
    }
    fake.files[ride["id"]] = ride_bytes()
    connection = await connect(db_session)
    await watch(db_session, connection)

    await feeds.poll_feeds()

    assert len(downloads(fake)) == 1
    events = await rows_of(db_session, IngestEventRow)
    assert [event.filename for event in events] == ["ride.fit"]
    assert await rows_of(db_session, QuarantineRecordRow) == []


async def test_an_in_progress_activity_is_skipped_by_name(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    # Zwift writes this file continuously *during* a ride and leaves it behind
    # after a crash; taking it ingests a truncated ride.
    entry = file_entry("inProgressActivity.fit", f"{WATCHED}/inprogressactivity.fit")
    fake.by_cursor = {None: page(entry, cursor="cursor-1")}
    fake.files[entry["id"]] = ride_bytes()
    connection = await connect(db_session)
    await watch(db_session, connection)

    with capture_logs() as logs:
        await feeds.poll_feeds()

    assert downloads(fake) == []
    assert await rows_of(db_session, SessionRow) == []
    # Logged, not silent: a file arc declined to take is the first thing
    # anybody looks for when a ride is missing.
    skipped = [line for line in logs if line["event"] == "dropbox_entry_skipped"]
    assert [line["name"] for line in skipped] == ["inProgressActivity.fit"]


async def test_an_uppercase_extension_is_taken(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    entry = file_entry("RIDE.FIT", f"{WATCHED}/ride.fit")
    fake.by_cursor = {None: page(entry, cursor="cursor-1")}
    fake.files[entry["id"]] = ride_bytes()
    connection = await connect(db_session)
    await watch(db_session, connection)

    await feeds.poll_feeds()

    assert len(downloads(fake)) == 1
    assert len(await rows_of(db_session, SessionRow)) == 1


async def test_an_oversized_entry_is_refused_from_the_listing_without_a_download(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    entry = file_entry("huge.fit", f"{WATCHED}/huge.fit", size=MAX_UPLOAD_BYTES + 1)
    fake.by_cursor = {None: page(entry, cursor="cursor-1")}
    fake.files[entry["id"]] = ride_bytes()
    connection = await connect(db_session)
    feed = await watch(db_session, connection)

    await feeds.poll_feeds()

    assert downloads(fake) == [], "a 100 MB file may not be pulled over to be refused"
    [event] = await rows_of(db_session, IngestEventRow)
    assert event.filename == "huge.fit"
    assert event.detail is not None
    assert str(MAX_UPLOAD_BYTES) in event.detail
    assert await rows_of(db_session, QuarantineRecordRow) == []
    # Not a batch failure: the cursor moves past it, so it is refused once.
    assert (await reread(db_session, feed)).cursor == "cursor-1"


async def test_a_deleted_entry_is_ignored(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    ride = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    fake.by_cursor = {
        None: page(
            deleted_entry("gone.fit", f"{WATCHED}/gone.fit"), ride, cursor="cursor-1"
        )
    }
    fake.files[ride["id"]] = ride_bytes()
    connection = await connect(db_session)
    feed = await watch(db_session, connection)

    await feeds.poll_feeds()

    assert len(downloads(fake)) == 1
    assert len(await rows_of(db_session, SessionRow)) == 1
    assert (await reread(db_session, feed)).cursor == "cursor-1"


async def test_a_folder_entry_inside_the_watched_folder_is_ignored(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    # The watch is non-recursive: a subfolder is not a file and is not walked.
    fake.by_cursor = {
        None: page(folder_entry("2026", f"{WATCHED}/2026"), cursor="cursor-1")
    }
    connection = await connect(db_session)
    feed = await watch(db_session, connection)

    await feeds.poll_feeds()

    assert downloads(fake) == []
    assert await rows_of(db_session, SessionRow) == []
    assert (await reread(db_session, feed)).cursor == "cursor-1"


async def test_an_empty_page_still_moves_the_delivery_clock(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    """`last_delivery_at` means *heard from Dropbox*, not *a ride landed*.

    A rest week with nothing new in the folder is a resolved, empty batch —
    exactly the case the column exists to tell apart from a broken feed. If
    only an ingest moved the clock, a quiet week and a dead connector would
    read identically on the settings panel and in `get_ingest_status`.
    """
    fake.by_cursor = {None: page(cursor="cursor-1")}
    connection = await connect(db_session)
    feed = await watch(db_session, connection)
    stale = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    stored = await reread(db_session, feed)
    stored.last_delivery_at = stale
    await db_session.commit()

    await feeds.poll_feeds()

    stored = await reread(db_session, feed)
    assert stored.cursor == "cursor-1"
    assert stored.last_delivery_at is not None
    assert stored.last_delivery_at > stale
    assert await rows_of(db_session, SessionRow) == []


# --- AC-14: the same file twice is one session -------------------------------------


async def test_a_rewound_cursor_redelivers_the_file_as_a_duplicate(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    entry = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    fake.by_cursor = {None: page(entry, cursor="cursor-1")}
    fake.files[entry["id"]] = ride_bytes()
    connection = await connect(db_session)
    feed = await watch(db_session, connection)

    await feeds.poll_feeds()
    # The rewind: whatever put the cursor back, the folder is listed again and
    # serves the same entry.
    stored = await reread(db_session, feed)
    stored.cursor = None
    # Backdate the delivery clock before the replay. `last_delivery_at` means
    # "heard from Dropbox at all", so a batch that ingests nothing because it
    # is all duplicates must still move it — that is what makes a stale value a
    # *silence* signal rather than a rest week. Comparing the replay's value
    # against a sentinel a day old, instead of against the first poll's own
    # timestamp, is what gives the claim a falsifiable direction: only a write
    # during the replay can clear it.
    stale = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    stored.last_delivery_at = stale
    await db_session.commit()
    resumed = dt.datetime.now(dt.UTC)

    await feeds.poll_feeds()

    assert len(await rows_of(db_session, SessionRow)) == 1
    outcomes = [row.outcome for row in await rows_of(db_session, IngestEventRow)]
    assert outcomes == [IngestOutcome.INGESTED, IngestOutcome.DUPLICATE_FILE]
    after = (await reread(db_session, feed)).last_delivery_at
    assert after is not None
    assert after > stale
    # And it is *this* poll's clock, not a value carried over from the first.
    assert after >= resumed


async def test_the_same_bytes_under_a_second_name_are_one_session(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    first = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    second = file_entry("ride-copy.fit", f"{WATCHED}/ride-copy.fit")
    fake.by_cursor = {None: page(first, second, cursor="cursor-1")}
    fake.files[first["id"]] = ride_bytes()
    fake.files[second["id"]] = ride_bytes()
    connection = await connect(db_session)
    await watch(db_session, connection)

    await feeds.poll_feeds()

    assert len(await rows_of(db_session, SessionRow)) == 1
    outcomes = [row.outcome for row in await rows_of(db_session, IngestEventRow)]
    assert outcomes == [IngestOutcome.INGESTED, IngestOutcome.DUPLICATE_FILE]


async def test_a_file_already_ingested_locally_is_not_recreated_over_dropbox(
    fake: FakeDropbox, db_session: AsyncSession, data_root: Path
) -> None:
    # Identity is the hash, not where the bytes arrived from.
    dropped = data_root / "inbox" / "ride.fit"
    dropped.write_bytes(ride_bytes())
    await IngestPipeline.from_session(db_session).ingest_file(
        dropped, actor=Actor.system()
    )
    assert len(await rows_of(db_session, SessionRow)) == 1

    entry = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    fake.by_cursor = {None: page(entry, cursor="cursor-1")}
    fake.files[entry["id"]] = ride_bytes()
    connection = await connect(db_session)
    await watch(db_session, connection)

    await feeds.poll_feeds()

    assert len(await rows_of(db_session, SessionRow)) == 1
    assert [row.outcome for row in await rows_of(db_session, IngestEventRow)][-1] is (
        IngestOutcome.DUPLICATE_FILE
    )


async def test_an_edited_file_with_new_bytes_ingests_as_a_new_session(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    entry = file_entry("ride.fit", f"{WATCHED}/ride.fit", rev="rev-1")
    fake.by_cursor = {None: page(entry, cursor="cursor-1")}
    fake.files[entry["id"]] = ride_bytes()
    connection = await connect(db_session)
    feed = await watch(db_session, connection)
    await feeds.poll_feeds()

    # Same id, new rev, different bytes — Dropbox re-lists it as a change.
    edited = file_entry("ride.fit", f"{WATCHED}/ride.fit", rev="rev-2")
    fake.by_cursor["cursor-1"] = page(edited, cursor="cursor-2")
    fake.files[edited["id"]] = other_ride_bytes()

    await feeds.poll_feeds()

    assert len(await rows_of(db_session, SessionRow)) == 2
    assert (await reread(db_session, feed)).cursor == "cursor-2"


# --- AC-15: a failed batch is replayed, not skipped ---------------------------------


async def test_a_failed_batch_is_retried_and_creates_no_second_session(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    """The cursor advances only after *every* entry in a batch is resolved.

    Advancing per entry would skip the file that failed, permanently — and a
    lost ride is the exact failure this feature exists to prevent. Replaying is
    free: rung-1 sha256 dedup turns the entries already taken into
    `duplicate_file` log lines rather than second sessions.
    """
    good = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    flaky = file_entry("second.fit", f"{WATCHED}/second.fit")
    fake.by_cursor = {None: page(good, flaky, cursor="cursor-1")}
    fake.files[good["id"]] = ride_bytes()
    fake.download_failures[flaky["id"]] = server_error()
    connection = await connect(db_session)
    feed = await watch(db_session, connection)

    await feeds.poll_feeds()

    stored = await reread(db_session, feed)
    assert stored.cursor is None, "a half-resolved batch may not advance the cursor"
    assert stored.cursor_attempts == 1
    assert stored.last_error is not None
    assert "second.fit" in stored.last_error

    # The replay: the second entry now works, and the first must not double.
    del fake.download_failures[flaky["id"]]
    fake.files[flaky["id"]] = other_ride_bytes()
    await feeds.poll_feeds()

    assert len(await rows_of(db_session, SessionRow)) == 2
    outcomes = [row.outcome for row in await rows_of(db_session, IngestEventRow)]
    assert outcomes.count(IngestOutcome.DUPLICATE_FILE) == 1
    stored = await reread(db_session, feed)
    assert stored.cursor == "cursor-1"
    assert stored.cursor_attempts == 0
    assert stored.last_error is None


async def test_a_rate_limit_stops_the_poll_early_and_records_the_delay(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    first = file_entry("a.fit", f"{WATCHED}/a.fit")
    throttled = file_entry("b.fit", f"{WATCHED}/b.fit")
    third = file_entry("c.fit", f"{WATCHED}/c.fit")
    fake.by_cursor = {None: page(first, throttled, third, cursor="cursor-1")}
    fake.files[first["id"]] = ride_bytes()
    fake.download_failures[throttled["id"]] = rate_limited("42")
    fake.files[third["id"]] = other_ride_bytes()
    connection = await connect(db_session)
    feed = await watch(db_session, connection)

    await feeds.poll_feeds()

    assert len(downloads(fake)) == 2, "the third entry must not be hammered"
    stored = await reread(db_session, feed)
    assert stored.cursor is None
    assert stored.last_error is not None
    assert "42" in stored.last_error


async def test_the_cursor_lives_in_the_row_so_a_restart_replays_the_batch(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    good = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    flaky = file_entry("second.fit", f"{WATCHED}/second.fit")
    fake.by_cursor = {None: page(good, flaky, cursor="cursor-1")}
    fake.files[good["id"]] = ride_bytes()
    fake.download_failures[flaky["id"]] = server_error()
    connection = await connect(db_session)
    feed = await watch(db_session, connection)
    await feeds.poll_feeds()

    # The "restart": every in-process cache is dropped and the poll is entered
    # again with nothing but the row to go on.
    get_settings.cache_clear()
    del fake.download_failures[flaky["id"]]
    fake.files[flaky["id"]] = other_ride_bytes()

    await feeds.poll_feeds()

    assert len(await rows_of(db_session, SessionRow)) == 2
    assert (await reread(db_session, feed)).cursor == "cursor-1"


# --- AC-16: giving up on a poisoned batch ------------------------------------------


async def test_a_poisoned_batch_is_given_up_on_after_the_configured_attempts(
    fake: FakeDropbox, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Holding the cursor forever would re-download one bad batch every 2 min."""
    monkeypatch.setenv("DROPBOX__MAX_BATCH_ATTEMPTS", "3")
    get_settings.cache_clear()
    poison = file_entry("poison.fit", f"{WATCHED}/poison.fit")
    later = file_entry("later.fit", f"{WATCHED}/later.fit")
    fake.by_cursor = {
        None: page(poison, cursor="cursor-1"),
        "cursor-1": page(later, cursor="cursor-2"),
    }
    fake.download_failures[poison["id"]] = server_error()
    fake.files[later["id"]] = ride_bytes()
    connection = await connect(db_session)
    feed = await watch(db_session, connection)

    for expected in (1, 2):
        await feeds.poll_feeds()
        stored = await reread(db_session, feed)
        assert stored.cursor is None
        assert stored.cursor_attempts == expected

    await feeds.poll_feeds()

    stored = await reread(db_session, feed)
    assert stored.cursor == "cursor-1", "the third failure gives up and moves on"
    assert stored.cursor_attempts == 0
    assert stored.last_error is not None
    assert "poison.fit" in stored.last_error

    # And the batch after the poisoned one is now reachable.
    await feeds.poll_feeds()
    assert len(await rows_of(db_session, SessionRow)) == 1
    stored = await reread(db_session, feed)
    assert stored.cursor == "cursor-2"
    assert stored.last_error is None, "a good poll clears the error it left"


async def test_the_attempt_counter_resets_after_any_successful_batch(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    # Failures have to be *consecutive*: one bad day a month must never add up
    # to a batch being abandoned.
    flaky = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    fake.by_cursor = {None: page(flaky, cursor="cursor-1")}
    fake.download_failures[flaky["id"]] = server_error()
    connection = await connect(db_session)
    feed = await watch(db_session, connection)

    await feeds.poll_feeds()
    assert (await reread(db_session, feed)).cursor_attempts == 1

    del fake.download_failures[flaky["id"]]
    fake.files[flaky["id"]] = ride_bytes()
    await feeds.poll_feeds()

    stored = await reread(db_session, feed)
    assert stored.cursor_attempts == 0
    assert stored.last_error is None


# --- what the give-up budget is *not* for ------------------------------------------
#
# `cursor_attempts` buys liveness: a page that keeps refusing must not dam the
# rides behind it for ever. It buys nothing against a condition that suspends
# all progress and lifts on its own, and spending it there advances the cursor
# past files that were never downloaded once. Each test below is one such
# condition, and each one skipped a real ride before it was written.


async def test_a_throttled_batch_is_never_given_up_on(
    fake: FakeDropbox, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 429 lifts on its own, so it may not spend the give-up budget.

    Counting it advanced the cursor past `b.fit` — an ordinary ride arc never
    attempted even once, because the poll stops at the first refusal — and
    nothing would have offered it again.
    """
    monkeypatch.setenv("DROPBOX__MAX_BATCH_ATTEMPTS", "3")
    get_settings.cache_clear()
    throttled = file_entry("a.fit", f"{WATCHED}/a.fit")
    behind = file_entry("b.fit", f"{WATCHED}/b.fit")
    fake.by_cursor = {None: page(throttled, behind, cursor="cursor-1")}
    fake.download_failures[throttled["id"]] = rate_limited("3600")
    fake.files[throttled["id"]] = ride_bytes()
    fake.files[behind["id"]] = other_ride_bytes()
    connection = await connect(db_session)
    feed = await watch(db_session, connection)

    for _ in range(4):  # one poll more than the budget would have allowed
        await feeds.poll_feeds()

    stored = await reread(db_session, feed)
    assert stored.cursor is None, "a throttle may not advance the cursor"
    assert stored.cursor_attempts == 0, "a throttle is not the batch's fault"
    assert stored.last_error is not None, "but it is visible while it lasts"
    assert await rows_of(db_session, SessionRow) == []

    # The throttle lifts, and the whole page arrives — including the entry
    # that was sitting behind the one Dropbox would not serve.
    del fake.download_failures[throttled["id"]]
    await feeds.poll_feeds()

    assert len(await rows_of(db_session, SessionRow)) == 2
    stored = await reread(db_session, feed)
    assert stored.cursor == "cursor-1"
    assert stored.last_error is None, "a resolved batch clears what it left"


async def test_a_stale_credential_on_download_is_never_given_up_on(
    fake: FakeDropbox, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A download-only 401 is not proof the credential is dead.

    `DropboxClient._content_failure` deliberately does not retry-and-refresh
    inline (see its own docstring) — that happens on the *next listing*. So a
    401 here has not been through the one retry that would say whether the
    token was merely stale or genuinely revoked, and blaming this page for it
    would advance the cursor past `b.fit` — an entry never attempted even
    once — before arc ever finds out which.
    """
    monkeypatch.setenv("DROPBOX__MAX_BATCH_ATTEMPTS", "3")
    get_settings.cache_clear()
    stale = file_entry("a.fit", f"{WATCHED}/a.fit")
    behind = file_entry("b.fit", f"{WATCHED}/b.fit")
    fake.by_cursor = {None: page(stale, behind, cursor="cursor-1")}
    fake.download_failures[stale["id"]] = expired_access_token()
    fake.files[stale["id"]] = ride_bytes()
    fake.files[behind["id"]] = other_ride_bytes()
    connection = await connect(db_session)
    feed = await watch(db_session, connection)

    for _ in range(4):  # one poll more than the budget would have allowed
        await feeds.poll_feeds()

    stored = await reread(db_session, feed)
    assert stored.cursor is None, "a stale credential may not advance the cursor"
    assert stored.cursor_attempts == 0, "a credential problem is not the page's fault"
    assert stored.last_error is not None
    assert await rows_of(db_session, SessionRow) == []

    # The credential is refreshed (elsewhere), and the whole page arrives.
    del fake.download_failures[stale["id"]]
    await feeds.poll_feeds()

    assert len(await rows_of(db_session, SessionRow)) == 2
    stored = await reread(db_session, feed)
    assert stored.cursor == "cursor-1"
    assert stored.last_error is None, "a resolved batch clears what it left"


async def test_a_feed_that_cannot_store_what_it_downloaded_says_so(
    fake: FakeDropbox, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A local fault must not read as a healthy feed.

    `_deliver` writes to the disk and the database, and a failure in either is
    not Dropbox's fault and not the page's. It used to unwind to the sweep's
    per-feed catch, which logged it and touched nothing: the row kept its old
    `last_delivery_at`, so the settings panel and the coach's
    `get_ingest_status` both went on reporting a feed that was storing nothing.
    """
    entry = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    fake.by_cursor = {None: page(entry, cursor="cursor-1")}
    fake.files[entry["id"]] = ride_bytes()
    connection = await connect(db_session)
    feed = await watch(db_session, connection)
    # A baseline older than "now": proves the fault does not touch the clock,
    # rather than merely leaving an already-null one alone.
    baseline = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    stored = await reread(db_session, feed)
    stored.last_delivery_at = baseline
    await db_session.commit()

    async def full_disk(**_: Any) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr(feeds, "_deliver", full_disk)
    for _ in range(3):
        await feeds.poll_feeds()

    stored = await reread(db_session, feed)
    assert stored.cursor is None, "nothing was stored, so the position holds"
    assert stored.cursor_attempts == 0, "a full disk is not the batch's fault"
    assert stored.last_error is not None
    assert "No space left on device" in stored.last_error
    assert await rows_of(db_session, SessionRow) == []
    assert stored.last_delivery_at == baseline, (
        "a local fault must not look like a heard-from-Dropbox poll"
    )

    # And the coach is told, rather than being shown a working pipe.
    async with session_scope() as session:
        status = await ConnectionService.from_session(session).ingest_status()
    [reported] = [
        folder for integration in status.integrations for folder in integration.folders
    ]
    assert reported.state is FeedDeliveryState.FAILING


async def test_a_listing_outage_does_not_spend_the_batchs_budget(
    fake: FakeDropbox, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Attempts count failures of a *page*, not of the connection.

    A counter that climbed through an outage would leave the next genuine
    download failure already at the threshold, abandoning a page on its first
    refusal — files skipped by arithmetic rather than by the rule.
    """
    monkeypatch.setenv("DROPBOX__MAX_BATCH_ATTEMPTS", "3")
    get_settings.cache_clear()
    fake.list_failures[WATCHED] = server_error()
    connection = await connect(db_session)
    feed = await watch(db_session, connection)

    for _ in range(4):
        await feeds.poll_feeds()

    stored = await reread(db_session, feed)
    assert stored.cursor_attempts == 0, "no page was reached, so none was attempted"
    assert stored.last_error is not None

    # Dropbox comes back and one entry will not download. The page gets its own
    # full budget rather than inheriting the outage's.
    del fake.list_failures[WATCHED]
    flaky = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    fake.by_cursor = {None: page(flaky, cursor="cursor-1")}
    fake.download_failures[flaky["id"]] = server_error()
    await feeds.poll_feeds()

    stored = await reread(db_session, feed)
    assert stored.cursor is None, "not abandoned on the page's first refusal"
    assert stored.cursor_attempts == 1


async def test_an_invalid_cursor_relists_from_scratch_on_the_same_poll(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    entry = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    # The fake answers an unknown cursor with Dropbox's own `reset`.
    fake.by_cursor = {None: page(entry, cursor="cursor-fresh")}
    fake.files[entry["id"]] = ride_bytes()
    connection = await connect(db_session)
    feed = await watch(db_session, connection)
    stored = await reread(db_session, feed)
    stored.cursor = "cursor-from-last-year"
    await db_session.commit()

    await feeds.poll_feeds()

    assert len(fake.calls_to(LIST_FOLDER_CONTINUE_PATH)) == 1
    assert len(fake.calls_to(LIST_FOLDER_PATH)) == 1, "it re-listed on the same poll"
    stored = await reread(db_session, feed)
    assert stored.cursor == "cursor-fresh"
    assert stored.cursor_attempts == 0, "a reset is not the batch's fault"
    assert stored.last_error is None
    assert len(await rows_of(db_session, SessionRow)) == 1


async def test_a_relisting_that_also_fails_does_not_erase_the_stored_cursor(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    """A reset's own retry can fail too, and that must still touch nothing.

    The retry is passed `cursor=None` as an argument; it must not be written
    to `feed.cursor` before the retry is known to succeed, or a failure here
    would commit that write alongside `last_error` and erase the feed's last
    known position over a condition arc has not actually resolved.
    """
    fake.by_cursor = {}  # any cursor presented comes back `reset`
    fake.list_failures[WATCHED] = server_error()  # the retried opening listing
    connection = await connect(db_session)
    feed = await watch(db_session, connection)
    stored = await reread(db_session, feed)
    stored.cursor = "cursor-from-last-year"
    await db_session.commit()

    await feeds.poll_feeds()

    stored = await reread(db_session, feed)
    assert stored.cursor == "cursor-from-last-year", (
        "a listing failure after a reset must not erase the last known position"
    )
    assert stored.cursor_attempts == 0, "no page was reached, so none was attempted"
    assert stored.last_error is not None


# --- AC-17: a connector failure may not reach the local inbox ----------------------


async def test_a_dead_connector_does_not_stop_the_local_inbox_sweep(
    fake: FakeDropbox, db_session: AsyncSession, data_root: Path
) -> None:
    """The offline invariant: Dropbox is an addition, never a dependency."""
    for path in (LIST_FOLDER_PATH, LIST_FOLDER_CONTINUE_PATH, DOWNLOAD_PATH):
        fake.raises[path] = RuntimeError("the network is gone")
    connection = await connect(db_session)
    await watch(db_session, connection)
    dropped = data_root / "inbox" / "ride.fit"
    dropped.write_bytes(ride_bytes())

    await feeds.run_feed_poll_job()  # must not raise
    # Two sweeps: the settle rule never takes a file on its first sighting.
    await scan_inbox(settle_seconds=0)
    reports = await scan_inbox(settle_seconds=0)

    assert [report.outcome for report in reports] == [IngestOutcome.INGESTED]
    assert len(await rows_of(db_session, SessionRow)) == 1


async def test_with_the_connection_deleted_the_local_sweep_is_unaffected(
    fake: FakeDropbox, db_session: AsyncSession, data_root: Path
) -> None:
    dropped = data_root / "inbox" / "ride.fit"
    dropped.write_bytes(ride_bytes())

    await feeds.run_feed_poll_job()
    await scan_inbox(settle_seconds=0)
    reports = await scan_inbox(settle_seconds=0)

    assert fake.calls == []
    assert [report.outcome for report in reports] == [IngestOutcome.INGESTED]


async def test_an_unreadable_credential_is_marked_and_other_feeds_still_poll(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    # Sealed under a key this process does not hold: `error`, not
    # `needs_reauth` — the remedy is the operator restoring the key, not the
    # athlete reconnecting.
    stranger = Fernet(Fernet.generate_key())
    broken = await connect(
        db_session, credentials=stranger.encrypt(b'{"access_token":"x"}')
    )
    await watch(db_session, broken, remote_path="/broken")

    await feeds.poll_feeds()

    [refreshed] = await rows_of(db_session, ConnectionRow)
    assert refreshed.status is ConnectionStatus.ERROR
    assert refreshed.last_error is not None
    assert fake.calls == [], "arc cannot open the credential, so it asks nothing"


async def test_a_connection_disconnected_mid_sweep_does_not_end_the_sweep(
    fake: FakeDropbox,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one write `_due_feeds` makes while still enumerating can lose a race.

    Marking an unreadable credential `error` commits mid-enumeration, and since
    `app.persistence.db` learned to translate a stale UPDATE, losing that race
    raises `ConflictError` — which uncaught would abort the whole cycle, every
    connection and every feed, because the athlete pressed Disconnect while the
    sweep was running.

    A real race: a second session really deletes the row and the sweep's UPDATE
    really goes stale. Only the *moment* the athlete acts is forced, by
    standing in front of the commit, because a race left to chance is a test
    that passes for the wrong reason. Nothing raises the exception on the
    sweep's behalf — the point is that SQLAlchemy raises what this layer claims
    to catch (`test_db_write_failures.py`).

    Asserted through the sweep completing rather than through a surviving
    connection: `uq_connections_provider` plus a one-member `ConnectionProvider`
    means there is at most one connection to enumerate today, so "it carries on
    to the next one" has nothing to show yet. What is observable is the
    difference that matters — the cycle ends normally instead of in a
    traceback.
    """
    stranger = Fernet(Fernet.generate_key())
    broken = await connect(
        db_session, credentials=stranger.encrypt(b'{"access_token":"x"}')
    )
    await watch(db_session, broken, remote_path="/broken")
    connection_id = broken.id
    real_commit = feeds.commit
    disconnected = False

    async def disconnect_then_commit(session: AsyncSession) -> None:
        nonlocal disconnected
        if not disconnected:
            disconnected = True
            async with session_factory() as athlete:
                row = await athlete.get(ConnectionRow, connection_id)
                assert row is not None
                await athlete.delete(row)
                await athlete.commit()
        await real_commit(session)

    monkeypatch.setattr(feeds, "commit", disconnect_then_commit)

    with capture_logs() as logs:
        await feeds.run_feed_poll_job()

    assert disconnected, "the commit the race is arranged around was never reached"
    assert await rows_of(db_session, ConnectionRow) == [], "the athlete's delete won"
    events = [entry["event"] for entry in logs]
    assert "dropbox_connection_vanished" in events
    assert "dropbox_feed_poll_job_failed" not in events, (
        "one disconnected connection ended the whole sweep"
    )
    assert fake.calls == []


async def test_one_feeds_failure_does_not_stop_the_next_feed(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    connection = await connect(db_session)
    broken = await watch(db_session, connection, remote_path="/apps/broken")
    healthy = await watch(db_session, connection, remote_path=WATCHED)
    fake.list_failures["/apps/broken"] = server_error()
    entry = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    fake.by_cursor = {None: page(entry, cursor="cursor-1")}
    fake.files[entry["id"]] = ride_bytes()

    await feeds.poll_feeds()

    assert (await reread(db_session, broken)).last_error is not None
    assert (await reread(db_session, healthy)).cursor == "cursor-1"
    assert len(await rows_of(db_session, SessionRow)) == 1


async def test_a_feed_disabled_after_the_sweep_selected_it_is_not_polled(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    """`_due_feeds` and `_poll_feed` read the row at two different moments.

    A feed the athlete pauses in the gap between them must not still spend
    one more poll — `_poll_feed` is driven directly here, standing in for
    `_due_feeds` having already selected this feed a moment before it was
    disabled.
    """
    entry = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    fake.by_cursor = {None: page(entry, cursor="cursor-1")}
    fake.files[entry["id"]] = ride_bytes()
    connection = await connect(db_session)
    feed = await watch(db_session, connection)
    feed.enabled = False
    await db_session.commit()

    await feeds._poll_feed(feed.id)  # standing in for `_due_feeds` having just run

    assert fake.calls == []
    assert (await reread(db_session, feed)).cursor is None


async def test_a_feed_records_its_own_deliveries_in_the_audit_trail(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    # The per-feed delivery count `get_ingest_status` reports is counted off
    # this trail (there is no per-feed column), so the row has to be written.
    entry = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    fake.by_cursor = {None: page(entry, cursor="cursor-1")}
    fake.files[entry["id"]] = ride_bytes()
    connection = await connect(db_session)
    feed_id = (await watch(db_session, connection)).id

    await feeds.poll_feeds()

    deliveries = [
        row
        for row in await rows_of(db_session, AuditLogEntry)
        if row.action == feeds.DELIVERED_ACTION
    ]
    assert [row.entity_id for row in deliveries] == [feed_id]
    assert deliveries[0].actor == "system"
    assert isinstance(deliveries[0].payload_json.get("external_id"), str)
    assert uuid.UUID(str(deliveries[0].payload_json["session_id"]))


# --- AC-14/AC-15: what the integration provides decides where the files go ---------


async def classify(
    session: AsyncSession,
    feed: FeedRow,
    *,
    kind: IntegrationKind = IntegrationKind.WAHOO,
) -> IntegrationRow:
    """Attach a watched folder to an integration, as `0017` and `add` both do."""
    row = IntegrationRow(kind=kind)
    session.add(row)
    await session.flush()
    feed.integration_id = row.id
    await session.commit()
    return row


def provides_only_wellness(patched: pytest.MonkeyPatch) -> None:
    """Make the stored integration one that feeds `wellness` and nothing else.

    There is no shipped `IntegrationKind` whose `provides` is `{wellness}`, and
    there deliberately never will be one until arc can deliver it — the
    `CATALOGUE` docstring is the argument. Patching the catalogue the poll
    reads, rather than inventing an enum member, keeps the test on the branch
    production code actually takes: the poll asks `CATALOGUE[row.kind].provides`
    whatever the row's kind happens to be, which is the same question it will
    ask of Apple Health.
    """
    patched.setattr(
        feeds,
        "CATALOGUE",
        MappingProxyType(
            {
                IntegrationKind.WAHOO: IntegrationSpec(
                    kind=IntegrationKind.WAHOO,
                    display_name="Wahoo",
                    provides=frozenset({DataKind.WELLNESS}),
                    transports=(
                        TransportSpec(
                            kind=TransportKind.CLOUD_FOLDER,
                            storage=StorageProvider.DROPBOX,
                            default_path="/apps/wahoofitness",
                        ),
                    ),
                )
            }
        ),
    )


def pipeline_calls(patched: pytest.MonkeyPatch) -> list[str]:
    """Replace `IngestPipeline.ingest_file` with a fake that only counts.

    A fake rather than "no session row appeared": a file that reached the
    pipeline and quarantined leaves no session either, so the absence of a
    session cannot tell "never delivered" from "delivered and rejected". The
    claim AC-15 makes is about the *call*.
    """
    calls: list[str] = []

    async def never_called(self: Any, path: Path, **kwargs: Any) -> Any:
        calls.append(str(path))
        raise AssertionError("a wellness feed reached IngestPipeline.ingest_file")

    patched.setattr(IngestPipeline, "ingest_file", never_called)
    return calls


async def folder_states(session: AsyncSession) -> dict[str, list[FeedDeliveryState]]:
    """What the settings panel renders for each integration's folders."""
    views = await IntegrationService.from_session(session).list()
    return {
        view.display_name: [folder.state for folder in view.folders] for view in views
    }


async def test_the_transport_is_recorded_not_the_integration(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    """AC-14: a classified feed ingests exactly as an unclassified one did.

    The recording says `dropbox` — the transport that carried the bytes — and
    says nothing at all about Wahoo, which is configuration living on the feed.
    """
    connection = await connect(db_session)
    feed = await watch(db_session, connection)
    feed_id = feed.id
    await classify(db_session, feed)
    entry = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    fake.by_cursor = {None: page(entry, cursor="cursor-1")}
    fake.files[entry["id"]] = ride_bytes()

    await feeds.poll_feeds()

    [session_row] = await rows_of(db_session, SessionRow)
    [recording] = await rows_of(db_session, RecordingRow)
    assert recording.session_id == session_row.id
    assert recording.source == IngestSource.DROPBOX.value == "dropbox"
    assert recording.external_id == entry["id"]
    # The integration kind is in no column of `recordings` — not by name and
    # not by value. It is recoverable through the feed, and nowhere else.
    columns = [column.name for column in RecordingRow.__table__.columns]
    assert [name for name in columns if "integration" in name] == []
    stored_values = [str(getattr(recording, name)).lower() for name in columns]
    assert [
        value for value in stored_values if IntegrationKind.WAHOO.value in value
    ] == []
    # And the feed's own delivery ledger is unchanged.
    deliveries = [
        row
        for row in await rows_of(db_session, AuditLogEntry)
        if row.action == feeds.DELIVERED_ACTION
    ]
    assert [row.entity_id for row in deliveries] == [feed_id]


async def test_an_unclassified_feed_still_ingests(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    """AC-14 edge: a folder configured before integrations existed keeps going.

    `integration_id IS NULL` means "not yet classified", never "do not
    collect": these rows are the installations that predate this vocabulary,
    and stopping them would lose rides over a schema change.
    """
    connection = await connect(db_session)
    feed = await watch(db_session, connection)
    entry = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    fake.by_cursor = {None: page(entry, cursor="cursor-1")}
    fake.files[entry["id"]] = ride_bytes()

    await feeds.poll_feeds()

    stored = await reread(db_session, feed)
    assert stored.integration_id is None
    assert stored.last_error is None
    [session_row] = await rows_of(db_session, SessionRow)
    [recording] = await rows_of(db_session, RecordingRow)
    assert recording.session_id == session_row.id
    assert recording.source == IngestSource.DROPBOX.value == "dropbox"


async def test_a_wellness_feed_is_never_handed_to_the_pipeline(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    """AC-15: a feed arc has no destination for is refused, loudly."""
    connection = await connect(db_session)
    feed = await watch(db_session, connection)
    await classify(db_session, feed)
    entry = file_entry("weight.fit", f"{WATCHED}/weight.fit")
    fake.by_cursor = {None: page(entry, cursor="cursor-1")}
    fake.files[entry["id"]] = ride_bytes()

    with pytest.MonkeyPatch.context() as patched:
        provides_only_wellness(patched)
        calls = pipeline_calls(patched)

        await feeds.poll_feeds()

        assert calls == []
    assert downloads(fake) == []
    assert await rows_of(db_session, SessionRow) == []
    stored = await reread(db_session, feed)
    assert stored.last_error is not None
    assert DataKind.WELLNESS.value in stored.last_error
    # The cursor never moved past the file arc did not deliver, and no attempt
    # was spent: there is nothing here to give up on.
    assert stored.cursor is None
    assert stored.cursor_attempts == 0
    assert stored.last_delivery_at is None
    # And both reads say the same word about it.
    status = await ConnectionService.from_session(db_session).ingest_status()
    assert [
        folder.state
        for integration in status.integrations
        for folder in integration.folders
    ] == [FeedDeliveryState.FAILING]
    assert (await folder_states(db_session))["Wahoo"] == [FeedDeliveryState.FAILING]


async def test_a_wellness_feed_is_retried_rather_than_treated_as_finished(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    """AC-15 edge: the next poll tries again, and nothing was skipped meanwhile.

    Proved the only way that cannot be faked by a held cursor: once the
    destination exists, the very file the refused polls never delivered is
    still offered and still becomes a session.
    """
    connection = await connect(db_session)
    feed = await watch(db_session, connection)
    await classify(db_session, feed)
    entry = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    fake.by_cursor = {None: page(entry, cursor="cursor-1")}
    fake.files[entry["id"]] = ride_bytes()

    with pytest.MonkeyPatch.context() as patched:
        provides_only_wellness(patched)
        calls = pipeline_calls(patched)

        await feeds.poll_feeds()
        await feeds.poll_feeds()

        assert calls == []
    refused = await reread(db_session, feed)
    assert refused.cursor is None
    assert refused.cursor_attempts == 0
    assert refused.last_error is not None

    await feeds.poll_feeds()

    [session_row] = await rows_of(db_session, SessionRow)
    delivered = await reread(db_session, feed)
    assert delivered.cursor == "cursor-1"
    assert delivered.last_error is None
    assert session_row.id is not None


# --- AC-8/AC-9: the poll is what makes `connected` an observation -------------------
#
# `connected` used to mean "nothing has told arc otherwise", and nothing ever
# asked. The poll already touches Dropbox every couple of minutes on behalf of
# every watched folder, so these two facts are by-products of work arc does
# anyway: a listing that succeeded stamps `last_verified_at`, and a listing
# refused for want of a scope flips the row on the spot.


async def reread_connection(
    session: AsyncSession, connection: ConnectionRow
) -> ConnectionRow:
    """The connection as the database now has it."""
    statement = (
        select(ConnectionRow)
        .where(ConnectionRow.id == connection.id)
        .execution_options(populate_existing=True)
    )
    fetched = (await session.execute(statement)).scalars().first()
    assert fetched is not None
    return fetched


async def test_a_poll_that_listed_the_folder_stamps_the_connection(
    fake: FakeDropbox, db_session: AsyncSession, client: AsyncClient, data_root: Path
) -> None:
    """AC-8: a resolved poll is when arc last saw the credential work.

    The window is bounded on both sides — a stamp inside `[before, after]`
    could only have been written by this poll, where `is not None` would pass
    for a value the connect wrote minutes earlier.
    """
    connection = await connect(db_session)
    await watch(db_session, connection)
    entry = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    fake.by_cursor = {None: page(entry, cursor="cursor-1")}
    fake.files[entry["id"]] = ride_bytes()
    before = dt.datetime.now(dt.UTC)

    await feeds.poll_feeds()
    after = dt.datetime.now(dt.UTC)

    stored = await reread_connection(db_session, connection)
    assert stored.last_verified_at is not None
    assert before <= stored.last_verified_at <= after
    assert stored.status is ConnectionStatus.CONNECTED

    # And it reaches the panel: a stamp nobody can read verifies nothing.
    response = await client.get(f"/api/v1/connections/{connection.id}")
    assert response.status_code == 200, response.text
    served = dt.datetime.fromisoformat(response.json()["last_verified_at"])
    assert served == stored.last_verified_at


async def test_a_poll_that_finds_nothing_new_still_stamps_the_connection(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    """AC-8 edge: the listing succeeded, which is the whole question.

    A rest week must not read as an unverified credential — otherwise "last
    checked" would go stale on a quiet folder and say the account is in doubt
    when the only thing that stopped is the riding.
    """
    fake.by_cursor = {None: page(cursor="cursor-1")}
    connection = await connect(db_session)
    await watch(db_session, connection)
    before = dt.datetime.now(dt.UTC)

    await feeds.poll_feeds()

    stored = await reread_connection(db_session, connection)
    assert stored.last_verified_at is not None
    assert stored.last_verified_at >= before
    assert await rows_of(db_session, SessionRow) == []


async def test_a_download_that_fails_after_a_good_listing_still_stamps_it(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    """AC-8: the *listing* is the evidence, and it was answered.

    A file arc could not fetch says something about that file; it says nothing
    about whether the credential can read the athlete's Dropbox, which Dropbox
    had already demonstrated one call earlier by answering the listing.
    """
    entry = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    fake.by_cursor = {None: page(entry, cursor="cursor-1")}
    fake.download_failures[entry["id"]] = server_error()
    connection = await connect(db_session)
    feed = await watch(db_session, connection)
    before = dt.datetime.now(dt.UTC)

    await feeds.poll_feeds()

    stored = await reread_connection(db_session, connection)
    assert stored.last_verified_at is not None
    assert stored.last_verified_at >= before
    assert (await reread(db_session, feed)).last_error is not None


async def test_a_failed_listing_does_not_move_an_earlier_stamp(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    """AC-8 edge: "last checked" may only ever mean *checked, and it worked*.

    A stamp that moved on a failure would make an account nobody has
    successfully reached in a week read as freshly verified — the exact
    reassurance-without-evidence this column exists to remove.
    """
    fake.list_failures[WATCHED] = server_error()
    connection = await connect(db_session)
    await watch(db_session, connection)
    yesterday = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    stored = await reread_connection(db_session, connection)
    stored.last_verified_at = yesterday
    await db_session.commit()

    await feeds.poll_feeds()

    stored = await reread_connection(db_session, connection)
    assert stored.last_verified_at == yesterday


async def test_a_never_verified_connection_stays_null_through_a_failed_poll(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    """AC-8 edge: no stamp is a state, and a failure may not invent one."""
    fake.list_failures[WATCHED] = server_error()
    connection = await connect(db_session)
    await watch(db_session, connection)

    await feeds.poll_feeds()

    assert (await reread_connection(db_session, connection)).last_verified_at is None


async def test_a_scope_refusal_in_the_poll_flips_the_row_in_one_cycle(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    """AC-9: a permission withdrawn behind arc's back surfaces on one poll.

    Nothing in arc changed and nothing in arc would ever notice: a browse-time
    refusal is left as one screen's error, because the athlete is standing in
    front of it. The poll is the only thing that asks unprompted, so it is
    where the row has to learn — and on the *first* refusal, because refreshing
    cannot mint a scope and a second attempt would buy no new evidence.
    """
    fake.list_failures[WATCHED] = missing_scope("files.metadata.read")
    connection = await connect(db_session)
    feed = await watch(db_session, connection)

    await feeds.poll_feeds()

    stored = await reread_connection(db_session, connection)
    assert stored.status is ConnectionStatus.NEEDS_REAUTH
    assert stored.last_error is not None
    # The four console moves, named: the athlete cannot guess that a newly
    # ticked permission only reaches a grant made after Submit.
    assert "files.metadata.read" in stored.last_error
    assert "Permissions" in stored.last_error
    assert "Submit" in stored.last_error
    # And the folder says why nothing is arriving, without repeating the
    # remedy the account line above it already carries.
    refused = await reread(db_session, feed)
    assert refused.last_error is not None
    assert WATCHED in refused.last_error
    assert refused.cursor_attempts == 0, "the page was never reached"


async def test_a_flip_stops_the_rest_of_the_same_cycle_dead(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    """AC-9: the flip takes effect on the feed after it, not on the next cycle.

    Two folders, one credential. The first is refused for want of a scope and
    flips the row; the second is now being polled with a credential arc has
    just watched Dropbox refuse. It must not ask — and specifically must not
    ask the *token* endpoint, because `mark_needs_reauth` clears the expiry, so
    a second feed that built a client would refresh before its listing. That
    refresh used to heal the row back to `connected`, and if the listing behind
    it then failed with anything that is not a scope refusal the cycle ended
    `connected` with no error at all: an account arc had proved was broken,
    reported as working, for ever.
    """
    fake.list_failures[ALSO_WATCHED] = missing_scope("files.metadata.read")
    fake.by_cursor = {None: page(cursor="cursor-1")}
    connection = await connect(db_session)
    refused = await watch(db_session, connection, remote_path=ALSO_WATCHED)
    untouched = await watch(db_session, connection, remote_path=WATCHED)

    await feeds.poll_feeds()

    # One listing — the refused one — and no token request behind it.
    assert len(fake.calls_to(LIST_FOLDER_PATH)) == 1
    assert fake.calls_to(LIST_FOLDER_PATH)[0].body["path"] == ALSO_WATCHED
    assert fake.calls_to(TOKEN_PATH) == []

    stored = await reread_connection(db_session, connection)
    assert stored.status is ConnectionStatus.NEEDS_REAUTH
    assert stored.last_error is not None
    # The scope sentence survives the rest of the cycle intact.
    assert "files.metadata.read" in stored.last_error
    assert "Permissions" in stored.last_error
    assert "Submit" in stored.last_error

    assert (await reread(db_session, refused)).last_error is not None
    # The second folder is not at fault and is not blamed: it was never asked.
    second = await reread(db_session, untouched)
    assert second.last_error is None
    assert second.cursor_attempts == 0


async def test_a_second_feed_failing_transiently_cannot_undo_the_flip(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    """AC-9: the zombie, written as the cycle that used to produce it.

    Scope refusal on the first folder, a 429 on the second. Every step of the
    old path was individually defensible — refresh a token whose expiry was
    cleared, treat a 200 from the token endpoint as the connection working,
    treat a 429 as transient — and together they left the row `connected` with
    `last_error` null, describing a Dropbox arc could not read.
    """
    fake.list_failures[ALSO_WATCHED] = missing_scope("files.metadata.read")
    fake.list_failures[WATCHED] = rate_limited("42")
    connection = await connect(db_session)
    await watch(db_session, connection, remote_path=ALSO_WATCHED)
    await watch(db_session, connection, remote_path=WATCHED)

    await feeds.poll_feeds()

    stored = await reread_connection(db_session, connection)
    assert stored.status is ConnectionStatus.NEEDS_REAUTH
    assert "files.metadata.read" in (stored.last_error or "")
    # The 429 was never even collected: the second feed made no request.
    assert len(fake.calls_to(LIST_FOLDER_PATH)) == 1
    assert fake.calls_to(TOKEN_PATH) == []


async def test_a_download_refused_for_a_scope_flips_the_connection(
    fake: FakeDropbox, db_session: AsyncSession, data_root: Path
) -> None:
    """AC-9: a revoked `files.content.read` is only ever visible here.

    The listing that precedes the download needs `files.metadata.read` and
    succeeds — stamping `last_verified_at` on the way past — so treating the
    download refusal as transient left the panel saying "connected, last
    checked just now" over a feed that would never download another ride
    again. Dropbox named the scope, which is proof, not a hedge.
    """
    entry = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    fake.by_cursor = {None: page(entry, cursor="cursor-1")}
    fake.download_failures[entry["id"]] = missing_scope("files.content.read")
    connection = await connect(db_session)
    feed = await watch(db_session, connection)

    await feeds.poll_feeds()

    stored = await reread_connection(db_session, connection)
    assert stored.status is ConnectionStatus.NEEDS_REAUTH
    assert stored.last_error is not None
    assert "files.content.read" in stored.last_error
    assert "Permissions" in stored.last_error
    assert "Submit" in stored.last_error

    # The page is not blamed and the cursor stays put: nothing was taken.
    refused = await reread(db_session, feed)
    assert refused.cursor is None
    assert refused.cursor_attempts == 0
    assert refused.last_error is not None
    assert WATCHED in refused.last_error
    assert await rows_of(db_session, SessionRow) == []


async def test_a_download_refused_with_a_403_scope_flips_and_keeps_the_cursor(
    fake: FakeDropbox, db_session: AsyncSession, data_root: Path
) -> None:
    """Dropbox spells `missing_scope` with 403 too, and this path read only 401.

    The consequence was silent data loss. Unclassified, the 403 fell through to
    `DropboxUpstreamError`, `_take_batch` blamed the *page* for it, and after
    `max_batch_attempts` polls the cursor advanced past a page whose file had
    never been downloaded — the ride gone for good, while the panel read
    "connected, last checked just now" because the listing before it had
    stamped `last_verified_at`.
    """
    entry = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    fake.by_cursor = {None: page(entry, cursor="cursor-1")}
    fake.download_failures[entry["id"]] = missing_scope(
        "files.content.read", status=403
    )
    connection = await connect(db_session)
    feed = await watch(db_session, connection)

    await feeds.poll_feeds()

    stored = await reread_connection(db_session, connection)
    assert stored.status is ConnectionStatus.NEEDS_REAUTH
    assert "files.content.read" in (stored.last_error or "")

    refused = await reread(db_session, feed)
    # The cursor did not move and no attempt was spent, so nothing is on the
    # road to being skipped: the file is still there to be collected.
    assert refused.cursor is None
    assert refused.cursor_attempts == 0
    assert await rows_of(db_session, SessionRow) == []


async def test_a_download_the_account_is_refused_blames_neither_page_nor_row(
    fake: FakeDropbox, db_session: AsyncSession, data_root: Path
) -> None:
    """A 403 naming no scope is the account, and neither remedy on offer fits.

    Blaming the page would spend the give-up budget and eventually advance the
    cursor past a ride that was never downloaded; flipping the row would offer
    a reconnect that cannot clear a team policy and would cost every feed on
    the account. The same download works the moment the athlete changes
    something at dropbox.com, so arc says that and keeps asking.
    """
    entry = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    fake.by_cursor = {None: page(entry, cursor="cursor-1")}
    fake.download_failures[entry["id"]] = no_access()
    connection = await connect(db_session)
    feed = await watch(db_session, connection)

    await feeds.poll_feeds()

    stored = await reread_connection(db_session, connection)
    assert stored.status is ConnectionStatus.CONNECTED
    assert stored.last_error is None

    refused = await reread(db_session, feed)
    assert refused.last_error == ACCOUNT_NO_ACCESS
    assert refused.cursor is None
    assert refused.cursor_attempts == 0
    assert await rows_of(db_session, SessionRow) == []


async def test_a_listing_the_account_is_refused_keeps_asking_every_cycle(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    """The listing half: same sentence, no flip, and the next cycle still polls.

    A flip would be the expensive mistake here. `_due_feeds` skips a connection
    that is not `connected`, so a row flipped over an account condition stops
    being polled entirely — and the condition is one that clears without arc
    being told, on an account arc cannot see. So the status holds, the folder
    says where the answer is, and the poll asks again.
    """
    fake.list_failures[WATCHED] = no_access()
    connection = await connect(db_session)
    feed = await watch(db_session, connection)

    await feeds.poll_feeds()

    stored = await reread_connection(db_session, connection)
    assert stored.status is ConnectionStatus.CONNECTED
    assert stored.last_error is None

    refused = await reread(db_session, feed)
    assert refused.last_error == ACCOUNT_NO_ACCESS
    assert "Disconnect" not in refused.last_error
    assert "Reconnect" not in refused.last_error
    assert refused.cursor is None
    assert refused.cursor_attempts == 0, "the page was never reached"

    # And the next cycle asks again rather than going quiet.
    spent = len(fake.calls_to(LIST_FOLDER_PATH))
    await feeds.poll_feeds()
    assert len(fake.calls_to(LIST_FOLDER_PATH)) == spent + 1


async def test_the_connection_a_refused_download_flipped_is_not_polled_again(
    fake: FakeDropbox, db_session: AsyncSession, data_root: Path
) -> None:
    """AC-9: one refusal is enough, whichever call met it.

    The old behaviour recorded "arc tries again at the next check" and then did
    exactly that, every two minutes, for ever — against a permission only the
    athlete can restore.
    """
    entry = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    fake.by_cursor = {None: page(entry, cursor="cursor-1")}
    fake.download_failures[entry["id"]] = missing_scope("files.content.read")
    connection = await connect(db_session)
    await watch(db_session, connection)
    await feeds.poll_feeds()
    spent = len(fake.calls)

    await feeds.poll_feeds()

    assert len(fake.calls) == spent


async def test_the_flipped_connection_is_not_polled_again(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    """AC-9: one refusal is enough — the next cycle spends no request.

    The rate limit is a shared budget, and asking a credential arc has already
    watched Dropbox refuse is the request a genuine folder wants later.
    """
    fake.list_failures[WATCHED] = missing_scope("files.metadata.read")
    connection = await connect(db_session)
    await watch(db_session, connection)
    await feeds.poll_feeds()
    spent = len(fake.calls)

    await feeds.poll_feeds()

    assert len(fake.calls) == spent


async def test_the_flip_keeps_the_stamp_of_when_it_last_worked(
    fake: FakeDropbox, db_session: AsyncSession
) -> None:
    """AC-9: "it broke" and "nobody ever checked" are different sentences.

    Clearing the stamp on the flip would replace a true "last worked at 14:02"
    with "not checked yet", which reads as a connection nobody has looked at
    rather than one that has just stopped working.
    """
    fake.list_failures[WATCHED] = missing_scope("files.metadata.read")
    connection = await connect(db_session)
    await watch(db_session, connection)
    worked_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
    stored = await reread_connection(db_session, connection)
    stored.last_verified_at = worked_at
    await db_session.commit()

    await feeds.poll_feeds()

    stored = await reread_connection(db_session, connection)
    assert stored.status is ConnectionStatus.NEEDS_REAUTH
    assert stored.last_verified_at == worked_at


async def test_no_failure_the_poll_records_names_a_token_or_the_api(
    fake: FakeDropbox, db_session: AsyncSession, data_root: Path
) -> None:
    """Every sentence this module stores is one an athlete can act on.

    A sweep rather than one assertion per branch: `last_error` is rendered
    verbatim in Settings, and the failure mode is a *new* branch quoting a
    connector diagnostic into it — which no test of the existing branches would
    catch. The four words are the ones the audited run-through produced;
    Dropbox's own diagnostics still reach the log, which is where they help.
    """
    banned = ("token", "credential", "the API", "401")
    entry = file_entry("ride.fit", f"{WATCHED}/ride.fit")
    failures: dict[str, Any] = {
        "scope": missing_scope("files.metadata.read"),
        "dead": expired_access_token(),
        "throttled": rate_limited("42"),
        "broken": server_error(),
        "gone": path_not_found(WATCHED),
        "account": no_access(),
    }
    for name, response in failures.items():
        fake.calls.clear()
        fake.list_failures = {WATCHED: response}
        fake.by_cursor = {None: page(entry, cursor=f"cursor-{name}")}
        async with session_scope() as session:
            for row in await rows_of(session, ConnectionRow):
                await session.delete(row)
            await session.commit()
        connection = await connect(db_session)
        feed = await watch(db_session, connection)

        await feeds.poll_feeds()

        stored = await reread(db_session, feed)
        assert stored.last_error is not None, name
        for word in banned:
            assert word not in stored.last_error, f"{name}: {stored.last_error}"
        refreshed = await reread_connection(db_session, connection)
        for word in banned:
            assert word not in (refreshed.last_error or ""), name

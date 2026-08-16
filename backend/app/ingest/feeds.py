"""The scheduled poll that makes a watched Dropbox folder deliver.

Every ``DROPBOX__POLL_INTERVAL_SECONDS`` each enabled feed asks Dropbox what
has changed since its stored cursor, downloads the activity files in the
answer, and hands each one to `app.ingest.pipeline` — the same pipeline an
upload and the local `data/inbox/` sweep go through, so a ride that arrives
this way is indistinguishable downstream from one dropped in by hand, except
that its recording says which transport carried it.

**An interval job, not `list_folder/longpoll`.** Dropbox offers a long-poll
endpoint that answers the moment a file lands, and it would give push-quality
latency. It is not used, and the reason is not the endpoint: a long poll is a
30-second-plus request, so it has to live in a long-lived asyncio task, and
this codebase has **no** task supervision. `app.main`'s shutdown awaits
nothing, nothing restarts a crashed task, and nothing bounds one that wedges.
Adopting longpoll here would mean shipping a supervision primitive, an
orderly-shutdown path and a restart-on-crash policy *alongside* the feature —
three new failure modes in the component whose whole job is to fail loudly.
The trade it buys is small: at a 120 s interval one cursor call per feed is
~720 requests/day against a limit measured in thousands, and the
ELEMNT→phone→Dropbox leg already costs minutes, so longpoll would shave a
fraction off a lag arc does not control. Revisit it when arc has supervised
background tasks for another reason, or when a feed's latency is genuinely
the complaint.

**A module of its own, and not part of `app.ingest.inbox`.** The poll has to
call `IngestPipeline`, and the layer contract forbids `app.services` from
importing `app.ingest`, so it cannot live beside `ConnectionService`. It is
not folded into `inbox.py` either: that module is the local-folder invariant —
arc ingests files with no network at all — and a connector failure must not be
able to reach it. They are two jobs on one scheduler, sharing a pipeline and
nothing else, which is what makes AC-17's "every Dropbox call raises and the
local sweep still works" true by construction rather than by care.

`poll_feeds` is an ordinary coroutine, so a test drives it directly against
the fake upstream — no scheduler, no sleeping.
"""

import asyncio
import datetime as dt
import uuid
from dataclasses import dataclass
from pathlib import Path

from apscheduler.schedulers.base import BaseScheduler
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.dropbox import (
    DropboxChanges,
    DropboxClient,
    DropboxCursorResetError,
    DropboxError,
    DropboxFile,
    DropboxRateLimitedError,
)
from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.activity import IngestOutcome, IngestSource
from app.domain.actor import Actor
from app.domain.connections import FEED_DELIVERED_ACTION, ConnectionStatus
from app.ingest.parsers import extension_of
from app.ingest.pipeline import FileOrigin, IngestPaths, IngestPipeline
from app.ingest.service import MAX_UPLOAD_BYTES, safe_filename, staged_name
from app.persistence.audit import AuditRepository
from app.persistence.connections import (
    MAX_ERROR_LENGTH,
    ConnectionRepository,
    CredentialDecryptionError,
    CredentialKeyError,
    EncryptedCredentials,
    FeedRow,
)
from app.persistence.db import commit, session_scope
from app.persistence.ingest_log import (
    MAX_DETAIL_LENGTH,
    MAX_FILENAME_LENGTH,
    IngestEventRepository,
)

logger = get_logger(__name__)

#: Job id under which the poll is registered with APScheduler.
FEED_POLL_JOB_ID = "dropbox_feed_poll"

#: `entity_type` / `action` written on this module's audit rows.
FEED_ENTITY = "feed"
#: One row per file a feed actually turned into a session.
#:
#: This trail **is** the per-feed delivery ledger `get_ingest_status` counts —
#: there is no per-feed column and there does not need to be one, because the
#: audit log already records every write with the entity it was about. A
#: counter beside it could disagree with it, and the one that would be believed
#: is the one nobody is looking at (the argument `AuditRepository.count_since`
#: makes for the write cap, applied again). The name is spelled in
#: `app.domain.connections` because the reader lives one layer below this one.
DELIVERED_ACTION = FEED_DELIVERED_ACTION

#: The only extensions arc will spend a download on.
#:
#: Everything else is skipped *without* downloading it and **without an ingest
#: event**. The alternative — download everything and let the pipeline
#: quarantine what it cannot read — was rejected because a real Dropbox folder
#: holds screenshots, CSV exports and the odd PDF, and a quarantine queue full
#: of files that were never rides is a queue nobody reads. The queue's value is
#: that everything in it is a decision somebody has to take.
ACTIVITY_EXTENSIONS = frozenset({"fit", "gpx", "tcx"})

#: Filenames that are never a finished activity, matched lowercased.
#:
#: Zwift writes `inProgressActivity.fit` continuously *during* a ride and
#: leaves it behind un-renamed when it crashes, so it is a real `.fit` file
#: containing a real but truncated ride. Letting it settle and ingest produces
#: a session that looks legitimate and is short — the worst kind of wrong,
#: because nothing downstream can tell it from an easy day.
SKIPPED_NAMES = frozenset({"inprogressactivity.fit"})


@dataclass(frozen=True, slots=True)
class _Refusal:
    """Why an entry was not taken, and whether it is worth a log row."""

    detail: str
    #: Whether this earns an `ingest_events` row. Reserved for refusals the
    #: athlete would otherwise never learn about — a ride-sized file arc
    #: declined. A `.txt` is not news.
    recorded: bool


def _should_take(entry: DropboxFile) -> _Refusal | None:
    """Decide an entry from its listing alone. ``None`` means download it.

    **Size is refused from the listing's own ``size`` field, before any
    download.** Refusing after downloading would be simpler and would pull a
    2 GB file in the watched folder across the network for the privilege of
    rejecting it — on a home connection, repeatedly, because the cursor only
    moves once the batch resolves. The bound is
    :data:`app.ingest.service.MAX_UPLOAD_BYTES`, shared with the upload
    endpoint deliberately: "how large a file will arc accept" is one answer,
    and two would eventually disagree.
    """
    if entry.name.lower() in SKIPPED_NAMES:
        return _Refusal(
            f"{entry.name} is written while a ride is still in progress", recorded=False
        )
    if extension_of(Path(entry.name)) not in ACTIVITY_EXTENSIONS:
        return _Refusal(f"{entry.name} is not an activity file", recorded=False)
    if entry.size > MAX_UPLOAD_BYTES:
        return _Refusal(
            f"this Dropbox file is {entry.size} bytes, above arc's "
            f"{MAX_UPLOAD_BYTES} byte limit, so it was not downloaded",
            recorded=True,
        )
    return None


# --- the sweep ----------------------------------------------------------------


async def poll_feeds() -> None:
    """Poll every enabled feed on every usable connection, one feed at a time.

    A connection that is not `connected` is skipped **without a request**:
    spending a call to be told what the row already says is a call the rate
    limit will want later, and a `needs_reauth` credential is not going to
    start working because arc tried it again.

    One feed's failure may not end the sweep, so each is inside its own
    ``try`` — the same guard `scan_inbox` puts around each file, one level up.
    """
    async with session_scope() as session:
        due = await _due_feeds(session)
    for feed_id in due:
        try:
            await _poll_feed(feed_id)
        except Exception:  # noqa: BLE001 — one feed may not end the sweep
            logger.exception("dropbox_feed_poll_failed", feed_id=str(feed_id))
        await asyncio.sleep(0)


async def _due_feeds(session: AsyncSession) -> list[uuid.UUID]:
    """The feeds worth polling, marking any connection arc cannot open.

    An unreadable credential is **persisted** as `error` here, unlike the read
    path in `ConnectionService._settle_readability` which only downgrades the
    row in memory. The difference is that this is the moment arc actually
    needed the credential and could not use it: the settings panel and the
    coach's own `get_ingest_status` both read that status, and a feed that has
    silently stopped collecting is the failure this whole feature exists to
    make visible.
    """
    due: list[uuid.UUID] = []
    for connection in await ConnectionRepository(session).list():
        if connection.status is not ConnectionStatus.CONNECTED:
            logger.info(
                "dropbox_connection_not_usable",
                connection_id=str(connection.id),
                status=connection.status.value,
            )
            continue
        try:
            EncryptedCredentials.unseal(connection.credentials)
        except (CredentialDecryptionError, CredentialKeyError) as exc:
            connection.status = ConnectionStatus.ERROR
            connection.last_error = str(exc)[:MAX_ERROR_LENGTH]
            await commit(session)
            logger.warning(
                "dropbox_credential_unreadable", connection_id=str(connection.id)
            )
            continue
        due.extend(feed.id for feed in connection.feeds if feed.enabled)
    return due


async def _poll_feed(feed_id: uuid.UUID) -> None:
    """List one feed's changes, take what is in them, and move the cursor.

    **The cursor advances only after *every* entry in the batch is resolved.**
    Advancing per entry is the obvious alternative and it is the dangerous one:
    a failure half-way through would leave the cursor past files that were
    never taken, and they would never be offered again — a ride silently lost,
    which is the exact failure this feature exists to prevent. Re-delivery
    costs nothing: rung-1 sha256 dedup turns every entry the replay has
    already taken into a `duplicate_file` log line rather than a second
    session. The cursor lives in the row, so this survives a restart mid-batch
    for free.

    **One `session_scope()` per file**, as the local sweep does — opened inside
    :func:`_deliver`, not here. One transaction for the batch would let a
    poison file roll back the rides ingested beside it, and the pipeline's own
    catch-all needs a usable session to write its quarantine record on.
    The session opened here is the *control* session: it holds the feed row,
    the connection whose token the client may refresh, and the refusal log.
    """
    async with session_scope() as session:
        repository = ConnectionRepository(session)
        feed = await repository.get_feed(feed_id)
        if feed is None:  # deleted between the sweep's read and now
            return
        connection = await repository.get(feed.connection_id)
        if connection is None:  # pragma: no cover — cascade removes the feed
            return
        client = DropboxClient(
            session,
            connection,
            app_key=get_settings().dropbox.app_key.get_secret_value(),
        )
        try:
            changes = await _list_changes(client, feed)
        except DropboxError as exc:
            # No batch, so nothing to advance past: the listing itself failed.
            await _record_failure(
                session,
                feed,
                detail=f"Dropbox would not list {feed.remote_path or '/'}: {exc}",
                advance_to=None,
            )
            return

        failure = await _take_batch(session, client, feed, changes)
        if failure is not None:
            await _record_failure(
                session, feed, detail=failure, advance_to=changes.cursor
            )
            return

        feed.cursor = changes.cursor
        feed.cursor_attempts = 0
        feed.last_error = None
        # "Heard from Dropbox at all", per the column's own definition — set on
        # every resolved batch, including an empty one. That is what makes it a
        # *silence* signal: a stale value means the pipe is broken, where a
        # value that only moved on an ingest would go stale on a rest week and
        # look identical.
        feed.last_delivery_at = dt.datetime.now(dt.UTC)
        await commit(session)
        logger.info(
            "dropbox_feed_polled",
            feed_id=str(feed.id),
            entries=len(changes.entries),
            has_more=changes.has_more,
        )


async def _list_changes(client: DropboxClient, feed: FeedRow) -> DropboxChanges:
    """One page of changes, re-listing from scratch if the cursor is stale.

    A `reset` is Dropbox saying the stored position is too old to continue
    from, and the remedy is entirely local: forget it and list the folder
    again, on this same poll. It is deliberately **not** counted as a failed
    attempt — nothing is wrong with the batch, and letting resets accumulate
    toward the give-up budget would skip real files over a condition arc fixes
    by itself. Everything already ingested is deduplicated by hash on the way
    back through, so a full re-listing costs log lines, not sessions.
    """
    try:
        return await client.changes(path=feed.remote_path, cursor=feed.cursor)
    except DropboxCursorResetError:
        logger.info("dropbox_cursor_reset", feed_id=str(feed.id), path=feed.remote_path)
        feed.cursor = None
        return await client.changes(path=feed.remote_path, cursor=None)


async def _take_batch(
    session: AsyncSession,
    client: DropboxClient,
    feed: FeedRow,
    changes: DropboxChanges,
) -> str | None:
    """Download and ingest every activity file in one page.

    Returns ``None`` when the whole page resolved, or a sentence naming the
    entry that stopped it. Stopping at the first failure rather than carrying
    on is what makes the batch rule meaningful: the cursor is not going to
    advance either way, so pulling the rest would be bytes moved twice.
    """
    events = IngestEventRepository(session)
    for entry in changes.entries:
        refusal = _should_take(entry)
        if refusal is not None:
            if refusal.recorded:
                await events.record(
                    filename=entry.name[:MAX_FILENAME_LENGTH],
                    file_hash=None,
                    outcome=IngestOutcome.ERROR,
                    detail=refusal.detail[:MAX_DETAIL_LENGTH],
                )
                await commit(session)
            else:
                logger.info(
                    "dropbox_entry_skipped", name=entry.name, reason=refusal.detail
                )
            continue

        try:
            content = await client.download(entry.id)
        except DropboxRateLimitedError as exc:
            # Stop, do not walk into the next entry: Dropbox has said how long
            # it wants arc to wait, and the next request would be the one that
            # earns a longer ban.
            return (
                f"Dropbox asked arc to wait {int(exc.retry_after)} seconds before "
                f"fetching {entry.name}; the rest of this batch waits for the "
                "next poll"
            )
        except DropboxError as exc:
            return f"{entry.name} could not be downloaded: {exc}"

        await _deliver(feed_id=feed.id, entry=entry, content=content)
    return None


async def _deliver(*, feed_id: uuid.UUID, entry: DropboxFile, content: bytes) -> None:
    """Stage one downloaded file and run it through the pipeline, now.

    Staged into `data/inbox/` under the *upload* path's own naming — the same
    `safe_filename` / `staged_name` pair — for the same two reasons it uses
    them: the name is written to disk so it is rebuilt from a safe alphabet
    rather than inspected, and the ``<uuid7>-`` prefix keeps two files called
    `activity.fit` from overwriting each other (they are the same file by hash
    or they are not the same file at all). Keeping the extension through
    sanitising is load-bearing: it is what `extension_of` dispatches a parser
    on, so a name mangled into extensionlessness would make a perfectly good
    ride an `unreadable_file`.

    The pipeline is called **directly**, exactly as `IngestService.upload`
    does, so the inbox's two-sightings settle rule is bypassed. That rule
    exists because a file appearing in a watched directory may still be
    arriving; this file is one arc wrote itself, from bytes it has already
    finished receiving, so waiting for it to settle would delay every ride by
    a sweep interval to protect against a race that cannot happen here. The
    pipeline removes the staged copy once its rows are committed.
    """
    paths = IngestPaths.from_settings()
    paths.inbox.mkdir(parents=True, exist_ok=True)
    name = safe_filename(entry.name)
    staged = paths.inbox / staged_name(name)
    await asyncio.to_thread(staged.write_bytes, content)
    actor = Actor.system()

    async with session_scope() as session:
        report = await IngestPipeline.from_session(session).ingest_file(
            staged,
            actor=actor,
            filename=name,
            origin=FileOrigin(source=IngestSource.DROPBOX, external_id=entry.id),
        )
        if report.outcome is not IngestOutcome.INGESTED:
            # A duplicate is not a delivery — it is one delivery seen twice,
            # and counting it would make a cursor rewind look like a busy week.
            logger.info(
                "dropbox_file_not_ingested",
                name=name,
                outcome=report.outcome.value,
                detail=report.detail,
            )
            return
        await AuditRepository(session).record(
            actor=actor,
            action=DELIVERED_ACTION,
            entity_type=FEED_ENTITY,
            entity_id=feed_id,
            payload={
                "external_id": entry.id,
                "remote_path": entry.path_lower,
                "filename": name,
                "rev": entry.rev,
                "session_id": (
                    str(report.session_ids[0]) if report.session_ids else None
                ),
            },
        )
        await commit(session)


async def _record_failure(
    session: AsyncSession, feed: FeedRow, *, detail: str, advance_to: str | None
) -> None:
    """Count a failed attempt on this cursor, and give up once it is hopeless.

    ``cursor_attempts`` counts **consecutive** failures against the position
    the feed is currently stuck at; any resolved batch puts it back to zero, so
    one bad afternoon a month never adds up to a skipped file.

    After ``DROPBOX__MAX_BATCH_ATTEMPTS`` of them the cursor advances past the
    batch anyway and the entry that defeated it is written into ``last_error``.
    That is a real trade and it is made deliberately: holding the position
    forever is the safer-sounding option and it means re-downloading the same
    poisoned batch every two minutes until a human notices, while every file
    behind it — every ride recorded since — waits behind one that is never
    going to work. Moving on loses at most that batch, says so on the settings
    panel and in the coach's own read, and lets the rides through.
    """
    attempts = feed.cursor_attempts + 1
    if advance_to is not None and attempts >= get_settings().dropbox.max_batch_attempts:
        feed.cursor = advance_to
        feed.cursor_attempts = 0
        detail = f"gave up after {attempts} attempts and moved on — {detail}"
        logger.warning("dropbox_batch_abandoned", feed_id=str(feed.id), detail=detail)
    else:
        feed.cursor_attempts = attempts
        logger.warning(
            "dropbox_batch_failed",
            feed_id=str(feed.id),
            attempts=attempts,
            detail=detail,
        )
    feed.last_error = detail[:MAX_ERROR_LENGTH]
    await commit(session)


# --- the job ------------------------------------------------------------------


async def run_feed_poll_job() -> None:
    """The scheduled poll. Never raises — a failed poll must not kill the job.

    APScheduler removes a job whose coroutine raises, so an exception escaping
    here would end delivery for the lifetime of the process and say nothing
    about it beyond one traceback. The catch-all is the guard that makes
    AC-17's containment true: whatever Dropbox does, the next interval still
    comes round, and the local `data/inbox/` sweep never even hears about it.
    """
    try:
        await poll_feeds()
    except Exception:  # noqa: BLE001 — a scheduler job that raises stops running
        logger.exception("dropbox_feed_poll_job_failed")


def register_feed_poll_job(scheduler: BaseScheduler) -> None:
    """Register the poll on the application scheduler.

    The job lives here rather than in `app.core.scheduler`, which owns no jobs
    of its own: the module that owns the work registers it. The first run is
    one interval away — a boot is busy enough — and ``max_instances=1`` plus
    ``coalesce`` mean a slow poll delays the next one instead of running two
    cursors over the same folder.
    """
    interval = get_settings().dropbox.poll_interval_seconds
    scheduler.add_job(
        run_feed_poll_job,
        "interval",
        seconds=interval,
        id=FEED_POLL_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("dropbox_feed_job_registered", seconds=interval)

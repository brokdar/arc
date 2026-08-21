"""The scheduled poll that makes a watched Dropbox folder deliver.

Every ``DROPBOX__POLL_INTERVAL_SECONDS`` each enabled feed asks Dropbox what
has changed since its stored cursor, downloads the activity files in the
answer, and hands each one to `app.ingest.pipeline` — the same pipeline an
upload and the local `data/inbox/` sweep go through, so a ride that arrives
this way is indistinguishable downstream from one dropped in by hand, except
that its recording says which transport carried it.

**Where a feed's files go is decided before Dropbox is asked anything.** An
integration declares which of arc's two destinations it feeds
(`app.domain.integrations.DataKind`), and a watched folder can only reach one
of them: :data:`DELIVERABLE_KINDS`. A feed whose integration provides a kind
this transport cannot deliver is refused with an error on the row rather than
poured into the pipeline — see :func:`_undeliverable`, which is the whole
reason `DataKind` exists.

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
    ACCOUNT_NO_ACCESS,
    DropboxAccessError,
    DropboxAuthError,
    DropboxChanges,
    DropboxClient,
    DropboxCursorResetError,
    DropboxError,
    DropboxFile,
    DropboxPathNotFoundError,
    DropboxRateLimitedError,
    DropboxScopeError,
    DropboxUnreachableError,
)
from app.core.config import get_settings
from app.core.exceptions import ConflictError
from app.core.logging import get_logger
from app.domain.activity import IngestOutcome, IngestSource
from app.domain.actor import Actor
from app.domain.connections import (
    ACTIVITY_EXTENSIONS,
    FEED_DELIVERED_ACTION,
    ConnectionStatus,
)
from app.domain.integrations import CATALOGUE, DataKind, ordered_data_kinds
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
from app.persistence.integrations import IntegrationRow
from app.services.connections import ConnectionService, scope_refusal

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

#: Filenames that are never a finished activity, matched lowercased.
#:
#: Zwift writes `inProgressActivity.fit` continuously *during* a ride and
#: leaves it behind un-renamed when it crashes, so it is a real `.fit` file
#: containing a real but truncated ride. Letting it settle and ingest produces
#: a session that looks legitimate and is short — the worst kind of wrong,
#: because nothing downstream can tell it from an easy day.
SKIPPED_NAMES = frozenset({"inprogressactivity.fit"})

#: The `DataKind`s this transport — a watched cloud folder — can deliver into.
#:
#: `recordings` only, and `wellness`'s absence is the point of the whole
#: dispatch. `WellnessService` exists and `wellness_days` exists, but **no**
#: ingest path reaches them from a file, so a folder full of an Apple Health
#: export handed to `IngestPipeline` would not land in wellness — it would be
#: parsed as ride files and quarantined one by one, or worse, produce sessions
#: from something that was never a session. Naming what this module can deliver
#: turns that into one refusal the athlete can read, on the day such an
#: integration is added rather than on the day somebody reads the quarantine
#: queue. Add a member here only together with the code that consumes it.
DELIVERABLE_KINDS = frozenset({DataKind.RECORDINGS})


@dataclass(frozen=True, slots=True)
class _Refusal:
    """Why an entry was not taken, and whether it is worth a log row."""

    detail: str
    #: Whether this earns an `ingest_events` row. Reserved for refusals the
    #: athlete would otherwise never learn about — a ride-sized file arc
    #: declined. A `.txt` is not news.
    recorded: bool


@dataclass(frozen=True, slots=True)
class _BatchFailure:
    """Why a page stopped, and whether it counts against the give-up budget.

    ``cursor_attempts`` buys **liveness**: after enough consecutive failures the
    cursor moves past a page so one stuck batch cannot dam every ride behind it
    for ever (:func:`_record_failure`). That is worth paying for a page that
    keeps refusing — a file Dropbox answers 503 for on every attempt is one
    nothing is going to fix, and the rides recorded since must not queue behind
    it.

    It is worth nothing at all for a condition that suspends *all* progress and
    lifts on its own. There is no dam to break: when the condition clears the
    same page proceeds untouched. Spending the budget there trades rides away
    and buys nothing back, because the failure never said anything about the
    page — arc has been told to wait, and waiting is the whole remedy. A
    throttled afternoon would otherwise advance the cursor past files that were
    never downloaded once, which is the silent loss the batch rule exists to
    prevent (`_poll_feed`).

    This is the same judgement `_list_changes` already makes for a cursor
    reset, which never reaches this module's accounting at all.
    """

    detail: str
    #: Whether this spends an attempt. False for a suspension arc waits out —
    #: a 429, or a local fault that stopped arc storing what it downloaded.
    blames_batch: bool


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
    # The one list, in the domain, because folder discovery ranks a folder by
    # how many of these are in it from a layer that may not import this one —
    # see :data:`app.domain.connections.ACTIVITY_EXTENSIONS` for why arc will
    # not spend a download on anything else.
    if extension_of(Path(entry.name)) not in ACTIVITY_EXTENSIONS:
        return _Refusal(f"{entry.name} is not an activity file", recorded=False)
    if entry.size > MAX_UPLOAD_BYTES:
        return _Refusal(
            f"this Dropbox file is {entry.size} bytes, above arc's "
            f"{MAX_UPLOAD_BYTES} byte limit, so it was not downloaded",
            recorded=True,
        )
    return None


async def _undeliverable(session: AsyncSession, feed: FeedRow) -> str | None:
    """Why this feed's files have nowhere to go, or ``None`` to poll it.

    Asked of the **catalogue**, not of the row: `integrations` stores that the
    athlete asked arc to collect from a source and nothing more, so what that
    source provides is read from `CATALOGUE` every time and a widened spec
    takes effect everywhere at once (`app.persistence.integrations`).

    An **unclassified** feed (`integration_id IS NULL`) is deliverable. It has
    to be: those are the folders configured before integrations existed, they
    have been feeding `IngestPipeline` since WP-4.3, and a vocabulary arriving
    underneath them is not a reason to stop collecting a ride. The classified
    ones are the ones arc knows something about, and knowing is what earns the
    right to refuse.

    A refusal is returned as prose rather than raised, because the caller has
    somewhere better to put it than a traceback: ``feed.last_error``, which is
    what the settings panel and the coach's `get_ingest_status` both read.
    """
    if feed.integration_id is None:
        return None
    row = await session.get(IntegrationRow, feed.integration_id)
    if row is None:  # pragma: no cover — the FK cascade removes the feed with it
        return None
    # Total by construction: `IntegrationKind`'s members are exactly the
    # catalogue's keys, pinned by `test_integrations_domain.py`.
    spec = CATALOGUE[row.kind]
    provides = ordered_data_kinds(spec.provides)
    missing = [kind for kind in provides if kind not in DELIVERABLE_KINDS]
    if not missing:
        return None
    return (
        "arc cannot yet collect "
        f"{', '.join(kind.value for kind in missing)} from a watched folder. "
        f"{spec.display_name} provides "
        f"{', '.join(kind.value for kind in provides)}, and of those only "
        f"{DataKind.RECORDINGS.value} has an ingest destination, so nothing in "
        f"{feed.remote_path or '/'} was downloaded or delivered."
    )


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
    # Read into plain values before anything writes. This loop is the only
    # place in the sweep that commits while still enumerating, and a commit
    # that loses a race rolls the session back — which expires *every*
    # instance the session holds, ORM rows this loop has not reached yet
    # included. Walking those rows afterwards raises `MissingGreenlet` on the
    # first attribute read, so the conflict would end the cycle one connection
    # later having been caught. Tuples cannot be expired.
    connections = [
        (
            row.id,
            row.status,
            row.credentials,
            [feed.id for feed in row.feeds if feed.enabled],
        )
        for row in await ConnectionRepository(session).list()
    ]
    due: list[uuid.UUID] = []
    for connection_id, status, credentials, feed_ids in connections:
        if status is not ConnectionStatus.CONNECTED:
            logger.info(
                "dropbox_connection_not_usable",
                connection_id=str(connection_id),
                status=status.value,
            )
            continue
        try:
            EncryptedCredentials.unseal(credentials)
        except (CredentialDecryptionError, CredentialKeyError) as exc:
            await _mark_unreadable(session, connection_id, exc)
            continue
        due.extend(feed_ids)
    return due


async def _mark_unreadable(
    session: AsyncSession, connection_id: uuid.UUID, exc: Exception
) -> None:
    """Persist `error` on one connection, tolerating it having gone away.

    Re-read rather than mutated through the row :func:`_due_feeds` already
    holds, because by the time this is reached that row may be expired — see
    the comment there — and because the athlete pressing Disconnect mid-sweep
    is exactly the case this function exists to survive. A row that is gone has
    nothing left to mark and no feeds left to poll, so it is a log line, not a
    failure.

    The commit is guarded for the same race one moment later: `ConflictError`
    is what `app.persistence.db` turns a stale UPDATE into, and letting it out
    of here would end the whole cycle — every connection, every feed — over one
    row that moved, which is the opposite of what `poll_feeds` promises.
    """
    connection = await ConnectionRepository(session).get(connection_id)
    if connection is None:
        logger.info("dropbox_connection_vanished", connection_id=str(connection_id))
        return
    connection.status = ConnectionStatus.ERROR
    connection.last_error = str(exc)[:MAX_ERROR_LENGTH]
    try:
        await commit(session)
    except ConflictError:
        logger.info("dropbox_connection_vanished", connection_id=str(connection_id))
        return
    logger.warning("dropbox_credential_unreadable", connection_id=str(connection_id))


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

    **The connection's status is re-read here, not trusted from the sweep.**
    `poll_feeds` enumerated the due feeds once; this function is what can flip
    a connection mid-enumeration, so the remaining folders on a connection this
    cycle has already seen refused are skipped without a request.

    **The destination is settled first** (:func:`_undeliverable`). A feed whose
    integration provides something a folder cannot deliver never reaches the
    client at all, so "never passed to `IngestPipeline`" is true by the shape
    of the function rather than by a check further down that a later edit could
    step around.
    """
    async with session_scope() as session:
        repository = ConnectionRepository(session)
        feed = await repository.get_feed(feed_id)
        if feed is None:  # deleted between the sweep's read and now
            return
        if not feed.enabled:  # paused between the sweep's read and now
            return
        undeliverable = await _undeliverable(session, feed)
        if undeliverable is not None:
            # Before the client, before the listing: a feed arc has no
            # destination for must not spend a Dropbox request either, and
            # refusing here means no entry is ever downloaded, so there is
            # nothing for the cursor to advance past. It does not blame the
            # batch — the page is fine, arc is not — so the budget is untouched
            # and the next poll asks the same question again, which is what
            # makes the feed start delivering the moment the destination exists
            # rather than only after somebody re-adds it.
            await _record_failure(
                session,
                feed,
                failure=_BatchFailure(undeliverable, blames_batch=False),
                advance_to=None,
            )
            return
        connection = await repository.get(feed.connection_id)
        if connection is None:  # pragma: no cover — cascade removes the feed
            return
        if connection.status is not ConnectionStatus.CONNECTED:
            # `_due_feeds` asked the same question, once, before the sweep
            # began — and the answer can change *during* it, because this
            # function is what changes it. A scope refusal on the first folder
            # of a connection flips the row, and every remaining folder on that
            # connection is now being polled with a credential arc has already
            # watched Dropbox refuse. Re-reading here is what makes the flip
            # take effect on the same cycle rather than the next one: no
            # listing request, and no token request either — which matters
            # more than it looks, because `mark_needs_reauth` clears the token
            # expiry, so the next call on this connection would refresh first
            # and spend a request on a credential nobody is going to use.
            logger.info(
                "dropbox_connection_not_usable",
                connection_id=str(connection.id),
                feed_id=str(feed.id),
                status=connection.status.value,
            )
            return
        # The app key comes from `ConnectionService`, not from settings: the
        # athlete may have stored it in Settings rather than in `.env`, and a
        # poll that refreshed with a blank client id would report a dead
        # credential for a connection that is perfectly healthy.
        client = DropboxClient(
            session,
            connection,
            app_key=await ConnectionService.from_session(session).app_key(
                connection.provider
            )
            or "",
        )
        try:
            changes = await _list_changes(client, feed)
        except DropboxError as exc:
            # Dropbox's own words go to the log, where whoever is debugging
            # wants them; `_listing_failure` decides what reaches the screen.
            logger.warning(
                "dropbox_listing_failed", feed_id=str(feed.id), error=str(exc)
            )
            if isinstance(exc, DropboxScopeError):
                # **The flip happens here, on the first refusal.** A scope
                # withdrawn in the Dropbox console breaks the credential
                # silently: the athlete changed nothing in arc, and nothing in
                # arc would ever say so, because a browse-time refusal is
                # deliberately left as one screen's error (the athlete is
                # standing in front of it) and the poll is the only thing that
                # asks unprompted. So the poll is where the row learns. One
                # cycle, not two: a second failure would buy no new evidence —
                # refreshing cannot mint a scope, which is why
                # `DropboxClient._call` raises this without its retry.
                await client.mark_needs_reauth(scope_refusal(exc.required_scope))
            # No batch, so nothing to advance past: the listing itself failed.
            # It does not spend an attempt either — arc never got as far as a
            # page, so there is nothing here to give up on, and a counter that
            # climbed through an outage would leave the *next* genuine failure
            # already at the threshold, skipping a page on its first refusal.
            await _record_failure(
                session,
                feed,
                failure=_BatchFailure(_listing_failure(exc, feed), blames_batch=False),
                advance_to=None,
            )
            return

        # Nothing between a listing and a resolved page may unwind past this
        # accounting. `_deliver` writes to the disk and the database, and a
        # failure in either is exactly the "feed that has silently stopped
        # collecting" this module exists to make visible — but it is not the
        # page's fault, so it holds the cursor rather than spending an attempt.
        try:
            failure = await _take_batch(session, client, feed, changes)
        except Exception as exc:  # noqa: BLE001 — a silent feed is the failure
            logger.exception("dropbox_batch_errored", feed_id=str(feed.id))
            # The local fault *is* quoted, unlike the Dropbox ones: "No space
            # left on device" names something on the athlete's own machine that
            # they can go and fix, which is the test every sentence on this row
            # has to pass.
            failure = _BatchFailure(
                f"arc could not save what it downloaded from "
                f"{feed.remote_path or '/'}: {exc}. Nothing was lost — it "
                "tries again at the next check.",
                blames_batch=False,
            )
        if failure is not None:
            await _record_failure(
                session, feed, failure=failure, advance_to=changes.cursor
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


def _listing_failure(exc: DropboxError, feed: FeedRow) -> str:
    """Why arc could not read a watched folder, in words the athlete can act on.

    Named for the listing because that is where most of these arise, but the
    scope branch also serves :func:`_take_batch`: a download refused for want
    of `files.content.read` is the same permission fault the same folder would
    hit on a listing, and giving it a second sentence of its own would tell one
    athlete two things about one problem.

    ``feed.last_error`` is rendered **verbatim** in Settings and in the coach's
    `get_ingest_status`, so the connector's exception text may not be
    interpolated into it. Those strings are diagnostics — endpoint paths,
    status codes, "Dropbox rejected a freshly refreshed access token" — and
    every noun in them names something the athlete cannot see, cannot check and
    cannot fix. Interpolating them is how "Dropbox said your app is missing
    files.metadata.read" reached the screen as a question about the network.

    So the split is: Dropbox's own words to the log (:func:`_poll_feed` writes
    them), the athlete's situation and what happens next to the row. Each
    branch says which of the two things is true — arc will retry by itself, or
    somebody has to do something — because a sentence that says neither leaves
    an athlete watching a folder that will never recover.

    The clause order is specificity, like `_dropbox_failures_translated`'s:
    `DropboxScopeError` is a `DropboxAuthError` and `DropboxUnreachableError` a
    `DropboxUpstreamError`, so reordering would answer the precise case with
    the general sentence.
    """
    where = feed.remote_path or "/"
    if isinstance(exc, DropboxScopeError):
        return (
            f"arc no longer has permission to read {where}. Reconnect the "
            "Dropbox account below to start collecting from it again."
        )
    if isinstance(exc, DropboxAuthError):
        return (
            f"Dropbox would not let arc read {where}. Reconnect the Dropbox "
            "account below to start collecting from it again."
        )
    if isinstance(exc, DropboxAccessError):
        # The one refusal on this row that names **no** remedy inside arc, and
        # says so: Dropbox is describing the account, and the condition "may
        # succeed on retry, but only after corresponding action on the
        # account". So the sentence sends the athlete to dropbox.com, the
        # connection is deliberately **not** flipped (see `_poll_feed` — only a
        # scope refusal flips one), and every cycle asks again. A flip here
        # would freeze the feed behind a reconnect that cannot clear a team
        # policy, and `_due_feeds` would stop polling the folder that is going
        # to start working by itself the moment the account changes.
        #
        # The path is not interpolated, unlike every branch around it: the
        # condition is about the account, and naming one folder in it invites
        # the athlete to go looking at that folder's sharing settings.
        return ACCOUNT_NO_ACCESS
    if isinstance(exc, DropboxPathNotFoundError):
        return (
            f"There is no folder at {where} in your Dropbox any more. It may "
            "have been renamed or moved — stop watching it here, and add "
            "wherever the rides are being written now."
        )
    if isinstance(exc, DropboxRateLimitedError):
        return (
            f"Dropbox asked arc to wait about {int(exc.retry_after)} seconds "
            f"before reading {where}. arc tries again at the next check."
        )
    if isinstance(exc, DropboxUnreachableError):
        return (
            f"arc could not reach Dropbox to read {where}. It tries again at "
            "the next check."
        )
    return (
        f"Dropbox answered with an error of its own when arc read {where}. "
        "Nothing is wrong with your setup — arc tries again at the next check."
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
        # Passed as an argument, not written to `feed.cursor`: the row's
        # cursor is the caller's to advance, only once a page has actually
        # resolved (`_poll_feed`). Writing it here would be visible to
        # nothing on success — `_poll_feed` overwrites it with the resolved
        # page's cursor regardless — and on a *failed* retry it would survive
        # into `_record_failure`'s commit and erase the feed's last known
        # position over a condition this function's own docstring says must
        # "touch nothing" until it resolves.
        return await client.changes(path=feed.remote_path, cursor=None)


async def _take_batch(
    session: AsyncSession,
    client: DropboxClient,
    feed: FeedRow,
    changes: DropboxChanges,
) -> _BatchFailure | None:
    """Download and ingest every activity file in one page.

    Returns ``None`` when the whole page resolved, or the failure that stopped
    it — naming the entry, and saying whether the page is what went wrong (see
    :class:`_BatchFailure`). Stopping at the first failure rather than carrying
    on is what makes the batch rule meaningful: the cursor is not going to
    advance either way, so pulling the rest would be bytes moved twice.

    One failure here does more than record itself: a download Dropbox refuses
    for want of a scope flips the connection to `needs_reauth`, because it is
    the only place a revoked `files.content.read` can ever be observed — the
    listing that precedes it needs `files.metadata.read` and succeeds.
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
            # earns a longer ban. It does not spend an attempt — a throttle
            # lifts on its own and says nothing about this page, and the
            # entries after this one have not been tried even once, so giving
            # up on the page over it would discard rides arc never fetched.
            return _BatchFailure(
                f"Dropbox asked arc to wait about {int(exc.retry_after)} seconds "
                f"before downloading {entry.name}. The rest of this folder waits "
                "for the next check.",
                blames_batch=False,
            )
        except DropboxScopeError as exc:
            # **Before the `DropboxAuthError` clause below, which it is a
            # subclass of** — the clause order is specificity, as
            # :func:`_listing_failure` documents. A revoked
            # `files.content.read` reaches arc *only* here: the listing still
            # succeeds (that is `files.metadata.read`), so it stamps
            # `last_verified_at` on the way past, and treating this download
            # refusal as transient left the panel saying "connected, last
            # checked just now" over a feed that would never download another
            # ride. Dropbox named the scope, which is proof, not a hedge — so
            # the row flips exactly as a refused listing flips it, through the
            # one function that owns the wording (`scope_refusal`).
            logger.warning("dropbox_download_refused", name=entry.name, error=str(exc))
            await client.mark_needs_reauth(scope_refusal(exc.required_scope))
            # The feed's own sentence is the one a refused *listing* writes,
            # deliberately: it is the same fault met by a different call, the
            # remedy is identical, and the account line directly above it in
            # Settings now carries the four console moves. Two spellings of one
            # permission problem is how an athlete ends up believing they have
            # two.
            return _BatchFailure(_listing_failure(exc, feed), blames_batch=False)
        except DropboxAuthError as exc:
            # Not the page's fault, and not (yet) proof the credential is
            # dead: `DropboxClient._content_failure` deliberately does not
            # retry-and-refresh inline, so a download-only 401 has not been
            # through the one retry that would tell arc whether the token was
            # merely stale or genuinely revoked — that happens on the next
            # listing call. Blaming the page here would spend the give-up
            # budget on a credential problem and, once past the threshold,
            # advance the cursor past a file that was never actually
            # downloaded — silent loss over a condition that, like a 429,
            # arc waits out rather than gives up on.
            # Athlete words, and carefully hedged ones: `last_error` renders in
            # Settings, and this failure is not yet evidence the connection is
            # dead — see above. "arc lost its permission" belongs to the paths
            # that have proved it (`app.connectors.dropbox.PERMISSION_LOST`).
            # The exception is deliberately not quoted into it, for the reason
            # :func:`_listing_failure` gives.
            logger.warning("dropbox_download_refused", name=entry.name, error=str(exc))
            return _BatchFailure(
                f"Dropbox would not let arc download {entry.name}. arc tries "
                "again at the next check.",
                blames_batch=False,
            )
        except DropboxAccessError as exc:
            # Not the page's fault and not the credential's: Dropbox is
            # describing the account, and the same download will succeed once
            # the athlete has changed something at dropbox.com. So the batch is
            # not blamed — blaming it would spend the give-up budget and then,
            # past the threshold, advance the cursor past a ride that was never
            # downloaded, which is the silent loss this whole accounting exists
            # to prevent — and the connection is not flipped, because a
            # reconnect cannot clear a team policy.
            #
            # The sentence is the *listing*'s, for the reason the scope clause
            # above gives: one condition, one account of it, whichever call
            # met it.
            logger.warning("dropbox_download_refused", name=entry.name, error=str(exc))
            return _BatchFailure(_listing_failure(exc, feed), blames_batch=False)
        except DropboxError as exc:
            # A file Dropbox refuses on every attempt is the case the give-up
            # budget is for: it is not going to start working, and the rides
            # behind it must not queue for ever (AC-16).
            logger.warning("dropbox_download_failed", name=entry.name, error=str(exc))
            return _BatchFailure(
                f"arc could not download {entry.name} from Dropbox. It tries "
                "again at the next check, and moves on if it keeps failing.",
                blames_batch=True,
            )

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
    session: AsyncSession,
    feed: FeedRow,
    *,
    failure: _BatchFailure,
    advance_to: str | None,
) -> None:
    """Say what went wrong, and give up on the batch once it is hopeless.

    **Every failure is written down**, whatever its kind: ``last_error`` is
    what turns a feed that has silently stopped collecting into a `failing` one
    on the settings panel and in the coach's `get_ingest_status`, and a poll
    that returns without recording anything is the one failure mode this whole
    feature exists to prevent. The next resolved batch clears it.

    Only a failure that **blames the batch** spends an attempt
    (:class:`_BatchFailure`). ``cursor_attempts`` counts *consecutive* such
    failures against the position the feed is stuck at; any resolved batch puts
    it back to zero, so one bad afternoon a month never adds up to a skipped
    file.

    After ``DROPBOX__MAX_BATCH_ATTEMPTS`` of them the cursor advances past the
    batch anyway and the entry that defeated it is written into ``last_error``.
    That is a real trade and it is made deliberately: holding the position
    forever is the safer-sounding option and it means re-downloading the same
    poisoned batch every two minutes until a human notices, while every file
    behind it — every ride recorded since — waits behind one that is never
    going to work. Moving on loses at most that batch, says so on the settings
    panel and in the coach's own read, and lets the rides through.

    A suspension arc waits out takes the early return: the error is recorded
    and nothing else moves. Holding the cursor costs a re-download of the page
    once the condition lifts, which is the right way round — the alternative is
    advancing past rides arc never stored.
    """
    if not failure.blames_batch:
        feed.last_error = failure.detail[:MAX_ERROR_LENGTH]
        await commit(session)
        logger.warning(
            "dropbox_batch_deferred", feed_id=str(feed.id), detail=failure.detail
        )
        return

    detail = failure.detail
    attempts = feed.cursor_attempts + 1
    if advance_to is not None and attempts >= get_settings().dropbox.max_batch_attempts:
        feed.cursor = advance_to
        feed.cursor_attempts = 0
        detail = (
            f"arc tried {attempts} times and moved on, so newer rides are not "
            f"held up behind this one — {detail}"
        )
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

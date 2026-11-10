"""Use-cases for completed sessions: read them, correct them, type one in.

Everything here is about a session **after** it exists. Creating one from a
device file is the pipeline's job (`app.ingest.pipeline`), which sits a layer
above this one; what is left is the three things the athlete does afterwards.

* **Read.** The list is a log and reads newest first, unlike the planned-session
  list, which is a calendar and reads forwards.
* **Correct.** Two overrides, and both are corrections of a guess this system
  made from a file: the discipline (which sets ``discipline_overridden``, so a
  later re-classification cannot quietly undo it) and the timezone (which
  **re-derives** ``local_date``, because the stored date is the local date of
  the start and a wrong zone puts a late-evening ride on the wrong day).
* **Type one in.** A gym session has no file. It gets the same session row with
  ``recording_kind=manual``, no recording, and its sets as `logged_sets` rows
  (B-6). WP-6 matches manual and device sessions with the same query.

Streams are not here and not in the API this exposes: `data/streams/` is read
by WP-5's endpoints, and a session's *metadata* is what WP-4 answers with.
"""

import datetime as dt
import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import athlete_timezone
from app.core.exceptions import NotFoundError, ValidationError, domain_rules
from app.core.logging import get_logger
from app.domain.activity import (
    ClassificationSource,
    HoldQuality,
    LoggedSetKind,
    PainBand,
    PainKind,
    RecordingKind,
    SessionDiscipline,
    SetConfidence,
    SetSide,
    SetSource,
    check_temperature,
    day_anchor,
    parse_timezone,
    session_date,
)
from app.domain.actor import Actor, ActorKind
from app.domain.strength import check_sidedness
from app.persistence.activity import (
    MAX_DONT_LEARN_REASON_LENGTH,
    MAX_NOTES_LENGTH,
    MAX_PLANNED_LINE_REF_LENGTH,
    LoggedSetRow,
    RecordingRepository,
    SessionRepository,
    SessionRow,
)
from app.persistence.audit import AuditRepository
from app.persistence.db import commit, refresh
from app.services.exercises import ExerciseService
from app.services.guardrails import check_write_cap
from app.services.matching import MatchingService
from app.services.metrics import SessionMetricsService
from app.services.proposals import resolve_proposals_for_session

logger = get_logger(__name__)

#: `entity_type` written on this use-case's audit rows.
ENTITY_TYPE = "session"

#: Fields `update` accepts. Two kinds, still deliberately short: corrections
#: of what the pipeline guessed (`discipline`, `timezone`) and the
#: athlete-reported **context of the measurement** (`rpe`, `temperature_c`,
#: #23, and `notes`, #51). Measurements themselves stay uneditable — this is
#: not an editor of history.
#:
#: `notes` is context rather than measurement because nothing in the pipeline
#: ever writes it: every character was typed by the athlete or by the coach on
#: their behalf, and a typo or a pasted tool-call fragment there (issue #51)
#: was otherwise a permanent artefact that reads like a prompt injection to the
#: next agent. Correcting it loses nothing, because the audit row of the edit
#: carries the text it replaced (see `update`).
UPDATABLE_FIELDS = ("discipline", "timezone", "rpe", "temperature_c", "notes")

#: The fields a session cannot be without: an explicit null for one of these
#: is refused rather than stored. The context fields are *clearable* — a
#: mistyped RPE is corrected by nulling it, and "nobody said" is their honest
#: empty state.
NON_CLEARABLE_FIELDS = ("discipline", "timezone")

#: Bounds on a manually entered session. RPE is the standard 0-10 scale; the
#: duration bound is the same "too short to be a session" line
#: `app.domain.streams.validate` draws for a recorded one, and the upper one
#: is a typo guard (a day).
MIN_RPE, MAX_RPE = 0.0, 10.0
MIN_MANUAL_DURATION_S = 60
MAX_MANUAL_DURATION_S = 24 * 60 * 60

#: Bounds on when a session can have happened. A timestamp outside them is a
#: typo or a fuzzer, and `start_time + duration` on year 9999 raises
#: `OverflowError` — a 500 where a 422 is the honest answer.
EARLIEST_SESSION = dt.datetime(2000, 1, 1, tzinfo=dt.UTC)
LATEST_SESSION = dt.datetime(2100, 1, 1, tzinfo=dt.UTC)

#: Bounds on one logged set.
MAX_SETS = 200
MAX_REPS = 1_000
#: Longest a logged hold may be. The domain's `MAX_HOLD_SECONDS` for the
#: prescription side; repeated here because the logged side takes its bounds
#: from this module, not from a prescription it may not have.
MAX_HOLD_S = 3_600
MAX_LOAD_KG = 1_000.0
MIN_RIR, MAX_RIR = 0, 10
#: The reps-in-reserve a capped ("4+") rating is stored as.
CAPPED_RIR = 4
#: A logged set's RPE (Full control): 6-10 in half steps. Below 6 the scale
#: stops meaning anything a lifter can report, and 8.3 is a typo, not a rating.
MIN_SET_RPE, MAX_SET_RPE, SET_RPE_STEP = 6.0, 10.0, 0.5
#: The RPE that is zero reps in reserve; RPE converts to reps left as this
#: minus the RPE (RPE 8.5 = 1.5 left).
RPE_AT_FAILURE = 10.0
#: Longest rest a set may report, seconds — a typo guard, like `MAX_HOLD_S`.
MAX_REST_S = 3_600
#: The UTC offsets that exist, in minutes: UTC-12:00 to UTC+14:00, rounded
#: out to a symmetric ±14 h so no real zone is ever refused.
MIN_UTC_OFFSET_MIN, MAX_UTC_OFFSET_MIN = -14 * 60, 14 * 60
#: The Finish screen's session rating, a whole number from 1 to 10.
MIN_SESSION_RATING, MAX_SESSION_RATING = 1, 10

#: Longest free-text exercise name accepted for a set that names no catalogue
#: movement. Matches `LoggedSetRow.exercise_name`.
MAX_EXERCISE_NAME = 160


@dataclass(frozen=True, slots=True)
class LoggedSetInput:
    """One set as the athlete entered it.

    Args:
        exercise_id: Catalogue slug, when the movement is one of ours. The
            name stored on the row is then the catalogue's name at the time of
            logging, so the row stays readable if the catalogue moves on.
        exercise_name: Free text, for a movement the catalogue does not have.
            Exactly one of the two is given.
        reps: Repetitions performed, or ``None`` for a timed hold. Exactly one
            of ``reps`` and ``duration_s`` is given.
        duration_s: Seconds held, for a timed set.
        per_side: Whether the row holds both sides of a one-side movement in
            one line: one set, its reps worked twice. Refused on a movement
            the catalogue marks bilateral, and beside a single ``side``.
        load_kg: External load, when there was one (bodyweight sets have
            none). For a per-side row, the load on **that side** — the same
            convention `app.domain.strength.Load` declares.
        rir: Reps in reserve, as **reported** after the set — never the
            planned target. ``None`` is "not rated".
        notes: Anything the athlete wrote about this set.
        client_id: The phone-minted uuid7 that makes a re-upload idempotent.
            ``None`` (the coach, legacy callers) has the server mint one.
        set_kind: Warm-up, working, or a working kind.
        side: Both sides at once, or the left or right one. A single side is
            refused on a movement the catalogue marks bilateral.
        planned_line_ref: The prescription line this set answered. A
            reference only: nothing about the line's targets is copied.
        replaced_exercise_id: The catalogue movement this set was swapped in
            for.
        source: How the set reached arc. ``None`` means the actor's default:
            ``recap`` for a typed-in session. An agent's sets are always
            ``coach``, whatever this says, and only an agent may write
            ``coach``.
        confidence: How far the numbers can be trusted.
        rir_capped: "4+" — ``rir`` is a floor. Requires ``rir == 4``.
        rpe: RPE as reported, 6-10 in half steps. With ``rir`` it must agree:
            ``rir == floor(10 - rpe)``.
        hold_quality: The rating of a timed hold; only on a hold.
        rest_s: Rest before this set, seconds.
        completed_at: When the set was logged, timezone-aware.
        utc_offset_min: The device's UTC offset when it was logged, minutes.
        pain_kind: Pain reported on this set, if any.
        pain_band: Its 0-10 band; needs ``pain_kind``.
    """

    reps: int | None = None
    duration_s: int | None = None
    per_side: bool = False
    exercise_id: str | None = None
    exercise_name: str | None = None
    load_kg: float | None = None
    rir: int | None = None
    notes: str | None = None
    client_id: uuid.UUID | None = None
    set_kind: LoggedSetKind = LoggedSetKind.WORKING
    side: SetSide = SetSide.BOTH
    planned_line_ref: str | None = None
    replaced_exercise_id: str | None = None
    source: SetSource | None = None
    confidence: SetConfidence = SetConfidence.HIGH
    rir_capped: bool = False
    rpe: float | None = None
    hold_quality: HoldQuality | None = None
    rest_s: int | None = None
    completed_at: dt.datetime | None = None
    utc_offset_min: int | None = None
    pain_kind: PainKind | None = None
    pain_band: PainBand | None = None


class SessionService:
    """Use-cases for completed sessions. Raises AppError subclasses."""

    def __init__(
        self,
        session: AsyncSession,
        repository: SessionRepository,
        recordings: RecordingRepository,
        audit: AuditRepository,
        exercises: ExerciseService,
        metrics: SessionMetricsService,
    ) -> None:
        self._session = session
        self._repository = repository
        self._recordings = recordings
        self._audit = audit
        self._exercises = exercises
        self._metrics = metrics

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(
            session,
            SessionRepository(session),
            RecordingRepository(session),
            AuditRepository(session),
            ExerciseService.from_session(session),
            SessionMetricsService.from_session(session),
        )

    # --- reads ---------------------------------------------------------------

    async def list(
        self,
        *,
        start: dt.date | None = None,
        end: dt.date | None = None,
        discipline: SessionDiscipline | None = None,
        query: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[SessionRow], int]:
        """A page of completed sessions, newest first, plus the total.

        ``query`` matches the session's current notes; see the repository.
        """
        return await self._repository.list(
            start=start,
            end=end,
            discipline=discipline,
            query=query,
            offset=offset,
            limit=limit,
        )

    async def get(self, session_id: uuid.UUID) -> SessionRow:
        """One completed session with its recordings and logged sets.

        Raises:
            NotFoundError: When no session has that id.
        """
        row = await self._repository.get(session_id)
        if row is None:
            raise NotFoundError(f"Session {session_id} not found")
        return row

    async def repair_counts(
        self, sessions: Sequence[SessionRow]
    ) -> dict[uuid.UUID, int]:
        """Repairs recorded per recording, for a page of sessions, in one query."""
        return await self._recordings.repair_counts(
            [recording.id for row in sessions for recording in row.recordings]
        )

    # --- writes --------------------------------------------------------------

    async def update(
        self,
        session_id: uuid.UUID,
        updates: Mapping[str, Any],
        *,
        actor: Actor,
        dry_run: bool = False,
    ) -> SessionRow:
        """Apply corrections and measurement context to one session.

        Patch semantics: a field not in ``updates`` is untouched; an explicit
        ``None`` clears a context field (`rpe`, `temperature_c`, `notes`) and is
        refused for a field a session cannot be without
        (:data:`NON_CLEARABLE_FIELDS`).

        ``dry_run`` runs **every** check a real call runs — the write cap
        excepted, because a dry run costs no budget — and returns the row
        unchanged: the MCP adapter's "check before you act" path, sharing
        this method so it can never validate less than the write does.

        Raises:
            NotFoundError: When no session has that id.
            ValidationError: When a field is unknown, a non-clearable one is
                cleared, a value is out of bounds, or the timezone cannot be
                resolved.
            RateLimitedError: When an agent's trailing-hour cap is spent.
        """
        unknown = set(updates) - set(UPDATABLE_FIELDS)
        if unknown:
            raise ValidationError(
                f"Unknown session fields: {', '.join(sorted(unknown))}"
            )
        for name in NON_CLEARABLE_FIELDS:
            if name in updates and updates[name] is None:
                raise ValidationError(f"{name} cannot be cleared")

        row = await self.get(session_id)
        if not updates:
            raise ValidationError("Supply at least one field to update")

        # Everything is checked before anything is touched, so a refusal —
        # and a dry run — leaves the row exactly as it was.
        with domain_rules():
            if "timezone" in updates:
                parse_timezone(str(updates["timezone"]))
            if updates.get("temperature_c") is not None:
                check_temperature(float(updates["temperature_c"]))
        rpe = updates.get("rpe")
        # Blank is "nobody wrote anything", the same empty state as null, so
        # both store null: a row holding "  " would read as a note that says
        # nothing. Stripped before the bound so padding cannot trip it.
        notes = updates.get("notes")
        notes = (str(notes).strip() or None) if notes is not None else None
        if notes is not None and len(notes) > MAX_NOTES_LENGTH:
            raise ValidationError(f"Notes are at most {MAX_NOTES_LENGTH} characters")
        if rpe is not None and not MIN_RPE <= float(rpe) <= MAX_RPE:
            raise ValidationError(f"RPE is on a {MIN_RPE:g}-{MAX_RPE:g} scale")
        if dry_run:
            return row
        await check_write_cap(self._session, actor)

        changed: dict[str, Any] = {}
        # Whether the artefact is stale afterwards, which is a narrower
        # question than "was a field submitted": only the discipline reaches
        # the metrics, and only when it actually moves.
        discipline_changed = False
        if "discipline" in updates:
            discipline_changed = (
                SessionDiscipline(updates["discipline"]) is not row.discipline
            )
            row.discipline = SessionDiscipline(updates["discipline"])
            # The athlete's answer is not a classification, so the source stops
            # claiming one guessed it; the flag is what stops a later
            # re-classification from overwriting the correction.
            row.discipline_overridden = True
            row.classification_source = ClassificationSource.MANUAL
            changed["discipline"] = row.discipline.value
        if "timezone" in updates:
            timezone = str(updates["timezone"])
            row.timezone = timezone
            row.local_date = session_date(row.start_time, timezone)
            changed["timezone"] = timezone
            changed["local_date"] = row.local_date.isoformat()
        if "rpe" in updates:
            row.rpe = None if rpe is None else float(rpe)
            changed["rpe"] = row.rpe
        if "temperature_c" in updates:
            value = updates["temperature_c"]
            row.temperature_c = None if value is None else float(value)
            changed["temperature_c"] = row.temperature_c
        previous_notes = row.notes
        if "notes" in updates:
            row.notes = notes
            changed["notes"] = notes

        row = await self._repository.add(row)
        await self._audit.record(
            actor=actor,
            action="session.updated",
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            payload=changed
            | {"changed": sorted(changed)}
            # The correction is a new version of the notes, not a deletion of
            # the old one: the text it replaced stays readable in the audit
            # trail, which is what lets `notes` be editable at all.
            | ({"previous_notes": previous_notes} if "notes" in updates else {}),
        )
        await commit(self._session)
        await refresh(self._session, row)
        # A discipline correction changes which load model is preferred
        # (A5.2), so the artefact is stale the moment it is applied. A
        # timezone correction does not touch a single metric input, and a
        # version chain that grew a link every time the athlete fixed a
        # timezone would make "which numbers was this verdict confirmed
        # against" unanswerable by drowning it — so the recompute is gated on
        # the discipline having actually moved, not on a field having been
        # submitted.
        #
        # Only the stream-free path can be re-run from here: reading a parquet
        # file is `app.ingest`'s job and a service may not reach it, so a
        # device session's correction is recovered through the recompute
        # endpoint, which can.
        if discipline_changed and not row.recordings:
            row = await self._recompute_strength(
                row, actor=actor, reason="session corrected"
            )
        return row

    async def _recompute_strength(
        self, row: SessionRow, *, actor: Actor, reason: str | None
    ) -> SessionRow:
        """Append a metric version for a session that has no stream.

        **Isolated from the write that preceded it**, and isolated in fact
        rather than only in intent: the session is already committed, so a
        failure here must leave the athlete with a stored session and no
        numbers rather than a 500 over work that succeeded. Without the guard,
        a unique-constraint race or a dropped connection turns a successful
        `POST /manual-sessions` into an error the client retries — and the
        retry creates a *second* session, because the first one is already in
        the database.

        The same shape, and the same reasoning, as
        `app.ingest.pipeline.IngestPipeline._compute_metrics`. The rollback
        matters as much as the `except`: the failed write has poisoned the
        session, and the request still has to answer from it.

        Returns the session row the caller should answer with. That is not
        cosmetic — `rollback()` **expires every instance** in the session
        (unlike `commit()`, which this codebase configures not to), so the
        handle the caller came in with would raise `MissingGreenlet` the
        moment the route touched it — reading even ``row.id`` off an expired
        instance is a lazy database read. Hence the id is taken **before** the
        attempt, and a freshly loaded row is the only usable answer after a
        rollback.
        """
        session_id = row.id
        try:
            reloaded = await self._repository.get(session_id)
            if reloaded is None:  # pragma: no cover — committed a line ago
                return row
            await self._metrics.record_strength(reloaded, actor=actor, reason=reason)
            return reloaded
        except Exception:  # noqa: BLE001 — see the docstring
            logger.exception("session_metrics_failed", session_id=str(session_id))
            await self._session.rollback()
            return await self._repository.get(session_id) or row

    async def create_manual(
        self,
        *,
        actor: Actor,
        start_time: dt.datetime | None = None,
        local_date: dt.date | None = None,
        timezone: str | None = None,
        duration_s: int,
        discipline: SessionDiscipline = SessionDiscipline.STRENGTH,
        rpe: float | None = None,
        temperature_c: float | None = None,
        notes: str | None = None,
        sets: Sequence[LoggedSetInput] = (),
        dont_learn: bool = False,
        dont_learn_reason: str | None = None,
        session_rating: int | None = None,
        dry_run: bool = False,
    ) -> SessionRow:
        """Record a session the athlete performed and typed in (B-6).

        **A repeated upload is the same session.** When every set carries a
        ``client_id`` and a manual session with the same start and discipline
        already holds all of them, that session is returned as it stands —
        nothing is written, audited or charged to the write cap. Two sets with
        one ``client_id`` in a single call are one set, and the later one
        wins: it is the more recent edit of the same set. A set without a
        ``client_id`` gets a fresh uuid7, so a caller that sends none (the
        coach, a legacy client) keeps today's behaviour and gets no replay
        protection.

        ``dry_run`` runs every check a real call runs — the timezone, the
        bounds, every catalogue slug — and returns the session it *would*
        have stored as a **transient** row, never added to the database
        session; its generated fields (id, status, timestamps) are unset,
        which is the honest rendering of a row that does not exist. Free of
        the write cap, because checking before acting must be the cheap
        option.

        ``timezone`` omitted means the athlete's own configured zone
        (`app.core.clock.athlete_timezone`), resolved **here** so both adapters
        get the same answer. It used to default to ``"UTC"`` in each of them,
        which is the right value to *store* when a device file did not say
        where it was recorded — a manual entry is not that case. The
        deployment knows where the athlete lives, and reading `local_date` off
        Greenwich for a session they typed in themselves put it on the wrong
        day, which then failed to match the planned session on the right one
        (issue #62).

        Exactly one of ``start_time`` and ``local_date`` is given. A report
        like "Kraft 1, Montagabend, 35 min" has a day and no clock reading, and
        requiring one made the coach invent 19:00 and disclose the invention
        in the note (issue #51). With ``local_date`` the session is stored at
        that day's local midnight (:func:`app.domain.activity.day_anchor`) with
        ``start_time_known`` false, and every read surface says so rather than
        presenting the anchor as a time. Both given is refused instead of
        reconciled: if they disagree there is no right answer, and if they
        agree one of them was redundant.

        The check lives here, not in either adapter, so the write and the MCP
        dry run cannot disagree about it.

        Raises:
            ValidationError: When the timezone is unresolvable, neither or both
                of ``start_time`` and ``local_date`` are given, the duration,
                RPE or temperature is out of range, or a set is malformed.
            NotFoundError: When a set names a catalogue exercise that does not
                exist.
            RateLimitedError: When an agent's trailing-hour cap is spent.
        """
        timezone = timezone or athlete_timezone()
        if (start_time is None) == (local_date is None):
            raise ValidationError(
                "give exactly one of start_time (when it started, with an "
                "offset) or local_date (the day, when no time was reported)"
            )
        start_time_known = start_time is not None
        with domain_rules():
            parse_timezone(timezone)
            if start_time is None:
                assert local_date is not None  # noqa: S101 — narrowed above
                start_time = day_anchor(local_date, timezone)
            else:
                local_date = session_date(start_time, timezone)
        _check_manual(
            start_time=start_time,
            duration_s=duration_s,
            rpe=rpe,
            temperature_c=temperature_c,
            sets=sets,
        )
        check_session_context(
            dont_learn=dont_learn,
            dont_learn_reason=dont_learn_reason,
            session_rating=session_rating,
        )
        sets = _latest_per_client_id(sets)
        if not dry_run:
            replayed = await self._replay(start_time, discipline, sets)
            if replayed is not None:
                return replayed
            await check_write_cap(self._session, actor)
        rows = [
            await self.build_logged_set(entry, index, actor=actor)
            for index, entry in enumerate(sets)
        ]

        draft = SessionRow(
            start_time=start_time,
            end_time=start_time + dt.timedelta(seconds=duration_s),
            start_time_known=start_time_known,
            timezone=timezone,
            local_date=local_date,
            discipline=discipline,
            # Nobody classified this: the athlete said what it was.
            classification_source=ClassificationSource.MANUAL,
            recording_kind=RecordingKind.MANUAL,
            rpe=rpe,
            temperature_c=temperature_c,
            notes=notes,
            dont_learn=dont_learn,
            dont_learn_reason=dont_learn_reason if dont_learn else None,
            session_rating=session_rating,
        )
        if dry_run:
            draft.logged_sets = list(rows)
            return draft

        row = await self._repository.add(draft)
        for logged in rows:
            logged.session_id = row.id
        self._session.add_all(rows)
        await self._audit.record(
            actor=actor,
            action="session.manually_created",
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            payload={
                "local_date": local_date.isoformat(),
                "start_time_known": start_time_known,
                "discipline": discipline.value,
                "duration_s": duration_s,
                "rpe": rpe,
                "temperature_c": temperature_c,
                "dont_learn": dont_learn,
                "session_rating": session_rating,
                "sets": len(rows),
            },
        )
        await commit(self._session)
        await refresh(self._session, row)
        return await self.refresh_derived(row, actor=actor, reason=None, match=True)

    async def refresh_derived(
        self, row: SessionRow, *, actor: Actor, reason: str | None, match: bool
    ) -> SessionRow:
        """Recompute what a committed manual session's numbers derive from.

        A new metric version always; with ``match``, matching and then the
        proposals the session made moot. Each step is isolated from the
        committed write before it (see :meth:`_recompute_strength`). Public
        because the outbox (`app.services.sync`) commits a batch first and then
        refreshes every finished session it touched, exactly as
        `create_manual` does for one.

        Returns the row to carry on with — after a failed step the handle
        passed in is expired.
        """
        row = await self._recompute_strength(row, actor=actor, reason=reason)
        if not match:
            return row
        # Matching first, then the proposals: a link is one of the two things
        # that resolve a pending proposal, and WP-6 links a recording
        # up to a day either side of the plan entry — so resolving before the
        # matcher has run would miss exactly the cases the date test cannot
        # see.
        row = await self._match(row, actor=actor)
        return await self._resolve_proposals(row, actor=actor)

    async def _replay(
        self,
        start_time: dt.datetime,
        discipline: SessionDiscipline,
        sets: Sequence[LoggedSetInput],
    ) -> SessionRow | None:
        """The session this call already created, when it is a re-upload.

        Only a call whose every set names its ``client_id`` can be a replay: a
        set without one has no identity to recognise, so a call carrying one
        is always new.
        """
        client_ids = [entry.client_id for entry in sets]
        if not client_ids or any(value is None for value in client_ids):
            return None
        return await self._repository.manual_replay(
            start_time=start_time,
            discipline=discipline,
            client_ids=[value for value in client_ids if value is not None],
        )

    async def _resolve_proposals(self, row: SessionRow, *, actor: Actor) -> SessionRow:
        """Close pending plan-change proposals this session made moot (WP-8.2).

        The athlete trained. A proposal still asking what to do with that day
        in that discipline is not a question any more, so it resolves itself
        (`resolved_by_reality`) rather than sitting in the inbox waiting to
        rewrite a plan a recorded session is about to be scored against.

        Isolated in its own ``try`` exactly as :meth:`_match` is, and for the
        same reason: the session is already committed, so a failure here must
        leave the athlete with a stored session and a stale inbox row rather
        than a 500 over work that succeeded — and the hourly expiry sweep will
        clear the row anyway.

        Returns the session row the caller should carry on with, for the reason
        :meth:`_recompute_strength` does: the rollback in the ``except``
        **expires every instance** in the session, so the handle passed in is
        unusable afterwards — even reading ``row.id`` off it is a lazy database
        read, and the next step (`_match`) does exactly that. Hence the id is
        taken before the attempt and a freshly loaded row is the only usable
        answer after a rollback.
        """
        session_id = row.id
        try:
            await resolve_proposals_for_session(
                self._session,
                actor=actor,
                local_date=row.local_date,
                discipline=row.discipline,
            )
        except Exception:  # noqa: BLE001 — see the docstring
            logger.exception("proposal_resolution_failed", session_id=str(session_id))
            await self._session.rollback()
            return await self._repository.get(session_id) or row
        return row

    async def _match(self, row: SessionRow, *, actor: Actor) -> SessionRow:
        """Look for the planned session a typed-in session answers to (WP-6).

        A gym session the athlete typed in is a session like any other, and the
        planned strength session it answers to is the whole point of having
        typed it in — so matching runs here for the same reason it runs at the
        end of the ingest pipeline, and **after** the metrics for the same
        reason too: the score reads the artefact.

        Isolated from the write that preceded it exactly as
        :meth:`_recompute_strength` is, and for the same reason: the session is
        already committed, so a matching failure must leave the athlete with a
        stored session and no proposal rather than a 500 over work that
        succeeded — and a retry of a failed `POST /manual-sessions` would
        create a *second* session.
        """
        session_id = row.id
        try:
            outcome = await MatchingService.from_session(self._session).match_session(
                session_id, actor=actor
            )
        except Exception:  # noqa: BLE001 — see the docstring
            logger.exception("session_matching_failed", session_id=str(session_id))
            await self._session.rollback()
            return await self._repository.get(session_id) or row
        return outcome.session

    async def build_logged_set(
        self, entry: LoggedSetInput, index: int, *, actor: Actor
    ) -> LoggedSetRow:
        """Build one set row, resolving a catalogue reference to its name.

        Public because two use-cases build sets: this service's
        `create_manual` and the outbox (`app.services.sync`), which must store
        a set exactly as a typed-in one would be and commits on its own terms.
        The row is returned unattached; adding it is the caller's.

        The sidedness rule is the domain's
        (`app.domain.strength.check_sidedness`); the catalogue lookup it needs
        is done here, because the domain cannot reach the catalogue and the
        service is the only layer that can see both the set and
        `Exercise.unilateral`.

        **Effort is stored per set, as reported.** The gym screen asks once per
        exercise, on its last working set, and that rating applies to all its
        working sets — but it is the phone that copies it onto each of them, so
        what arrives here, and what is stored, is a ``rir`` / ``rpe`` on every
        row. There is no per-exercise rating row: one rating per exercise was
        the alternative, and it would have needed its own key, its own sync
        mutation and a join in every reader, for a value that per-set storage
        already carries (and Full control rates per set anyway). Nothing here
        fills ``rir`` or ``rpe`` from ``planned_line_ref`` or any target: an
        unrated set stays null (S10.1).

        ``source`` is ``coach`` for every set an agent writes, whatever the
        payload claims, and ``coach`` is refused from anyone else — the column
        exists to tell the athlete's own account from the coach's.

        Raises:
            ValidationError: When the set names neither a catalogue movement
                nor a free-text one, or both, claims ``per_side`` or a single
                side on a movement the catalogue marks bilateral, claims both
                ``per_side`` and a single side
                (`app.domain.strength.check_sidedness`), replaces
                itself, or claims to be the coach's when no agent wrote it.
            NotFoundError: When the catalogue has no such movement, or no
                such replaced movement.
        """
        if (entry.exercise_id is None) == (entry.exercise_name is None):
            raise ValidationError(
                f"Set {index + 1} needs exactly one of exercise_id or "
                "exercise_name: a set names a catalogue movement or names its "
                "own, not both and not neither."
            )
        unilateral: bool | None = None
        if entry.exercise_id is not None:
            catalogue = await self._exercises.get(entry.exercise_id)
            name = catalogue.name
            label = f"{catalogue.name} ({entry.exercise_id})"
            unilateral = catalogue.unilateral
        else:
            name = label = (entry.exercise_name or "").strip()
            if not name:
                raise ValidationError(f"Set {index + 1} has an empty exercise name")
        try:
            check_sidedness(unilateral, per_side=entry.per_side, side=entry.side)
        except ValueError as error:
            raise ValidationError(f"Set {index + 1}: {label}: {error}.") from error
        if entry.replaced_exercise_id is not None:
            if entry.replaced_exercise_id == entry.exercise_id:
                raise ValidationError(
                    f"Set {index + 1}: replaced_exercise_id names the movement "
                    "the set itself is — a movement does not replace itself."
                )
            await self._exercises.get(entry.replaced_exercise_id)
        return LoggedSetRow(
            client_id=entry.client_id or uuid.uuid7(),
            exercise_id=entry.exercise_id,
            exercise_name=name[:MAX_EXERCISE_NAME],
            set_index=index,
            set_kind=entry.set_kind,
            side=entry.side,
            planned_line_ref=entry.planned_line_ref,
            replaced_exercise_id=entry.replaced_exercise_id,
            source=_source(entry, actor, index),
            confidence=entry.confidence,
            reps=entry.reps,
            duration_s=entry.duration_s,
            per_side=entry.per_side,
            load_kg=entry.load_kg,
            rir=entry.rir,
            rir_capped=entry.rir_capped,
            rpe=entry.rpe,
            hold_quality=entry.hold_quality,
            rest_s=entry.rest_s,
            completed_at=entry.completed_at,
            utc_offset_min=entry.utc_offset_min,
            pain_kind=entry.pain_kind,
            pain_band=entry.pain_band,
            notes=(entry.notes or None),
        )


def _source(entry: LoggedSetInput, actor: Actor, index: int) -> SetSource:
    """Who a set is credited to: the coach for an agent, else what it says.

    Raises:
        ValidationError: When a non-agent claims ``coach``.
    """
    if actor.kind is ActorKind.AGENT:
        return SetSource.COACH
    if entry.source is SetSource.COACH:
        raise ValidationError(
            f"Set {index + 1}: source 'coach' is written only by the coaching "
            "agent; a set you enter is 'app', 'recap' or 'import'."
        )
    return entry.source or SetSource.RECAP


def _latest_per_client_id(sets: Sequence[LoggedSetInput]) -> list[LoggedSetInput]:
    """One set per ``client_id``: the last copy wins, in its own position.

    Sets without a ``client_id`` are distinct by definition and all kept.
    """
    last = {
        entry.client_id: position
        for position, entry in enumerate(sets)
        if entry.client_id is not None
    }
    return [
        entry
        for position, entry in enumerate(sets)
        if entry.client_id is None or last[entry.client_id] == position
    ]


def check_session_context(
    *, dont_learn: bool, dont_learn_reason: str | None, session_rating: int | None
) -> None:
    """Bounds on the Finish screen's two answers.

    Raises:
        ValidationError: When a reason comes without the flag, the reason is
            too long, or the rating is off its 1-10 scale.
    """
    if dont_learn_reason and not dont_learn:
        raise ValidationError(
            "dont_learn_reason is the reason for dont_learn; give dont_learn: "
            "true with it, or leave the reason out"
        )
    if dont_learn_reason is not None and (
        len(dont_learn_reason) > MAX_DONT_LEARN_REASON_LENGTH
    ):
        raise ValidationError("The reason not to learn from this session is too long")
    if session_rating is not None and not (
        MIN_SESSION_RATING <= session_rating <= MAX_SESSION_RATING
    ):
        raise ValidationError(
            f"session_rating is a whole number from {MIN_SESSION_RATING} to "
            f"{MAX_SESSION_RATING}"
        )


def _check_manual(
    *,
    start_time: dt.datetime,
    duration_s: int,
    rpe: float | None,
    temperature_c: float | None,
    sets: Sequence[LoggedSetInput],
) -> None:
    """Bounds a JSON schema cannot express, refused as 422s rather than 500s.

    Raises:
        ValidationError: When any of them is violated.
    """
    if start_time.tzinfo is None:
        raise ValidationError("start_time must carry a timezone offset")
    if not EARLIEST_SESSION <= start_time <= LATEST_SESSION:
        raise ValidationError(
            f"start_time must be between {EARLIEST_SESSION:%Y} and {LATEST_SESSION:%Y}"
        )
    if not MIN_MANUAL_DURATION_S <= duration_s <= MAX_MANUAL_DURATION_S:
        raise ValidationError(
            f"A session lasts between {MIN_MANUAL_DURATION_S} s and "
            f"{MAX_MANUAL_DURATION_S} s; {duration_s} s is not a session"
        )
    if rpe is not None and not MIN_RPE <= rpe <= MAX_RPE:
        raise ValidationError(f"RPE is on a {MIN_RPE:g}-{MAX_RPE:g} scale")
    if temperature_c is not None:
        with domain_rules():
            check_temperature(temperature_c)
    if len(sets) > MAX_SETS:
        raise ValidationError(f"A session may log at most {MAX_SETS} sets")
    for index, entry in enumerate(sets, start=1):
        check_logged_set(entry, index)


def check_logged_set(entry: LoggedSetInput, index: int) -> None:
    """The bounds on one set a JSON schema cannot express.

    ``index`` is 1-based and only names the set in the message. Shared with
    the outbox (`app.services.sync`), which checks each synced set as it is
    merged.

    Raises:
        ValidationError: When any of them is violated.
    """
    if (entry.reps is None) == (entry.duration_s is None):
        raise ValidationError(
            f"Set {index} needs exactly one of reps or duration_s: a set "
            "is repetitions or it is a hold, not both and not neither."
        )
    if entry.reps is not None and not 1 <= entry.reps <= MAX_REPS:
        raise ValidationError(f"Set {index}: reps must be between 1 and {MAX_REPS}")
    if entry.duration_s is not None and not 1 <= entry.duration_s <= MAX_HOLD_S:
        raise ValidationError(
            f"Set {index}: duration_s must be between 1 and {MAX_HOLD_S} seconds"
        )
    if entry.load_kg is not None and not 0 <= entry.load_kg <= MAX_LOAD_KG:
        raise ValidationError(
            f"Set {index}: load must be between 0 and {MAX_LOAD_KG:g} kg"
        )
    if entry.rir is not None and not MIN_RIR <= entry.rir <= MAX_RIR:
        raise ValidationError(
            f"Set {index}: reps in reserve must be between {MIN_RIR} and {MAX_RIR}"
        )
    if entry.notes is not None and len(entry.notes) > MAX_NOTES_LENGTH:
        raise ValidationError(f"Set {index}: the note is too long")
    _check_effort(entry, index)
    _check_set_context(entry, index)


def _check_effort(entry: LoggedSetInput, index: int) -> None:
    """The reported effort is on its scale and says one thing.

    Every rule here guards S10.1 from the other side: what is stored is what
    the athlete reported, so it has to be something they *could* have
    reported. "4+" is ``rir = 4`` with the cap, and an RPE given beside reps
    in reserve must name the same effort (RPE 8.5 is 1.5 reps left, which a
    whole-rep count records as 1).

    Raises:
        ValidationError: Naming the set and the rule.
    """
    if entry.rir_capped and entry.rir != CAPPED_RIR:
        raise ValidationError(
            f"Set {index}: rir_capped means '{CAPPED_RIR}+ reps left' and needs "
            f"rir = {CAPPED_RIR}, got {entry.rir}"
        )
    if entry.rpe is None:
        return
    steps = entry.rpe / SET_RPE_STEP
    if not MIN_SET_RPE <= entry.rpe <= MAX_SET_RPE or steps != round(steps):
        raise ValidationError(
            f"Set {index}: rpe is {MIN_SET_RPE:g}-{MAX_SET_RPE:g} in steps of "
            f"{SET_RPE_STEP:g}, got {entry.rpe:g}"
        )
    if entry.rir is not None and entry.rir != math.floor(RPE_AT_FAILURE - entry.rpe):
        raise ValidationError(
            f"Set {index}: rir {entry.rir} and rpe {entry.rpe:g} disagree — rpe "
            f"{entry.rpe:g} is {RPE_AT_FAILURE - entry.rpe:g} reps left, so rir "
            f"would be {math.floor(RPE_AT_FAILURE - entry.rpe)}"
        )


def _check_set_context(entry: LoggedSetInput, index: int) -> None:
    """Bounds on the rest of what a logged set says about itself.

    Raises:
        ValidationError: Naming the set and the field.
    """
    if entry.hold_quality is not None and entry.duration_s is None:
        raise ValidationError(
            f"Set {index}: hold_quality rates a timed hold; a rep set is rated "
            "with rir or rpe"
        )
    if entry.pain_band is not None and entry.pain_kind is None:
        raise ValidationError(
            f"Set {index}: pain_band needs pain_kind — say which kind of pain first"
        )
    if entry.rest_s is not None and not 0 <= entry.rest_s <= MAX_REST_S:
        raise ValidationError(
            f"Set {index}: rest_s must be between 0 and {MAX_REST_S} seconds"
        )
    if entry.utc_offset_min is not None and not (
        MIN_UTC_OFFSET_MIN <= entry.utc_offset_min <= MAX_UTC_OFFSET_MIN
    ):
        raise ValidationError(
            f"Set {index}: utc_offset_min must be between {MIN_UTC_OFFSET_MIN} "
            f"and {MAX_UTC_OFFSET_MIN}"
        )
    if entry.completed_at is not None:
        if entry.completed_at.tzinfo is None:
            raise ValidationError(f"Set {index}: completed_at must carry an offset")
        if not EARLIEST_SESSION <= entry.completed_at <= LATEST_SESSION:
            raise ValidationError(
                f"Set {index}: completed_at must be between "
                f"{EARLIEST_SESSION:%Y} and {LATEST_SESSION:%Y}"
            )
    if entry.planned_line_ref is not None and not (
        0 < len(entry.planned_line_ref) <= MAX_PLANNED_LINE_REF_LENGTH
    ):
        raise ValidationError(
            f"Set {index}: planned_line_ref is 1-{MAX_PLANNED_LINE_REF_LENGTH} "
            "characters"
        )

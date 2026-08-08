"""Matching use-cases: propose links, let the athlete rule on them, sweep misses.

Build plan WP-6. `app.domain.matching` decides *how well* a recording answers a
prescription; this module decides what to do about it, and it is the only
writer of `session_matches` and `evening_prompts`.

**Automatic matching runs once, when a session is created.** The ingest
pipeline calls :meth:`MatchingService.match_session` after the metric artefact
exists, because the intensity and structure terms read that artefact. A
re-match (`POST /sessions/{id}/rematch`) is the athlete or the agent asking
again, explicitly, and therefore **overrules an earlier automatic verdict and
an earlier "this was unplanned"** — but never a `confirmed` or `displaced`
link, which are the athlete's own words and which no re-run touches (WP-6.6,
D142).

**Every state change is reversible, and reversible exactly.** A link records
the two statuses it displaced, so :meth:`unlink` restores them rather than
guessing at `unmatched`/`planned` (WP-6.8). The three statuses move together
and always here:

===================  ===================  =======================
link                 completed session    planned session
===================  ===================  =======================
``pending``          unchanged            unchanged
``auto_high``        ``matched``          ``completed``
``confirmed``        ``matched``          ``completed``
``displaced``        ``displaced``        ``displaced``
no link              ``unplanned``        ``planned`` / ``missed``
===================  ===================  =======================

A **pending** link changes nothing on either side on purpose: a proposal is a
question, and a calendar that marked a session complete because the machine
thought it probably was would be answering it on the athlete's behalf (D140).

**The missed sweep is thin because the rule is pure.**
`app.domain.matching.is_missed` owns "end of day+1 in the athlete's local
timezone"; :meth:`mark_missed` turns it into rows and prompts, and skips
entirely while the plan is paused — pausing a plan is a statement about
enforcement, and filling the week with `missed` is enforcement
(`app.domain.plan`).
"""

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Self

from apscheduler.schedulers.base import BaseScheduler
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.domain.activity import (
    SessionDiscipline,
    SessionMatchStatus,
    as_planned_discipline,
    parse_timezone,
    session_date,
)
from app.domain.actor import Actor
from app.domain.anchors import AnchorType
from app.domain.matching import (
    PROMPT_TTL_HOURS,
    STICKY_STATUSES,
    EveningPromptKind,
    EveningPromptStatus,
    IntensityBasis,
    MatchEvidence,
    MatchLinkStatus,
    Similarity,
    StructureBasis,
    better,
    candidate_window,
    classify,
    date_distance,
    is_missed,
    missed_on_or_before,
    planned_hr_intensity,
    planned_power_intensity,
    planned_work_steps,
    similarity,
    similarity_to_json,
)
from app.domain.plan import PlanState
from app.domain.prediction import PinnedAnchor, predict_strength_volume
from app.domain.sessions import SessionStatus
from app.domain.strength import StrengthWorkout
from app.domain.workout import (
    EnduranceWorkout,
    WorkoutBody,
    total_duration_s,
    workout_body_from_json,
)
from app.persistence.activity import (
    RecordingRow,
    SessionRepository,
    SessionRow,
    session_duration_s,
)
from app.persistence.athlete import AthleteRepository
from app.persistence.audit import AuditRepository
from app.persistence.db import commit, session_scope
from app.persistence.matching import (
    EveningPromptRepository,
    EveningPromptRow,
    SessionMatchRepository,
    SessionMatchRow,
)
from app.persistence.planned_sessions import PlannedSessionRow
from app.services.metrics import MetricSummary, SessionMetricsService, summarise
from app.services.planned_sessions import PlannedSessionService

logger = get_logger(__name__)

#: `entity_type` written on this use-case's audit rows.
ENTITY_TYPE = "session_match"

#: `entity_type` for the rows that are about the completed session rather than
#: about a link — marking one unplanned, merging two.
SESSION_ENTITY_TYPE = "session"

#: `entity_type` for the planned side, used by the missed sweep.
PLANNED_ENTITY_TYPE = "planned_session"

#: Job id under which the missed sweep is registered with APScheduler.
MISSED_JOB_ID = "matching_missed_sweep"

#: Longest gap between two recordings that a **merge** will bridge, in seconds.
#: The case the build plan names is the garage-door stop — one ride, two files,
#: minutes apart. Six hours is generous for that and still refuses the mistake
#: this guard exists for: merging the morning commute into the evening ride,
#: which would produce one session with a five-hour hole in the middle of it
#: and a training load computed over both.
MAX_MERGE_GAP_S = 6 * 60 * 60


@dataclass(frozen=True, slots=True)
class MatchOutcome:
    """What one run of matching decided about one completed session.

    Args:
        session: The session that was matched.
        link: The link that now stands, or ``None`` when nothing was proposed.
        candidates: How many planned sessions were in the window and unlinked.
        sticky: True when an existing confirmed or displaced link was found and
            left alone — the run decided nothing, deliberately.
    """

    session: SessionRow
    link: SessionMatchRow | None
    candidates: int
    sticky: bool = False


@dataclass(frozen=True, slots=True)
class MatchContext:
    """Both rows one link joins, loaded together for a page of them."""

    session: SessionRow
    planned: PlannedSessionRow


class MatchingService:
    """Use-cases for match links. Raises AppError subclasses."""

    def __init__(
        self,
        session: AsyncSession,
        links: SessionMatchRepository,
        prompts: EveningPromptRepository,
        sessions: SessionRepository,
        planned: PlannedSessionService,
        metrics: SessionMetricsService,
        athletes: AthleteRepository,
        audit: AuditRepository,
    ) -> None:
        self._session = session
        self._links = links
        self._prompts = prompts
        self._sessions = sessions
        self._planned = planned
        self._metrics = metrics
        self._athletes = athletes
        self._audit = audit

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and everything it reads to one database session."""
        return cls(
            session,
            SessionMatchRepository(session),
            EveningPromptRepository(session),
            SessionRepository(session),
            PlannedSessionService.from_session(session),
            SessionMetricsService.from_session(session),
            AthleteRepository(session),
            AuditRepository(session),
        )

    # --- reads ---------------------------------------------------------------

    async def list(
        self,
        *,
        status: MatchLinkStatus | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[SessionMatchRow], int]:
        """A page of links, newest first, plus the total."""
        return await self._links.list(status=status, offset=offset, limit=limit)

    async def get(self, link_id: uuid.UUID) -> SessionMatchRow:
        """One link.

        Raises:
            NotFoundError: When no link has that id.
        """
        row = await self._links.get(link_id)
        if row is None:
            raise NotFoundError(f"Match {link_id} not found")
        return row

    async def for_sessions(
        self, session_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, SessionMatchRow]:
        """Links for a page of completed sessions, in one query."""
        return await self._links.for_sessions(session_ids)

    async def for_planned_sessions(
        self, planned_session_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, SessionMatchRow]:
        """Links for a page of planned sessions, in one query."""
        return await self._links.for_planned_sessions(planned_session_ids)

    async def contexts(
        self, links: Sequence[SessionMatchRow]
    ) -> dict[uuid.UUID, MatchContext]:
        """Both sides of every link on a page, in two queries.

        The proposal inbox renders "your 2 h ride on Tuesday, against the
        90 min endurance session planned for Monday" per row, and fetching two
        resources per row to say it is what this exists to avoid.
        """
        sessions = await self._sessions.by_ids([link.session_id for link in links])
        planned = await self._planned.by_ids(
            [link.planned_session_id for link in links]
        )
        return {
            link.id: MatchContext(
                session=sessions[link.session_id],
                planned=planned[link.planned_session_id],
            )
            for link in links
            if link.session_id in sessions and link.planned_session_id in planned
        }

    async def prompts(
        self,
        *,
        status: EveningPromptStatus | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[EveningPromptRow], int]:
        """A page of evening prompts, newest first, plus the total (WP-7 reads)."""
        return await self._prompts.list(status=status, offset=offset, limit=limit)

    # --- automatic matching --------------------------------------------------

    async def match_session(
        self, session_id: uuid.UUID, *, actor: Actor, rematch: bool = False
    ) -> MatchOutcome:
        """Find the planned session this recording answers to (WP-6.1-6.3).

        Candidates are planned sessions of the same discipline, within a day
        either side of the session's athlete-local date, that nothing is
        already linked to. Each is scored by `app.domain.matching.similarity`
        against the prescription **as frozen** — the intent version in force
        and the anchor versions it pinned — and the best one is linked,
        proposed or dropped by :func:`app.domain.matching.classify`.

        Idempotent: running it again over an unchanged session reaches the same
        verdict and rewrites the same row. An existing `confirmed` or
        `displaced` link ends the run before anything is scored.

        Args:
            session_id: The completed session to match.
            actor: Credited on the audit row and on the link.
            rematch: Whether this is an explicit re-run. It only widens what
                may be replaced — an automatic run leaves an existing link
                alone, an explicit one revises an open link and reconsiders a
                session the athlete or an earlier run marked `unplanned`
                (D142).

        Raises:
            NotFoundError: When no session has that id.
        """
        row = await self._require_session(session_id)
        existing = await self._links.for_session(session_id)
        if existing is not None and existing.status in STICKY_STATUSES:
            return MatchOutcome(session=row, link=existing, candidates=0, sticky=True)
        if existing is not None and not rematch:
            # An automatic run over a session that already has an open link is
            # a no-op rather than a re-score: the only caller is ingest, and a
            # session is ingested once.
            return MatchOutcome(session=row, link=existing, candidates=0)

        discipline = as_planned_discipline(row.discipline)
        if discipline is None:
            # A walk, a swim, a sport the head unit did not name. There is no
            # planned session it could ever be, which is a fact, not a failure.
            return await self._settle_unplanned(
                row, existing, actor=actor, reason="discipline is never planned"
            )

        earliest, latest = candidate_window(row.local_date)
        candidates = await self._links.candidates(
            for_session=session_id,
            discipline=discipline,
            earliest=earliest,
            latest=latest,
        )
        summary = await self._summary(session_id)
        scored = [
            (planned, await self._score(row, planned, summary))
            for planned in candidates
        ]
        best = self._best(row, scored)
        if best is None:
            return await self._settle_unplanned(
                row,
                existing,
                actor=actor,
                reason=(
                    "no planned session within a day of this one scores above "
                    "the proposal floor"
                ),
                candidates=len(candidates),
            )

        planned, result = best
        status = classify(result.score)
        if status is None:  # pragma: no cover — `_best` already dropped these
            return await self._settle_unplanned(
                row, existing, actor=actor, reason="below the proposal floor"
            )
        link = await self._apply(
            row,
            planned,
            status=status,
            result=result,
            actor=actor,
            existing=existing,
            action="match.rematched" if rematch else "match.proposed",
        )
        await commit(self._session)
        await self._session.refresh(row)
        return MatchOutcome(session=row, link=link, candidates=len(candidates))

    def _best(
        self,
        row: SessionRow,
        scored: Sequence[tuple[PlannedSessionRow, Similarity]],
    ) -> tuple[PlannedSessionRow, Similarity] | None:
        """The candidate to link, or ``None`` when none clears the floor.

        Two-planned-one-done (build plan's case table): the **best** candidate
        takes the link and every other one is simply left open, which is what
        makes the second ride of the day find the session the first did not
        take. Ties break on the nearer date — a session planned for the day the
        ride happened beats an identical one the day before.
        """
        eligible = [
            (planned, result)
            for planned, result in scored
            if classify(result.score) is not None
        ]
        if not eligible:
            return None
        chosen = eligible[0]
        for candidate in eligible[1:]:
            if better(candidate[1], chosen[1]) or (
                candidate[1].score == chosen[1].score
                and date_distance(row.local_date, candidate[0].date)
                < date_distance(row.local_date, chosen[0].date)
            ):
                chosen = candidate
        return chosen

    # --- manual operations (build plan WP-6.6) -------------------------------

    async def link(
        self,
        *,
        session_id: uuid.UUID,
        planned_session_id: uuid.UUID,
        actor: Actor,
        displaced: bool = False,
    ) -> SessionMatchRow:
        """Link a session to a planned session by hand.

        Always `confirmed` — or `displaced` when the athlete is saying "I
        trained, and it was not this" (WP-6.4). Either way the link is sticky:
        no re-run of matching revises it. The similarity is still computed and
        stored, because a deliberate link at 0.12 is worth being able to see.

        Raises:
            NotFoundError: When either side does not exist.
            ValidationError: When the disciplines cannot correspond.
            ConflictError: When either side is already linked. Unlink or swap
                first — silently replacing a link would lose the states it was
                holding for the restore.
        """
        row = await self._require_session(session_id)
        planned = await self._planned.get(planned_session_id)
        await self._require_unlinked(session_id, planned_session_id)
        self._require_compatible(row, planned)

        result = await self._score(row, planned, await self._summary(session_id))
        link = await self._apply(
            row,
            planned,
            status=(
                MatchLinkStatus.DISPLACED if displaced else MatchLinkStatus.CONFIRMED
            ),
            result=result,
            actor=actor,
            existing=None,
            action="match.linked",
        )
        await commit(self._session)
        return link

    async def confirm(self, link_id: uuid.UUID, *, actor: Actor) -> SessionMatchRow:
        """Accept a proposal (or an automatic link) as the athlete's own.

        Raises:
            NotFoundError: When no link has that id.
            ConflictError: When it is already the athlete's — confirming twice
                is not an error the second time, but confirming a `displaced`
                link would silently turn "I did something else" into "I did
                this".
        """
        link = await self.get(link_id)
        if link.status is MatchLinkStatus.DISPLACED:
            raise ConflictError(
                f"Match {link_id} says the athlete trained instead of this "
                "session; confirming it would claim the opposite. Unlink it "
                "and link the session again if that is what you mean."
            )
        row = await self._require_session(link.session_id)
        planned = await self._planned.get(link.planned_session_id)
        previous = link.status
        link.status = MatchLinkStatus.CONFIRMED
        link.confirmed_at = dt.datetime.now(dt.UTC)
        await self._links.add(link)
        await self._settle_statuses(row, planned, link)
        await self._audit.record(
            actor=actor,
            action="match.confirmed",
            entity_type=ENTITY_TYPE,
            entity_id=link.id,
            payload=_payload(link, row, planned) | {"from_status": previous.value},
        )
        await commit(self._session)
        await self._session.refresh(link)
        return link

    async def reject(self, link_id: uuid.UUID, *, actor: Actor) -> SessionRow:
        """Refuse a proposal: drop the link and call the session unplanned.

        The negative of :meth:`confirm`, and not the same as :meth:`unlink`.
        Unlinking restores exactly what was there before; rejecting is the
        athlete saying "this ride was not that session", which leaves the ride
        `unplanned` and the planned session open for something else.

        Raises:
            NotFoundError: When no link has that id.
            ConflictError: When the link is the athlete's own — a confirmed or
                displaced link is unlinked, not rejected.
        """
        link = await self.get(link_id)
        if link.status in STICKY_STATUSES:
            raise ConflictError(
                f"Match {link_id} is {link.status.value!r}, which the athlete "
                "set deliberately; unlink it instead of rejecting it."
            )
        row = await self._restore(link, actor=actor, action="match.rejected")
        row.status = SessionMatchStatus.UNPLANNED
        await self._sessions.add(row)
        await commit(self._session)
        await self._session.refresh(row)
        return row

    async def unlink(self, link_id: uuid.UUID, *, actor: Actor) -> SessionRow:
        """Remove a link and put both sides back exactly as they were (WP-6.8).

        Raises:
            NotFoundError: When no link has that id.
        """
        link = await self.get(link_id)
        row = await self._restore(link, actor=actor, action="match.unlinked")
        await commit(self._session)
        await self._session.refresh(row)
        return row

    async def swap(
        self, link_id: uuid.UUID, *, planned_session_id: uuid.UUID, actor: Actor
    ) -> SessionMatchRow:
        """Retarget a link at a different planned session.

        One operation rather than an unlink and a link, because it is one
        decision: the old planned session goes back to exactly what it was and
        the new one takes the link. A retarget is always the athlete's, so an
        open link comes out `confirmed` — but a `displaced` link **stays
        displaced**: it says "I trained, and it was not this", and the swap
        changes which *this* was, not the claim itself. Promoting it would
        hand WP-7 adherence axes for a prescription the ride never followed —
        the exact flip `confirm` refuses with a 409.

        Raises:
            NotFoundError: When the link or the new planned session is absent.
            ValidationError: When the disciplines cannot correspond.
            ConflictError: When the new planned session is already linked.
        """
        link = await self.get(link_id)
        if link.planned_session_id == planned_session_id:
            raise ValidationError(
                "That is the planned session this match already points at"
            )
        row = await self._require_session(link.session_id)
        target = await self._planned.get(planned_session_id)
        self._require_compatible(row, target)
        if await self._links.for_planned_session(planned_session_id) is not None:
            raise ConflictError(
                f"Planned session {planned_session_id} is already matched to "
                "another session; unlink that one first."
            )

        previous_planned = await self._planned.get(link.planned_session_id)
        previous_planned.status = link.previous_planned_status
        await self._planned.save(previous_planned)
        from_id = link.planned_session_id

        status = (
            MatchLinkStatus.DISPLACED
            if link.status is MatchLinkStatus.DISPLACED
            else MatchLinkStatus.CONFIRMED
        )
        result = await self._score(row, target, await self._summary(row.id))
        link.planned_session_id = target.id
        link.previous_planned_status = target.status
        link.status = status
        link.confirmed_at = dt.datetime.now(dt.UTC)
        link.similarity = result.score
        link.breakdown = similarity_to_json(result)
        await self._links.add(link)
        await self._settle_statuses(row, target, link)
        await self._audit.record(
            actor=actor,
            action="match.swapped",
            entity_type=ENTITY_TYPE,
            entity_id=link.id,
            payload=_payload(link, row, target)
            | {
                "from_planned_session_id": str(from_id),
                "restored_planned_status": previous_planned.status.value,
            },
        )
        await commit(self._session)
        await self._session.refresh(link)
        return link

    async def mark_unplanned(
        self, session_id: uuid.UUID, *, actor: Actor
    ) -> SessionRow:
        """Declare that a session answers to nothing on the calendar.

        Drops any open link on the way (restoring the planned session), and
        refuses to drop one the athlete already confirmed — that would be two
        contradictory statements, and the second one should be an unlink.

        Raises:
            NotFoundError: When no session has that id.
            ConflictError: When the session carries a confirmed or displaced
                link.
        """
        row = await self._require_session(session_id)
        link = await self._links.for_session(session_id)
        if link is not None and link.status in STICKY_STATUSES:
            raise ConflictError(
                f"Session {session_id} is {link.status.value!r} against a "
                "planned session; unlink it before calling it unplanned."
            )
        outcome = await self._settle_unplanned(
            row, link, actor=actor, reason="marked unplanned by hand"
        )
        return outcome.session

    async def merge(
        self, session_id: uuid.UUID, *, absorbed_session_id: uuid.UUID, actor: Actor
    ) -> SessionRow:
        """Fold a second recording of the same ride into one session (WP-6.5).

        The garage-door case: a head unit stopped and restarted leaves two
        files, two sessions and half a ride each. The absorbed session's
        **recordings are kept** and re-parented onto the survivor, whose time
        span widens to cover both; the absorbed session row itself goes, and
        with it the metric artefacts computed over half a ride.

        The parquet frames are not touched (D143): a stream file is addressed
        by *recording* id, and the concatenated view a scorer needs is
        assembled on read by `app.ingest.analysis`, which is where reading
        parquet is allowed. The survivor's metrics are stale the moment this
        returns — recomputing them means reading those frames, which a service
        may not do — so the route that calls this recomputes afterwards, on the
        same path a manual recompute takes.

        Raises:
            NotFoundError: When either session is absent.
            ValidationError: When a session is merged into itself, when either
                side has no recording to merge, when the disciplines differ, or
                when the two are further apart than :data:`MAX_MERGE_GAP_S`.
            ConflictError: When the absorbed session is linked to a planned
                session. Deleting the absorbed row would cascade its link away
                at the database level — below the service that restores
                statuses — leaving the planned session `completed` with no
                link, unreachable by re-match (which only offers `planned` and
                `missed` candidates) and invisible to the missed sweep. The
                athlete states what the ride was by unlinking first.
        """
        if session_id == absorbed_session_id:
            raise ValidationError("A session cannot be merged into itself")
        survivor = await self._require_session(session_id)
        absorbed = await self._require_session(absorbed_session_id)
        _check_mergeable(survivor, absorbed)
        absorbed_link = await self._links.for_session(absorbed_session_id)
        if absorbed_link is not None:
            raise ConflictError(
                f"Session {absorbed_session_id} is linked to a planned session "
                f"({absorbed_link.status.value}); unlink it before merging, or "
                "merge the other way around."
            )

        moved: list[RecordingRow] = list(absorbed.recordings)
        # A **move**, both halves of it, and both before the flush. The
        # recordings relationship cascades `delete-orphan`, so a recording
        # taken out of one collection and not put into another is deleted — and
        # the stream file it addresses would then be an orphan on disk with no
        # row naming it. Removing and appending in the same unit of work is
        # what tells SQLAlchemy this is a re-parenting rather than a deletion;
        # `absorbed.recordings` is empty by the time it is deleted, so nothing
        # cascades.
        for recording in moved:
            absorbed.recordings.remove(recording)
            survivor.recordings.append(recording)
        survivor.start_time = min(survivor.start_time, absorbed.start_time)
        survivor.end_time = max(survivor.end_time, absorbed.end_time)
        survivor.local_date = session_date(survivor.start_time, survivor.timezone)
        await self._sessions.add(survivor)
        await self._session.delete(absorbed)

        await self._audit.record(
            actor=actor,
            action="session.merged",
            entity_type=SESSION_ENTITY_TYPE,
            entity_id=survivor.id,
            payload={
                "absorbed_session_id": str(absorbed_session_id),
                "recordings_moved": [str(recording.id) for recording in moved],
                "start_time": survivor.start_time.isoformat(),
                "end_time": survivor.end_time.isoformat(),
                "local_date": survivor.local_date.isoformat(),
            },
        )
        await commit(self._session)
        await self._session.refresh(survivor)
        return survivor

    # --- the missed sweep (build plan WP-6.7) --------------------------------

    async def mark_missed(
        self, *, actor: Actor, today_local: dt.date | None = None
    ) -> Sequence[PlannedSessionRow]:
        """Mark every planned session whose grace has run out, and prompt.

        "No link by the end of day+1, athlete-local" — the rule itself is
        `app.domain.matching.is_missed` and this only supplies the day and
        writes the rows. Idempotent: a session already marked missed is not a
        candidate, and the unique constraint on `evening_prompts` means a
        second sweep cannot raise a second prompt for one session.

        **Nothing happens while the plan is paused.** Pausing is a statement
        about enforcement (`app.domain.plan`), and a week the athlete stepped
        away from filling up with `missed` is exactly the enforcement it
        suspends.

        Args:
            actor: Credited on the audit rows — `system` for the sweep.
            today_local: The athlete's local date; resolved from
                ``MATCHING__TIMEZONE`` when omitted.

        Returns:
            The sessions marked missed, oldest first. Empty while paused.
        """
        if await self._plan_state() is PlanState.PAUSED:
            logger.info("missed_sweep_skipped", reason="plan_paused")
            return []
        today = today_local or athlete_today()
        settings = get_settings()
        due = await self._links.unanswered_planned_sessions(
            on_or_before=missed_on_or_before(today),
            limit=settings.matching.missed_scan_batch,
        )
        raised: list[PlannedSessionRow] = []
        now = dt.datetime.now(dt.UTC)
        for planned in due:
            if not is_missed(planned.date, today):  # pragma: no cover — query bound
                continue
            planned.status = SessionStatus.MISSED
            await self._planned.save(planned)
            if await self._prompts.for_planned_session(planned.id) is None:
                await self._prompts.add(
                    EveningPromptRow(
                        planned_session_id=planned.id,
                        kind=EveningPromptKind.MISSED_SESSION,
                        status=EveningPromptStatus.PENDING,
                        expires_at=now + dt.timedelta(hours=PROMPT_TTL_HOURS),
                    )
                )
            await self._audit.record(
                actor=actor,
                action="planned_session.missed",
                entity_type=PLANNED_ENTITY_TYPE,
                entity_id=planned.id,
                payload={
                    "date": planned.date.isoformat(),
                    "discipline": planned.discipline.value,
                    "today_local": today.isoformat(),
                },
            )
            raised.append(planned)
        if raised:
            await commit(self._session)
        return raised

    # --- helpers -------------------------------------------------------------

    async def _require_session(self, session_id: uuid.UUID) -> SessionRow:
        """The completed session, or a 404.

        Raises:
            NotFoundError: When no session has that id.
        """
        row = await self._sessions.get(session_id)
        if row is None:
            raise NotFoundError(f"Session {session_id} not found")
        return row

    async def _require_unlinked(
        self, session_id: uuid.UUID, planned_session_id: uuid.UUID
    ) -> None:
        """Refuse a manual link when either side already has one.

        Raises:
            ConflictError: When either side is linked.
        """
        if await self._links.for_session(session_id) is not None:
            raise ConflictError(
                f"Session {session_id} is already matched; unlink it or swap "
                "the existing match to another planned session."
            )
        if await self._links.for_planned_session(planned_session_id) is not None:
            raise ConflictError(
                f"Planned session {planned_session_id} is already matched to "
                "another session; unlink that one first."
            )

    def _require_compatible(self, row: SessionRow, planned: PlannedSessionRow) -> None:
        """Refuse a link between disciplines that cannot correspond.

        The date window is **not** checked: a manual link is the athlete
        overruling the machine, and "that ride three days ago was this
        session" is a legitimate thing to say. The discipline is different —
        a swim is not a strength session under any reading, and linking them
        would hand WP-7 a prescription it has no axes for.

        Raises:
            ValidationError: When the two disciplines cannot correspond.
        """
        discipline = as_planned_discipline(row.discipline)
        if discipline is None:
            raise ValidationError(
                f"This session was recorded as {SessionDiscipline.OTHER.value!r}, "
                "which is not a discipline anything is planned in. Correct the "
                "session's discipline first."
            )
        if discipline is not planned.discipline:
            raise ValidationError(
                f"A {discipline.value} session cannot answer to a "
                f"{planned.discipline.value} planned session"
            )

    async def _summary(self, session_id: uuid.UUID) -> MetricSummary | None:
        """The metric artefact in force for one session, reduced, or ``None``."""
        row = await self._metrics.get_current(session_id)
        return summarise(row) if row is not None else None

    async def _plan_state(self) -> PlanState:
        """Whether the plan is being enforced. Active before a profile exists."""
        profile = await self._athletes.get()
        return profile.plan_state if profile is not None else PlanState.ACTIVE

    async def _score(
        self,
        row: SessionRow,
        planned: PlannedSessionRow,
        summary: MetricSummary | None,
    ) -> Similarity:
        """Score one recording against one prescription, as it was frozen."""
        anchors = (await self._planned.pins([planned]))[planned.id]
        body = workout_body_from_json(planned.current_intent.structure)
        return similarity(_evidence(row, body, anchors, summary))

    async def _apply(
        self,
        row: SessionRow,
        planned: PlannedSessionRow,
        *,
        status: MatchLinkStatus,
        result: Similarity,
        actor: Actor,
        existing: SessionMatchRow | None,
        action: str,
    ) -> SessionMatchRow:
        """Write (or revise) the link and move both sides' statuses with it.

        An `existing` open link is revised in place rather than deleted and
        rewritten: the statuses it is holding for the restore are the ones from
        *before* any of this, and a rewrite would capture the states this link
        itself produced.
        """
        if existing is None:
            link = SessionMatchRow(
                session_id=row.id,
                planned_session_id=planned.id,
                previous_session_status=row.status,
                previous_planned_status=planned.status,
            )
        else:
            link = existing
            if link.planned_session_id != planned.id:
                await self._release(link)
                link.planned_session_id = planned.id
                link.previous_planned_status = planned.status
        link.status = status
        link.similarity = result.score
        link.breakdown = similarity_to_json(result)
        link.created_by = str(actor)
        if status in STICKY_STATUSES:
            link.confirmed_at = dt.datetime.now(dt.UTC)
        link = await self._links.add(link)
        await self._settle_statuses(row, planned, link)
        await self._audit.record(
            actor=actor,
            action=action,
            entity_type=ENTITY_TYPE,
            entity_id=link.id,
            payload=_payload(link, row, planned),
        )
        return link

    async def _release(self, link: SessionMatchRow) -> None:
        """Put the planned session a link is moving away from back as it was."""
        planned = await self._planned.get(link.planned_session_id)
        planned.status = link.previous_planned_status
        await self._planned.save(planned)

    async def _settle_statuses(
        self,
        row: SessionRow,
        planned: PlannedSessionRow,
        link: SessionMatchRow,
    ) -> None:
        """Move both sides to the statuses one link status implies.

        The table in the module docstring, in one place. `pending` **restores**
        both sides to what the link recorded rather than moving nothing: for a
        fresh proposal the recorded statuses are the current ones and the
        restore is a no-op — a question moves nothing (D140) — but a link
        *revised down* to pending (an `auto_high` whose re-score fell below the
        threshold) is holding the pre-link statuses, and an early return would
        strand the session at `matched` under an unanswered proposal.
        """
        status = link.status
        if status is MatchLinkStatus.PENDING:
            row.status = link.previous_session_status
            planned.status = link.previous_planned_status
        elif status is MatchLinkStatus.DISPLACED:
            row.status = SessionMatchStatus.DISPLACED
            planned.status = SessionStatus.DISPLACED
        else:
            row.status = SessionMatchStatus.MATCHED
            planned.status = SessionStatus.COMPLETED
        await self._sessions.add(row)
        await self._planned.save(planned)

    async def _restore(
        self, link: SessionMatchRow, *, actor: Actor, action: str
    ) -> SessionRow:
        """Delete a link and put both sides back where it found them (WP-6.8)."""
        row = await self._require_session(link.session_id)
        planned = await self._planned.get(link.planned_session_id)
        payload = _payload(link, row, planned) | {
            "restored_session_status": link.previous_session_status.value,
            "restored_planned_status": link.previous_planned_status.value,
        }
        row.status = link.previous_session_status
        planned.status = link.previous_planned_status
        await self._links.delete(link)
        await self._sessions.add(row)
        await self._planned.save(planned)
        await self._audit.record(
            actor=actor,
            action=action,
            entity_type=ENTITY_TYPE,
            entity_id=link.id,
            payload=payload,
        )
        return row

    async def _settle_unplanned(
        self,
        row: SessionRow,
        existing: SessionMatchRow | None,
        *,
        actor: Actor,
        reason: str,
        candidates: int = 0,
    ) -> MatchOutcome:
        """Leave a session standing as its own thing, and say why (WP-6.3)."""
        if existing is not None:
            await self._restore(existing, actor=actor, action="match.unlinked")
        row.status = SessionMatchStatus.UNPLANNED
        await self._sessions.add(row)
        await self._audit.record(
            actor=actor,
            action="session.unplanned",
            entity_type=SESSION_ENTITY_TYPE,
            entity_id=row.id,
            payload={
                "local_date": row.local_date.isoformat(),
                "discipline": row.discipline.value,
                "candidates": candidates,
                "reason": reason,
            },
        )
        await commit(self._session)
        await self._session.refresh(row)
        return MatchOutcome(session=row, link=None, candidates=candidates)


# --- turning two rows into the domain's evidence ------------------------------


def _evidence(
    row: SessionRow,
    body: WorkoutBody,
    anchors: Mapping[AnchorType, PinnedAnchor],
    summary: MetricSummary | None,
) -> MatchEvidence:
    """Everything the domain compares, read off the two sides.

    Strength and endurance take different halves of the same three components
    (D139): an endurance prescription has seconds, a power or heart-rate target
    and work steps; a strength one has none of the first two and counts **sets**
    instead of intervals. Nothing is substituted for what is missing — the
    domain renormalises over what it was given.
    """
    if isinstance(body, StrengthWorkout):
        return MatchEvidence(
            planned_units=predict_strength_volume(body).total_sets,
            performed_units=len(row.logged_sets) if row.logged_sets else None,
            structure_basis=StructureBasis.SETS,
            actual_duration_s=session_duration_s(row),
        )
    return _endurance_evidence(row, body, anchors, summary)


def _endurance_evidence(
    row: SessionRow,
    body: EnduranceWorkout,
    anchors: Mapping[AnchorType, PinnedAnchor],
    summary: MetricSummary | None,
) -> MatchEvidence:
    """The endurance half of :func:`_evidence`."""
    planned_np = planned_power_intensity(body, anchors)
    actual_np = summary.normalized_power if summary is not None else None
    planned_bpm = planned_hr_intensity(body, anchors)
    actual_bpm = summary.average_hr if summary is not None else None
    # Power first when both sides have it: it is the channel the prescription
    # is written in and the one WP-7 scores against. Heart rate is the
    # fallback the build plan names, not a second opinion.
    if planned_np is not None and actual_np is not None:
        intensity = (planned_np, actual_np, IntensityBasis.POWER)
    elif planned_bpm is not None and actual_bpm is not None:
        intensity = (planned_bpm, actual_bpm, IntensityBasis.HR)
    else:
        intensity = (None, None, None)
    planned_intensity, actual_intensity, basis = intensity
    return MatchEvidence(
        planned_duration_s=total_duration_s(body),
        actual_duration_s=session_duration_s(row),
        planned_intensity=planned_intensity,
        actual_intensity=actual_intensity,
        intensity_basis=basis,
        planned_units=planned_work_steps(body),
        # No power means no detector output to count, and an empty interval
        # list from a ride that recorded nothing to detect on is not "zero
        # intervals" — it is nothing to say.
        performed_units=(
            summary.interval_count
            if summary is not None and actual_np is not None
            else None
        ),
        structure_basis=StructureBasis.INTERVALS,
    )


def _check_mergeable(survivor: SessionRow, absorbed: SessionRow) -> None:
    """Refuse merges that are not the case this feature exists for.

    Raises:
        ValidationError: When either side has no recording, the disciplines
            differ, or the two are further apart than :data:`MAX_MERGE_GAP_S`.
    """
    if not survivor.recordings or not absorbed.recordings:
        raise ValidationError(
            "Merging joins two device recordings of one ride; a session typed "
            "in by hand has no recording to merge."
        )
    if survivor.discipline is not absorbed.discipline:
        raise ValidationError(
            f"A {survivor.discipline.value} session and a "
            f"{absorbed.discipline.value} session are not two halves of one "
            "recording"
        )
    gap = max(
        (absorbed.start_time - survivor.end_time).total_seconds(),
        (survivor.start_time - absorbed.end_time).total_seconds(),
    )
    if gap > MAX_MERGE_GAP_S:
        raise ValidationError(
            f"These sessions are {gap / 3600:.1f} h apart, further than the "
            f"{MAX_MERGE_GAP_S // 3600} h a merge will bridge. Merging is for "
            "one ride recorded as two files, not for two rides."
        )


def _payload(
    link: SessionMatchRow, row: SessionRow, planned: PlannedSessionRow
) -> dict[str, Any]:
    """One link, as JSON, for the audit trail."""
    return {
        "session_id": str(row.id),
        "planned_session_id": str(planned.id),
        "status": link.status.value,
        "similarity": link.similarity,
        "components": [
            part.get("component") for part in link.breakdown.get("components", [])
        ],
        "session_status": row.status.value,
        "planned_status": planned.status.value,
        "local_date": row.local_date.isoformat(),
        "planned_date": planned.date.isoformat(),
    }


# --- the athlete's clock, and the sweep that reads it -------------------------


def athlete_today(now: dt.datetime | None = None) -> dt.date:
    """Today's date in the athlete's own timezone (``MATCHING__TIMEZONE``).

    Raises:
        ValueError: When the configured timezone cannot be resolved. Loud
            rather than defaulted: a sweep that silently fell back to UTC would
            mark sessions missed up to a day early for anybody east of it.
    """
    zone = get_settings().matching.timezone
    parse_timezone(zone)  # refuses an unusable value before it reaches a date
    return session_date(now or dt.datetime.now(dt.UTC), zone)


async def run_missed_sweep() -> None:
    """The scheduled sweep. Never raises — a failed run must not kill the job."""
    try:
        async with session_scope() as session:
            marked = await MatchingService.from_session(session).mark_missed(
                actor=Actor.system()
            )
        logger.info("missed_sweep_ran", marked=len(marked))
    except Exception:  # noqa: BLE001 — a scheduler job that raises stops running
        logger.exception("missed_sweep_failed")


def register_missed_sessions_job(scheduler: BaseScheduler) -> None:
    """Register the missed-session sweep on the application scheduler.

    Registered by the work package that needs it, like the inbox sweep, rather
    than in `app.core.scheduler`, which owns no jobs of its own. ``coalesce``
    and ``max_instances=1`` because the sweep is idempotent and two of them
    over one backlog would race on the prompt's unique constraint for nothing.
    """
    interval = get_settings().matching.missed_scan_interval_seconds
    scheduler.add_job(
        run_missed_sweep,
        "interval",
        seconds=interval,
        id=MISSED_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("missed_sweep_registered", seconds=interval)

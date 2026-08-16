"""The plan as the calendar reads it: one week, projected for rendering.

A read-only use-case, and the only one in this layer that exists for a
*screen*. Everything it returns is derivable from what
`app.services.planned_sessions` already serves, but deriving it per card costs
the calendar a request per session and a step-tree walk in the browser, so the
week is assembled once, here, where the domain's own helpers are.

Two shape decisions the adapters inherit:

* the week is **seven days**, always, including the empty ones — a calendar
  renders a grid, and a projection that omitted Thursday would make every
  client rebuild it;
* each card carries what a card shows and nothing more. The session's full
  detail — the step tree, the criteria, the pins, the intent history — stays
  behind `GET /api/v1/planned-sessions/{id}`, which is what the day sheet
  opens.

**Planned and completed are separate columns and never a single number.** The
week also carries what actually happened — the recorded sessions dated inside
the window, with the training load off their current metric artefacts — and
every completed total carries its own coverage pair for the same reason the
planned ones do. A week where two of five rides have no load must not read as
a light week, and a week that added planned and completed would be a number
with no meaning at all.

The weekly polarization index counts **one channel per session** (A5.4), and
the rule that chose it travels in the payload: summing a session's power zones
and its heart-rate zones counts its duration twice.
"""

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.activity import SessionMatchStatus, as_planned_discipline
from app.domain.anchors import AnchorType
from app.domain.athlete import Discipline
from app.domain.matching import MatchLinkStatus
from app.domain.metrics import (
    ONE_CHANNEL_PER_SESSION_RULE,
    Measured,
    polarization_index,
)
from app.domain.plan import WEEK_DAYS, week_dates, week_start
from app.domain.prediction import (
    PinnedAnchor,
    predict_endurance_load,
    predict_strength_volume,
)
from app.domain.purpose import Purpose
from app.domain.scoring import CompletionState, Verdict, completion_state, worst_state
from app.domain.sessions import SessionStatus
from app.domain.strength import StrengthWorkout
from app.domain.workout import WorkoutBody, workout_body_from_json
from app.persistence.activity import (
    SessionRepository,
    SessionRow,
    session_duration_s,
)
from app.persistence.matching import SessionMatchRepository, SessionMatchRow
from app.persistence.planned_sessions import (
    PlannedSessionRepository,
    PlannedSessionRow,
)
from app.persistence.scoring import (
    SessionScoreRepository,
    VerdictDeclarationRepository,
)
from app.persistence.workouts import WorkoutRepository
from app.services.anchors import AnchorService, parse_pins, resolve_pins
from app.services.metrics import SessionMetricsService, summarise
from app.services.workouts import WorkoutSummary

#: Most sessions one week's projection will read. Not pagination — a week is
#: rendered whole or not at all — but a bound, so a corrupted date column
#: cannot make one request load the entire table. Two hundred is roughly
#: thirty sessions a day; a plan that dense is not a plan.
MAX_WEEK_SESSIONS = 200

#: The same bound on the *completed* side. A week cannot hold more recorded
#: sessions than planned ones for any honest reason either.
MAX_WEEK_COMPLETED = 200


@dataclass(frozen=True, slots=True)
class WeekSession:
    """One planned session, as a calendar card needs it."""

    id: uuid.UUID
    date: dt.date
    discipline: Discipline
    purpose: Purpose
    status: SessionStatus
    #: The library workout this was planned from, if it still exists. ``None``
    #: for an inline prescription (and for one whose library entry has since
    #: been deleted): the card then labels itself from the purpose, which is
    #: the one thing every session has.
    title: str | None
    workout_id: uuid.UUID | None
    #: Prescribed seconds. ``None`` for a strength session, and for an
    #: endurance one with a distance-based step — there is no duration to show
    #: rather than a zero.
    planned_duration_s: int | None
    #: Prescribed working sets, for a strength session; ``None`` otherwise.
    total_sets: int | None
    #: Flattened steps (endurance) or prescription lines (strength).
    step_count: int
    #: The one-line intent, the athlete's own words.
    intent_text: str | None
    #: Which intent version the card is showing.
    intent_version: int
    #: TSS-equivalent this prescription is expected to cost, predicted from
    #: the frozen intent and the anchor versions it pinned. ``None`` whenever
    #: there is nothing honest to say — a strength session, a distance-based
    #: ride, a ride with no power target, an unpinned FTP.
    predicted_load: float | None
    #: Planned NP over the pinned FTP. ``None`` alongside `predicted_load`.
    predicted_intensity_factor: float | None
    #: Fraction of the prescribed duration that carried a power target, the
    #: same number `PredictedLoad.coverage` carries on the session resource
    #: and from the same computation. ``None`` exactly when `predicted_load`
    #: is: a load without it cannot be told apart from a fully covered one,
    #: and a card is where that mistake gets made.
    predicted_load_coverage: float | None
    #: Σ ``sets × reps × kg`` for a strength session, when its loads are in
    #: kilograms. Kilograms, **not** a load: never add this to
    #: `predicted_load` (spec v2 §5.4).
    predicted_volume_load_kg: float | None
    #: The recorded session linked to this card (WP-6), when there is one.
    #: ``None`` while nothing has been matched to it.
    matched_session_id: uuid.UUID | None = None
    #: What that link claims. A `pending` link is a **proposal**: `status`
    #: above is still `planned` until the athlete answers it, and the card
    #: renders a question rather than a completion.
    match_status: MatchLinkStatus | None = None
    #: What the week strip colours this card by (WP-7.5): the session's own
    #: status, refined by the athlete's declared verdict — or, until there is
    #: one, by the machine's suggestion. `completed` means matched and not yet
    #: judged, which is a real state and not a verdict.
    completion_state: CompletionState = CompletionState.PLANNED


@dataclass(frozen=True, slots=True)
class CompletedSession:
    """One recorded session, reduced to what a week total needs.

    Not a card: the session list and the session page render recorded
    sessions, and this exists only so the week rail can put what happened
    beside what was planned.

    Args:
        id: The completed session.
        date: Its athlete-local date — the day it is totalled into.
        discipline: The planning discipline it corresponds to, or ``None``
            for a recorded sport nothing is ever planned as (a walk, a swim).
            Those count in the week's flat totals and in no discipline row.
        duration_s: Recording time for a device session (pauses removed) and
            wall-clock duration for a typed-in one — the same number the
            session list shows, so the two cannot disagree.
        load: The selected training load from the session's current metric
            artefact; ``None`` when nothing has been computed yet or neither
            load model could be.
        easy_s: Seconds in the easy bands of the **one** channel A5.4's rule
            picked, or ``None`` when neither channel produced a distribution.
        moderate_s: The same, moderate.
        hard_s: The same, hard.
        match_status: Where this recording stands relative to the plan. The
            week strip needs it for one state a planned session can never be
            in: `unplanned`, which is a fact about a *ride* nothing was
            planned for.
    """

    id: uuid.UUID
    date: dt.date
    discipline: Discipline | None
    duration_s: float
    load: float | None
    easy_s: float | None
    moderate_s: float | None
    hard_s: float | None
    match_status: SessionMatchStatus = SessionMatchStatus.UNMATCHED


@dataclass(frozen=True, slots=True)
class WeekDay:
    """One day of the week, with the sessions planned for it and what was done."""

    date: dt.date
    sessions: tuple[WeekSession, ...]
    #: How many recorded sessions fell on this day.
    completed_session_count: int
    #: Their total duration; ``None`` — never 0 — when there were none.
    completed_duration_s: float | None
    #: Their total training load; ``None`` when none of them has one.
    completed_load: float | None
    #: The state the strip colours this day by: the **worst** of its cards'
    #: states, plus `unplanned` when something was ridden that nothing was
    #: planned for. ``None`` for a day on which nothing was planned and nothing
    #: was done — an empty day has no outcome, and colouring it `planned`
    #: would draw a session that does not exist.
    completion_state: CompletionState | None = None


@dataclass(frozen=True, slots=True)
class PlanWeekDiscipline:
    """One week's totals for one discipline.

    The two axes stay in their own columns: `planned_load` is TSS and
    `total_sets` counts strength sets, and there is deliberately no field that
    could hold their sum.

    Both totals carry their own coverage pair. A row is the only place a
    reader learns *why* a discipline's number is missing — a cycling row with
    a null load and three sessions says something a client cannot otherwise
    reconstruct, and a client that has to guess the reason will guess wrong.
    """

    discipline: Discipline
    session_count: int
    #: Prescribed seconds across this discipline's sessions that have one;
    #: ``None`` — never 0 — when none of them does.
    planned_duration_s: int | None
    #: How many of this discipline's sessions contributed to
    #: `planned_duration_s`, and how many could not.
    duration_sessions_counted: int
    duration_sessions_uncounted: int
    #: TSS across this discipline's predictable sessions; ``None`` when none
    #: of them is.
    planned_load: float | None
    #: How many of this discipline's sessions contributed to `planned_load`,
    #: and how many could not.
    load_sessions_counted: int
    load_sessions_uncounted: int
    #: Prescribed working sets; ``None`` for a discipline that has none.
    total_sets: int | None
    #: Recorded sessions of this discipline in the window.
    completed_session_count: int
    #: Their duration; ``None`` — never 0 — when there were none.
    completed_duration_s: float | None
    #: Their training load, and how many sessions could and could not
    #: contribute one. The pair is not decoration: a discipline whose only
    #: ride has no artefact yet must not read as a rest week.
    completed_load: float | None
    completed_load_sessions_counted: int
    completed_load_sessions_uncounted: int


@dataclass(frozen=True, slots=True)
class PlanWeek:
    """Seven consecutive days of the plan."""

    start: dt.date
    #: The last day in the window, inclusive — ``start + 6``.
    end: dt.date
    days: tuple[WeekDay, ...]
    #: Every session in the window, including any past `MAX_WEEK_SESSIONS`
    #: that `days` therefore does not carry.
    session_count: int
    #: Prescribed seconds across the week. ``None`` — never 0 — when no
    #: session contributed one, the empty week included: a week of two
    #: distance rides has no planned time, and a zero would read as a rest
    #: week exactly as a zero load would.
    planned_duration_s: int | None
    #: How many sessions contributed to `planned_duration_s`, and how many
    #: could not. Same contract as the load pair below.
    duration_sessions_counted: int
    duration_sessions_uncounted: int
    #: TSS across the sessions that could be predicted. ``None`` — never 0 —
    #: when none of them could: an unpredictable week has no load, and a zero
    #: would read as a rest week.
    planned_load: float | None
    #: How many sessions contributed to `planned_load`, and how many could
    #: not. Never render the total without them: a week of six sessions where
    #: only two are predictable must not read as a light week.
    load_sessions_counted: int
    load_sessions_uncounted: int
    #: Recorded sessions dated inside the window, whatever was planned —
    #: including any past `MAX_WEEK_COMPLETED` the read did not carry, the
    #: same contract `session_count` has on the planned side.
    completed_session_count: int
    #: Their total duration; ``None`` — never 0 — when there were none.
    completed_duration_s: float | None
    #: Their total training load, with its own coverage pair. Never added to
    #: `planned_load`: what was planned and what was done are two columns, and
    #: their sum is not a quantity.
    completed_load: float | None
    completed_load_sessions_counted: int
    completed_load_sessions_uncounted: int
    #: Treff's polarization index across the week's recorded sessions, over
    #: **one channel per session** (A5.4). ``None`` until it is computable —
    #: which needs time in all three bands, so an easy week has none.
    completed_polarization_index: float | None
    #: Why the index is missing, when it is; ``None`` when it is present.
    completed_polarization_not_assessed: str | None
    #: The channel rule the index counted by. Always present, because the
    #: number is meaningless without it.
    completed_polarization_rule: str
    #: How many recorded sessions contributed zone time, and how many could
    #: not.
    completed_polarization_sessions_counted: int
    completed_polarization_sessions_uncounted: int
    #: One row per discipline that has a session this week, in vocabulary
    #: order.
    by_discipline: tuple[PlanWeekDiscipline, ...]
    #: The recorded sessions in the window that **no card in this window
    #: references** — the rides nothing on this calendar accounts for, with
    #: their ids (#49).
    #:
    #: The rule is "no `WeekSession` above carries this id as
    #: `matched_session_id`", **not** `match_status is UNPLANNED`. A status is
    #: a fact about the *ride*; this list is a fact about *this window's*
    #: cards, and the two come apart in both directions. A ride dated here but
    #: matched to last week's card is `matched` and nothing here accounts for
    #: it — a status filter would hide it. A ride this week's card has an open
    #: **proposal** about is `unmatched` while that card already carries it —
    #: a status filter would list it twice over. The card's
    #: `matched_session_id` is the join for planned work, and repeating that
    #: ride here would make one session read as two.
    #:
    #: Each entry keeps its own `match_status`, rather than reducing to a
    #: "nothing was planned" flag: a ride nothing could be matched to resolves
    #: to `unplanned` on the spot, a ride carried by an adjacent week's card
    #: reads `matched`, and only the status tells the agent which of those it
    #: is looking at.
    #:
    #: Truncated by :data:`MAX_WEEK_COMPLETED` with the totals, so
    #: `completed_session_count` can exceed what this lists — the count is the
    #: true one and the list is what a bounded read could carry.
    #:
    #: **Rendered by the MCP adapter only**; `PlanWeekRead` deliberately does
    #: not name it, so the REST contract is unchanged. The browser reaches
    #: these rows through the session list its week strip already links to; the
    #: agent is the caller with no cheap join, and giving it a second way to
    #: read the same rows over HTTP would be two contracts for one fact.
    unplanned_sessions: tuple[CompletedSession, ...]


class PlanService:
    """Read-projections over the plan. Raises AppError subclasses."""

    def __init__(
        self,
        session: AsyncSession,
        sessions: PlannedSessionRepository,
        workouts: WorkoutRepository,
        anchors: AnchorService,
        completed: SessionRepository,
        metrics: SessionMetricsService,
        matches: SessionMatchRepository,
        scores: SessionScoreRepository,
        declarations: VerdictDeclarationRepository,
    ) -> None:
        self._session = session
        self._sessions = sessions
        self._workouts = workouts
        self._anchors = anchors
        self._completed = completed
        self._metrics = metrics
        self._matches = matches
        self._scores = scores
        self._declarations = declarations

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(
            session,
            PlannedSessionRepository(session),
            WorkoutRepository(session),
            AnchorService.from_session(session),
            SessionRepository(session),
            SessionMetricsService.from_session(session),
            SessionMatchRepository(session),
            SessionScoreRepository(session),
            VerdictDeclarationRepository(session),
        )

    async def week(self, start: dt.date | None = None) -> PlanWeek:
        """Project the seven days beginning at ``start``.

        ``start`` is taken literally — a Wednesday start gives the seven days
        from Wednesday — so a client can page the calendar by a day if it
        wants to. Omitted, it defaults to the Monday of the current week,
        computed in **UTC** (:func:`_today`). That is right for the
        browser client, which always sends an explicit `start=` derived from
        the athlete's own clock; a WP-8 MCP caller that passes nothing gets
        the UTC Monday, which is the wrong week for a few hours either side of
        midnight in a distant timezone. The athlete-local answer arrives when
        WP-4 gives the athlete a timezone — there is nothing to be local *to*
        until then, and guessing from the server's clock would be a second
        wrong answer rather than a better one.

        Predicted load is computed here, on read, from each intent's frozen
        prescription and the anchor versions it pinned — never stored, exactly
        like the durations beside it. The pins for the whole week are loaded
        in **one** query, so a busy week costs the same round-trips as an
        empty one.

        Every total on the projection reports its own coverage, and a session
        past :data:`MAX_WEEK_SESSIONS` counts as uncounted on both — the cap
        truncates what is rendered, and a truncated week must not claim its
        totals are whole. :data:`MAX_WEEK_COMPLETED` works the same way on the
        recorded side: the count is the true one, and what the cap left behind
        is uncounted against the load and the polarization pairs.

        Raises:
            ValueError: When **any** session in the window has a stored
                prescription that no longer parses. One bad row therefore
                fails the whole week rather than one card. That is inherited
                from the single-session read, where the policy is loud on
                purpose and the blast radius is one session; here the radius
                is the calendar, which is the deliberate trade: a week that
                silently dropped a session would be a plan with a hole in it,
                and the hole is exactly what an athlete would not notice. If
                this ever fires in anger the remedy is the stored document,
                not a per-card ``try``.
        """
        first = start if start is not None else week_start(_today())
        dates = week_dates(first)
        rows, total = await self._sessions.list(
            start=first, end=dates[-1], limit=MAX_WEEK_SESSIONS
        )
        # What the cap left behind. Counted in the session total and against
        # both coverage pairs: the rows are unread, so nothing can be said
        # about their duration or their load except that it is missing.
        overflow = max(total - len(rows), 0)
        titles = await self._workouts.names(
            [
                workout_id
                for row in rows
                if (workout_id := row.current_intent.workout_id) is not None
            ]
        )
        pins = {
            row.id: parse_pins(row.current_intent.pinned_anchor_versions)
            for row in rows
        }
        versions = await self._anchors.by_ids(
            version_id
            for session_pins in pins.values()
            for version_id in session_pins.values()
        )
        links = await self._matches.for_planned_sessions([row.id for row in rows])
        verdicts = await self._verdicts(links)
        cards = [
            _card(
                row,
                titles,
                resolve_pins(pins[row.id], versions),
                links.get(row.id),
                verdicts.get(row.id),
            )
            for row in rows
        ]
        done, completed_total = await self._completed_sessions(first, dates[-1])
        # The recordings the cap left behind, counted the way the planned
        # side counts its own: in the session total, and as uncounted against
        # every coverage pair, because an unread row can contribute neither a
        # load nor a band.
        completed_overflow = max(completed_total - len(done), 0)
        claimed = {
            card.matched_session_id
            for card in cards
            if card.matched_session_id is not None
        }
        loads = [
            card.predicted_load for card in cards if card.predicted_load is not None
        ]
        durations = [
            card.planned_duration_s
            for card in cards
            if card.planned_duration_s is not None
        ]
        done_loads = [entry.load for entry in done if entry.load is not None]
        banded = [entry for entry in done if entry.easy_s is not None]
        index = polarization_index(
            sum(entry.easy_s or 0.0 for entry in banded),
            sum(entry.moderate_s or 0.0 for entry in banded),
            sum(entry.hard_s or 0.0 for entry in banded),
        )
        return PlanWeek(
            start=first,
            end=dates[-1],
            days=tuple(
                _day(day, cards, [entry for entry in done if entry.date == day])
                for day in dates
            ),
            session_count=total,
            planned_duration_s=sum(durations) if durations else None,
            duration_sessions_counted=len(durations),
            duration_sessions_uncounted=len(cards) - len(durations) + overflow,
            planned_load=sum(loads) if loads else None,
            load_sessions_counted=len(loads),
            load_sessions_uncounted=len(cards) - len(loads) + overflow,
            completed_session_count=completed_total,
            completed_duration_s=(
                sum(entry.duration_s for entry in done) if done else None
            ),
            completed_load=sum(done_loads) if done_loads else None,
            completed_load_sessions_counted=len(done_loads),
            completed_load_sessions_uncounted=(
                len(done) - len(done_loads) + completed_overflow
            ),
            completed_polarization_index=(
                index.value if isinstance(index, Measured) else None
            ),
            completed_polarization_not_assessed=(
                None if isinstance(index, Measured) else index.reason
            ),
            completed_polarization_rule=ONE_CHANNEL_PER_SESSION_RULE,
            completed_polarization_sessions_counted=len(banded),
            completed_polarization_sessions_uncounted=(
                len(done) - len(banded) + completed_overflow
            ),
            by_discipline=_by_discipline(cards, done),
            unplanned_sessions=tuple(
                entry for entry in done if entry.id not in claimed
            ),
        )

    async def _verdicts(
        self, links: Mapping[uuid.UUID, SessionMatchRow]
    ) -> dict[uuid.UUID, Verdict | None]:
        """The verdict standing on each linked planned session, in two queries.

        **The athlete's declaration wins wherever there is one.** The machine's
        suggestion is what the strip shows until then, and a calendar that kept
        showing the suggestion after the athlete had overruled it would be
        arguing with them once a week.

        A score is only read for the plan entry it was **computed against**.
        Both sides are already keyed by the link, so the only way the two can
        disagree is a swap whose rescore did not land — and showing the old
        prescription's verdict on the new one's card would be the strip
        answering a question nobody asked of it.
        """
        session_ids = [link.session_id for link in links.values()]
        declared = await self._declarations.for_sessions(session_ids)
        scored = await self._scores.current_for_sessions(session_ids)
        resolved: dict[uuid.UUID, Verdict | None] = {}
        for planned_id, link in links.items():
            declaration = declared.get(link.session_id)
            if declaration is not None:
                resolved[planned_id] = declaration.declared_verdict
                continue
            score = scored.get(link.session_id)
            resolved[planned_id] = (
                score.suggested_verdict
                if score is not None and score.planned_session_id == planned_id
                else None
            )
        return resolved

    async def _completed_sessions(
        self, start: dt.date, end: dt.date
    ) -> tuple[list[CompletedSession], int]:
        """The recorded sessions dated in the window, and how many there are.

        One query for the sessions and one for every current artefact behind
        them, so a busy week costs the same round trips as an empty one. A
        session with no artefact is **kept** — it happened, it has a duration,
        and it counts as uncounted against the load total rather than
        vanishing from the week.

        The count is the repository's total and not ``len()`` of the rows: it
        survives :data:`MAX_WEEK_COMPLETED`, so a truncated window still says
        how many sessions it holds — the same contract the planned side's
        `session_count` has always had, and what keeps the count honest
        against the ids `unplanned_sessions` could carry.
        """
        rows, total = await self._completed.list(
            start=start, end=end, limit=MAX_WEEK_COMPLETED
        )
        current = await self._metrics.current_for_sessions(row.id for row in rows)
        completed: list[CompletedSession] = []
        for row in rows:
            artefact = current.get(row.id)
            summary = summarise(artefact) if artefact is not None else None
            completed.append(
                CompletedSession(
                    id=row.id,
                    date=row.local_date,
                    discipline=as_planned_discipline(row.discipline),
                    duration_s=_completed_duration(row),
                    load=summary.training_load if summary is not None else None,
                    easy_s=summary.easy_s if summary is not None else None,
                    moderate_s=summary.moderate_s if summary is not None else None,
                    hard_s=summary.hard_s if summary is not None else None,
                    match_status=row.status,
                )
            )
        return completed, total


def _completed_duration(row: SessionRow) -> float:
    """How long a recorded session lasted, the way the session list says.

    Recording time for a device session — elapsed with the pauses removed
    (A4.4), which is also the duration its load was computed over — and the
    wall-clock duration for one typed in, which has no pauses to remove. The
    week and the log must not answer this differently, which is why both go
    through the one function that answers it.
    """
    return session_duration_s(row)


def _day(
    day: dt.date,
    cards: Sequence[WeekSession],
    done: Sequence[CompletedSession],
) -> WeekDay:
    """One day of the grid: what was planned for it and what was recorded."""
    loads = [entry.load for entry in done if entry.load is not None]
    planned = tuple(card for card in cards if card.date == day)
    states = [card.completion_state for card in planned]
    if any(entry.match_status is SessionMatchStatus.UNPLANNED for entry in done):
        states.append(CompletionState.UNPLANNED)
    return WeekDay(
        date=day,
        sessions=planned,
        completed_session_count=len(done),
        completed_duration_s=(
            sum(entry.duration_s for entry in done) if done else None
        ),
        completed_load=sum(loads) if loads else None,
        completion_state=worst_state(states),
    )


def _today() -> dt.date:
    """The current date, in UTC.

    The athlete's own timezone is not modelled until WP-4 puts one on each
    recorded session, so UTC is the calendar the whole application already
    agrees on. Isolated here so that work package has one line to
    change.
    """
    return dt.datetime.now(dt.UTC).date()


def _card(
    row: PlannedSessionRow,
    titles: dict[uuid.UUID, str],
    anchors: Mapping[AnchorType, PinnedAnchor],
    link: SessionMatchRow | None = None,
    verdict: Verdict | None = None,
) -> WeekSession:
    """Project one stored session onto its calendar card.

    Raises:
        ValueError: When the stored prescription no longer parses — loud on
            purpose, exactly as when one is read back individually.
    """
    intent = row.current_intent
    body = workout_body_from_json(intent.structure)
    summary = WorkoutSummary(body)
    load, factor, coverage, volume = _predict(body, anchors)
    return WeekSession(
        id=row.id,
        date=row.date,
        discipline=row.discipline,
        purpose=intent.purpose,
        status=row.status,
        title=titles.get(intent.workout_id) if intent.workout_id else None,
        workout_id=intent.workout_id,
        planned_duration_s=summary.total_duration_s,
        total_sets=summary.total_sets,
        step_count=summary.step_count,
        intent_text=intent.intent_text,
        intent_version=intent.version,
        predicted_load=load,
        predicted_intensity_factor=factor,
        predicted_load_coverage=coverage,
        predicted_volume_load_kg=volume,
        matched_session_id=link.session_id if link is not None else None,
        match_status=link.status if link is not None else None,
        completion_state=completion_state(row.status, verdict),
    )


def _predict(
    body: WorkoutBody, anchors: Mapping[AnchorType, PinnedAnchor]
) -> tuple[float | None, float | None, float | None, float | None]:
    """Return ``(load, intensity_factor, coverage, volume_load_kg)``.

    Exactly one of the two axes is ever populated: a strength prescription has
    kilograms and no TSS, an endurance one has TSS and no kilograms. The
    split is the point — see `app.domain.prediction`.

    The coverage travels with the load rather than being recomputed anywhere:
    it comes off the same `PredictedLoad` the session resource renders, so a
    card and its sheet cannot disagree about how much of the ride the number
    was integrated over.
    """
    if isinstance(body, StrengthWorkout):
        return None, None, None, predict_strength_volume(body).volume_load_kg
    predicted = predict_endurance_load(body, anchors)
    if predicted is None:
        return None, None, None, None
    return predicted.load, predicted.intensity_factor, predicted.coverage, None


def _by_discipline(
    cards: Sequence[WeekSession], done: Sequence[CompletedSession]
) -> tuple[PlanWeekDiscipline, ...]:
    """Total the week per discipline, skipping disciplines with no session.

    Every total here is the same fold as its flat counterpart on
    :class:`PlanWeek`, over a subset of the same cards, so the rows reconcile
    with the week's own numbers by construction rather than by agreement. The
    one thing the rows cannot carry is the `MAX_WEEK_SESSIONS` overflow: an
    unread row has no discipline to be attributed to, so the truncation shows
    up only in the week's own coverage pairs.
    """
    rows: list[PlanWeekDiscipline] = []
    for discipline in Discipline:
        group = [card for card in cards if card.discipline is discipline]
        recorded = [entry for entry in done if entry.discipline is discipline]
        if not group and not recorded:
            continue
        loads = [
            card.predicted_load for card in group if card.predicted_load is not None
        ]
        durations = [
            card.planned_duration_s
            for card in group
            if card.planned_duration_s is not None
        ]
        sets = [card.total_sets for card in group if card.total_sets is not None]
        rows.append(
            PlanWeekDiscipline(
                discipline=discipline,
                session_count=len(group),
                planned_duration_s=sum(durations) if durations else None,
                duration_sessions_counted=len(durations),
                duration_sessions_uncounted=len(group) - len(durations),
                planned_load=sum(loads) if loads else None,
                load_sessions_counted=len(loads),
                load_sessions_uncounted=len(group) - len(loads),
                total_sets=sum(sets) if sets else None,
                completed_session_count=len(recorded),
                completed_duration_s=(
                    sum(entry.duration_s for entry in recorded) if recorded else None
                ),
                completed_load=(
                    sum(done_loads)
                    if (
                        done_loads := [
                            entry.load for entry in recorded if entry.load is not None
                        ]
                    )
                    else None
                ),
                completed_load_sessions_counted=len(
                    [entry for entry in recorded if entry.load is not None]
                ),
                completed_load_sessions_uncounted=len(
                    [entry for entry in recorded if entry.load is None]
                ),
            )
        )
    return tuple(rows)


#: Re-exported so an adapter can state the window it renders without reaching
#: into the domain for the constant.
__all__ = [
    "MAX_WEEK_COMPLETED",
    "MAX_WEEK_SESSIONS",
    "WEEK_DAYS",
    "CompletedSession",
    "PlanService",
    "PlanWeek",
    "PlanWeekDiscipline",
    "WeekDay",
    "WeekSession",
]

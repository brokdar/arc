"""The coaching agent's tool surface (WP-8.1).

Registered onto the server by `app.mcp.main.create_server`. Every tool here is
the same three lines of adapter and no more:

1. ``actor = require_scope(...)`` — the key's scope, checked per tool;
2. ``async with session_scope() as session:`` — the non-HTTP way to a session
   (`app.persistence.db`), which the service commits and this never does;
3. one service call, and a projection from `app.mcp.views`.

**No logic lives here.** Every rule the agent meets — the write cap, the red
flag, append-only anchors, one-change-per-session, who may write a note — is
in `app.services`, because an adapter can be bypassed and a guardrail that
only exists in one adapter is not a guardrail (see
`app.services.guardrails`). What this module adds is *wording*: a refusal
reaches the model as a sentence it can act on rather than a status code, which
is what :func:`tool_errors` is for.

**The docstrings are the API.** They are what the model reads before deciding
whether to call, so they state units, formats, what is refused and why. A
docstring here that describes the implementation instead of the contract is a
bug in the tool.

**Conventions across the surface.** Ids and dates are strings — a uuid as
``"0198..."``, a date as ``"2026-08-10"``, a moment as an ISO datetime *with a
timezone offset* — and a malformed one is refused by name. Durations are
seconds, cycling load is TSS-equivalent, strength volume is kilograms, and
power is watts. Every read carries ``red_flag``; every write takes
``dry_run``.
"""

import datetime as dt
from collections import Counter
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from app.core.exceptions import (
    AppError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    RateLimitedError,
    RedFlagError,
    UnauthorizedError,
    ValidationError,
)
from app.core.logging import get_logger
from app.domain.activity import SessionDiscipline
from app.domain.agent_notes import NoteKind
from app.domain.anchors import (
    RESERVED_ANCHOR_TYPES,
    AnchorSource,
    AnchorType,
    AnchorUnit,
    Provenance,
)
from app.domain.athlete import Discipline
from app.domain.proposals import ProposalStatus, changes_from_json
from app.domain.strength import ExerciseCategory
from app.domain.templates import sorted_templates
from app.domain.wellness import (
    WRITABLE_FIELDS,
    HrvContext,
    HrvMetric,
    WellnessSource,
)
from app.domain.zones import DEFAULT_ZONE_MODEL
from app.ingest.repricing import append_anchor_and_reprice, preview_anchor_append
from app.mcp import views
from app.mcp.auth import Scope
from app.mcp.identity import require_scope
from app.persistence.db import session_scope
from app.services.activity import LoggedSetInput, SessionService
from app.services.agent_notes import AgentNoteService
from app.services.anchors import AnchorService
from app.services.exercises import ExerciseService
from app.services.guardrails import current_profile, remaining_write_budget
from app.services.history import HistoryService
from app.services.matching import MatchingService
from app.services.metrics import SessionMetricsService, summarise
from app.services.plan import PlanService
from app.services.proposals import ProposalService
from app.services.scoring import ScoringService
from app.services.templates import purpose_templates
from app.services.wellness import (
    DayInput,
    WellnessService,
    parse_confounders,
    parse_soreness_by_region,
)
from app.services.workouts import WorkoutService, step_count_of
from app.services.zones import ZoneService

logger = get_logger(__name__)

#: Most rows any one read tool will return. A model that asks for everything
#: gets a page and a total, so it can see that there was more.
MAX_LIMIT = 200

#: How many recorded sessions `get_coaching_context` carries — enough to see
#: the week that was and the shape of the one before it, small enough that
#: the one opening call stays one-call sized. Older ones are `list_sessions`.
RECENT_SESSIONS = 7

#: How an `AppError` is labelled on its way out, most specific first —
#: `RedFlagError` is a `ForbiddenError`, and the order is what keeps it from
#: being reported as the generic one.
_KINDS: tuple[tuple[type[AppError], str], ...] = (
    (RedFlagError, "red_flag"),
    (RateLimitedError, "rate_limited"),
    (ConflictError, "conflict"),
    (NotFoundError, "not_found"),
    (ValidationError, "invalid"),
    (UnauthorizedError, "unauthorized"),
    (ForbiddenError, "forbidden"),
)


def _kind(exc: AppError) -> str:
    """The label for one application error."""
    for cls, label in _KINDS:
        if isinstance(exc, cls):
            return label
    return "error"


@contextmanager
def tool_errors() -> Iterator[None]:
    """Turn service refusals into `ToolError`s the model can act on.

    Three cases, and the difference between them matters:

    * an **`AppError`** is a refusal the services *meant* — a stale
      concurrency token, a spent write cap, the red flag — and its ``detail``
      is written to be read by whoever tripped it. It is passed through whole,
      behind a short label (``conflict:``, ``rate_limited:``, ``red_flag:``),
      so the model can both branch on the kind and read the reason. Losing
      that text would leave the agent retrying a thing that will never work.
    * a **`ValueError`** is a malformed argument, caught from the parsers in
      `app.mcp.views` and from pure domain code; it names the argument.
    * **anything else** is a bug, and the model is told only that. A traceback
      through an MCP tool result is an information leak with an audience of
      one, and the useful copy of it is in the server's log.
    """
    try:
        yield
    except ToolError:
        raise
    except AppError as exc:
        raise ToolError(f"{_kind(exc)}: {exc.detail}") from exc
    except ValueError as exc:
        raise ToolError(f"invalid: {exc}") from exc
    except Exception as exc:
        logger.exception("mcp_tool_failed")
        raise ToolError(
            "The server failed after this call was validated. It has been "
            "logged. If this was a write, re-read before retrying: the write "
            "may have landed and the failure be in rendering the answer."
        ) from exc


def _limit(limit: int) -> int:
    """Clamp a caller-supplied page size into what the surface will serve."""
    return max(1, min(limit, MAX_LIMIT))


def _offset(offset: int) -> int:
    """Check a caller-supplied page offset.

    Clamped nowhere and refused instead: a negative offset means the caller is
    paging with arithmetic that has gone wrong, and silently serving them page
    one would hide it behind rows they have already seen.

    Raises:
        ValueError: When it is negative. Named, like every other malformed
            argument on this surface.
    """
    if offset < 0:
        raise ValueError(f"offset must be zero or more, got {offset}")
    return offset


#: The fields one set object of `record_manual_session` may carry — the same
#: vocabulary the service's set input takes, checked here so a misspelled
#: field is refused by name rather than silently dropped.
_SET_FIELDS = frozenset(
    {
        "exercise_id",
        "exercise_name",
        "reps",
        "duration_s",
        "per_side",
        "load_kg",
        "rir",
        "notes",
    }
)


#: How many days of wellness `get_coaching_context` carries — the same seven
#: `RECENT_SESSIONS` carries, so the two blocks describe the same stretch of
#: time and a coach can read one against the other without a second call.
RECENT_WELLNESS_DAYS = 7

#: Fields on a wellness day that are parsed rather than passed through.
_WELLNESS_TIMES = ("sleep_start_local", "sleep_end_local")


def _wellness_updates(payload: Mapping[str, Any], *, where: str) -> dict[str, Any]:
    """Parse one day's fields, refusing an unknown one **by name**.

    Every vocabulary this touches is enumerated in the refusal — confounders,
    body regions, the field list itself — because an error that does not say
    what *is* legal costs the agent a round trip it should never have paid for
    (the #19 lesson), and `get_wellness_inputs` exists so it never has to guess
    in the first place.

    Raises:
        ValueError: Naming the day and what is wrong with it.
    """
    unknown = sorted(set(payload) - set(WRITABLE_FIELDS))
    if unknown:
        raise ValueError(
            f"{where}unknown field(s): {', '.join(unknown)}. A day carries "
            f"{', '.join(WRITABLE_FIELDS)} — call get_wellness_inputs for what "
            "each one means."
        )
    updates = dict(payload)
    for name in _WELLNESS_TIMES:
        if isinstance(updates.get(name), str):
            updates[name] = views.as_time(updates[name], field=f"{where}{name}")
    if updates.get("confounders") is not None:
        updates["confounders"] = parse_confounders(updates["confounders"])
    if updates.get("soreness_by_region") is not None:
        updates["soreness_by_region"] = parse_soreness_by_region(
            updates["soreness_by_region"]
        )
    for name, enum_class in (("hrv_metric", HrvMetric), ("hrv_context", HrvContext)):
        value = updates.get(name)
        if isinstance(value, str):
            try:
                updates[name] = enum_class(value)
            except ValueError as exc:
                raise ValueError(
                    f"{where}{name} must be one of "
                    f"{', '.join(member.value for member in enum_class)}, "
                    f"got {value!r}"
                ) from exc
    return updates


def _cleared(clear: list[str] | None, *, where: str = "") -> dict[str, Any]:
    """Turn a ``clear`` argument into the explicit nulls the service reads.

    A separate argument rather than an overloaded null, because an omitted
    field already means "leave it alone" and one word cannot mean both.
    `record_session_context` refused clearing outright and that was defensible
    for two fields; over eighteen, a typo'd HRV the agent cannot retract is a
    permanent lie in a baseline.

    Raises:
        ValueError: When a named field is not one a day carries.
    """
    unknown = sorted(set(clear or ()) - set(WRITABLE_FIELDS))
    if unknown:
        raise ValueError(
            f"{where}cannot clear unknown field(s): {', '.join(unknown)}. A day "
            f"carries {', '.join(WRITABLE_FIELDS)}."
        )
    return dict.fromkeys(clear or ())


def _logged_sets(entries: list[dict[str, Any]] | None) -> list[LoggedSetInput]:
    """Parse a tool call's set objects into the service's input values.

    Field-name and type errors are caught here, in words that name the set
    and the field: the service validates *values* (bounds, catalogue slugs),
    but a key it does not know would otherwise be dropped on the floor and a
    string where a number belongs would be a 500.

    Raises:
        ValueError: Naming the first malformed set and what is wrong with it.
    """
    inputs: list[LoggedSetInput] = []
    for index, entry in enumerate(entries or [], start=1):
        unknown = sorted(set(entry) - _SET_FIELDS)
        if unknown:
            raise ValueError(
                f"set {index} has unknown field(s): {', '.join(unknown)}. A "
                f"set carries {', '.join(sorted(_SET_FIELDS))}."
            )
        reps = entry.get("reps")
        if reps is not None and (not isinstance(reps, int) or isinstance(reps, bool)):
            raise ValueError(f"set {index}: reps must be an integer, got {reps!r}")
        duration_s = entry.get("duration_s")
        if duration_s is not None and (
            not isinstance(duration_s, int) or isinstance(duration_s, bool)
        ):
            raise ValueError(
                f"set {index}: duration_s must be a whole number of seconds, "
                f"got {duration_s!r}"
            )
        per_side = entry.get("per_side", False)
        if not isinstance(per_side, bool):
            raise ValueError(
                f"set {index}: per_side must be true or false, got {per_side!r}"
            )
        load_kg = entry.get("load_kg")
        if load_kg is not None and (
            not isinstance(load_kg, int | float) or isinstance(load_kg, bool)
        ):
            raise ValueError(
                f"set {index}: load_kg must be a number of kilograms, got {load_kg!r}"
            )
        rir = entry.get("rir")
        if rir is not None and (not isinstance(rir, int) or isinstance(rir, bool)):
            raise ValueError(f"set {index}: rir must be an integer, got {rir!r}")
        inputs.append(
            LoggedSetInput(
                reps=reps,
                duration_s=duration_s,
                per_side=per_side,
                exercise_id=entry.get("exercise_id"),
                exercise_name=entry.get("exercise_name"),
                load_kg=None if load_kg is None else float(load_kg),
                rir=rir,
                notes=entry.get("notes"),
            )
        )
    return inputs


def register_tools(mcp: FastMCP) -> None:  # noqa: C901 — one function per tool
    """Register the coaching agent's tools on ``mcp``.

    A free function taking the server rather than a body inside
    `create_server`, so the tool surface can grow without the entrypoint
    growing with it — and so a test can build a server and get the real tools.
    """

    @mcp.tool
    async def ping() -> dict[str, str]:
        """Liveness check — returns a fixed payload if the server is reachable.

        Needs no scope and touches no data; it exists to prove transport and
        auth are wired.
        """
        return {"status": "ok", "service": mcp.name}

    # --- reads ----------------------------------------------------------------

    @mcp.tool
    async def get_coaching_context() -> dict[str, Any]:
        """Start every coaching session with this one call.

        It replaces the `get_athlete` → `get_anchors` → `get_plan_week` →
        `list_sessions` → `list_proposals` opening: who you are coaching,
        the numbers in force, the week they are in, what they just did and
        what you last suggested — one answer, assembled from the same
        services those tools read, so it never disagrees with them.

        What each block is, and where the rest lives:

        * `athlete` — the profile, as `get_athlete` returns it. `plan_state`
          is `active` or `paused` — while paused the athlete has stopped
          training and nothing is being scored, so suggesting a busier week
          answers a question nobody asked.
        * `red_flag` — illness or injury. While `active`, any proposal that
          **adds or intensifies** training is refused (see
          `propose_plan_change`). It is on every read from this server, so
          there is never a call where you did not know.
        * `anchors` — the version of each anchor **currently in force**,
          keyed by type; null where none has been appended yet, which is an
          answer (there is no FTP), not a gap. The append-only histories are
          `get_anchors`.
        * `week` — the current plan week, exactly as `get_plan_week` returns
          it, concurrency tokens included. Other weeks are `get_plan_week`
          with a `start`.
        * `agent_notes` — **what you have already said about this week**: the
          annotations filed under this Monday, oldest first, with the
          athlete's `dispute` on each. Here for the reason `prompt` is: a
          coach that has to fetch its own standing opinion separately is a
          coach that will one day not fetch it, and will repeat itself — or
          contradict itself — permanently and under its own `model_id`. Other
          weeks come with `get_plan_week`; what was said about one *session*
          is on `get_session_detail`.
        * `open_proposals` — `pending` proposals only, the summary rows
          `list_proposals` serves. Resolved ones are `list_proposals`; the
          stored diff is `get_proposal`.
        * `recent_sessions` — the last 7 recorded sessions, the summary rows
          `list_sessions` serves. Older ones are `list_sessions`; one
          session's axes, verdict and alignment are `get_session_detail`.
        * `wellness` — what the athlete has reported about their own state.
          `today` is the day in full when they have answered and null when
          they have not (which is a fact, not a gap); `recent` is the last 7
          days cut down to the handful of inputs the morning question turns on,
          with a field the athlete did not report simply **absent**;
          `weight_in_force` is the weight watts per kilogram is computed
          against. Whole days are `get_wellness`, weekly shape is
          `get_wellness_weeks`.

          `prompt` is the standing of the day's own question — `pending` while
          the athlete still has time to answer, `answered` once they have, and
          `expired` when the window closed with no answer. **Null means nobody
          was asked yet**, which is not the same as an unanswered day: an
          `expired` prompt beside a missing day is a morning that went
          unreported, and a null one is a morning the application never put the
          question. Read it before treating an absent day as a signal.

          Two flags decide how to read any of it. `not_actionable` on a day
          names the confounders the athlete declared that void that morning's
          objective markers — the numbers are real and they are still here,
          but they are not evidence about today. `subjective_recalled` means
          the *felt* ratings were entered late enough to be memory; the device
          numbers are never discounted for it.

          A compact day carries `source` and `provenance` **only when they are
          not `athlete` and `athlete_reported`** — so a day *you* wrote down is
          marked and a day the athlete entered themselves is not. Absence there
          means "the athlete's own first-hand report", and `get_wellness`
          spells both out on every day if you would rather read them stated.

          **Nothing here is a readiness verdict, and you should not treat it as
          one.** Arc stores what the athlete said and describes it; whether
          today is a day to train is your call, made out loud, with the
          confounders and the gaps visible.
        * `budget_remaining` — the hourly write cap's current standing: how
          many writes it still admits.

        Requires a `read` key.
        """
        actor = require_scope(Scope.READ)
        with tool_errors():
            async with session_scope() as session:
                profile = await current_profile(session)
                anchor_service = AnchorService.from_session(session)
                anchors: dict[str, Any] = {}
                for anchor_type in AnchorType:
                    if anchor_type in RESERVED_ANCHOR_TYPES:
                        continue
                    try:
                        row = await anchor_service.current(anchor_type)
                    except NotFoundError:
                        # "No version in force yet" is a fact about the
                        # record, not a failed call — the same answer-shaped
                        # absence `get_zones` serves.
                        anchors[anchor_type.value] = None
                    else:
                        anchors[anchor_type.value] = views.anchor(row)
                week = await PlanService.from_session(session).week()
                pending, _ = await ProposalService.from_session(session).list(
                    status=ProposalStatus.PENDING, limit=MAX_LIMIT
                )
                recent, _ = await SessionService.from_session(session).list(
                    limit=RECENT_SESSIONS
                )
                current = await SessionMetricsService.from_session(
                    session
                ).current_for_sessions(row.id for row in recent)
                wellness_service = WellnessService.from_session(session)
                today = wellness_service.local_today()
                # The last seven days including today, half-open like every
                # other range here. Read as one page rather than day by day:
                # this is the one call every session begins with.
                wellness_page = await wellness_service.range(
                    start=today - dt.timedelta(days=RECENT_WELLNESS_DAYS - 1),
                    end=today + dt.timedelta(days=1),
                    limit=RECENT_WELLNESS_DAYS,
                )
                today_row = next(
                    (row for row in wellness_page.days if row.local_date == today),
                    None,
                )
                return {
                    "athlete": views.athlete(profile, today=dt.date.today()),
                    "red_flag": views.red_flag(profile),
                    "wellness": {
                        "today": (
                            None
                            if today_row is None
                            else views.wellness_day(
                                today_row,
                                subjective_recalled=wellness_service.is_recalled(
                                    today_row
                                ),
                            )
                        ),
                        "recent": [
                            views.wellness_day_compact(
                                row,
                                subjective_recalled=wellness_service.is_recalled(row),
                            )
                            for row in wellness_page.days
                        ],
                        "weight_in_force": views.weight_in_force(
                            await wellness_service.weight_in_force(today)
                        ),
                        # The day's standing question, on the opener rather
                        # than behind a second call: a coach that has to fetch
                        # "was the athlete asked?" separately is a coach that
                        # will one day not fetch it, and will read an empty
                        # morning as an athlete who felt fine.
                        "prompt": views.wellness_prompt(
                            await wellness_service.prompt()
                        ),
                        # The projection, not the whole trend: the opener says
                        # how many markers are off their own normal and which,
                        # and `get_wellness_trend` has the series behind it.
                        # Computed here because the alternative is the coach
                        # reconstructing this athlete's normal from memory
                        # every morning, which is the guessing this surface
                        # exists to end.
                        "readiness": views.wellness_readiness(
                            (
                                await wellness_service.trend(
                                    start=today,
                                    end=today + dt.timedelta(days=1),
                                    metrics=[],
                                )
                            ).readiness
                        ),
                    },
                    "anchors": anchors,
                    "week": views.plan_week(week),
                    # The opener passes no `start`, so `week.start` is always
                    # the resolved Monday — the null `get_plan_week` can
                    # answer with is unreachable here by construction.
                    "agent_notes": [
                        views.note(row)
                        for row in await AgentNoteService.from_session(session).list(
                            plan_week=week.start
                        )
                    ],
                    "open_proposals": [views.proposal(row) for row in pending],
                    "recent_sessions": [
                        views.session_summary(
                            row,
                            summarise(current[row.id]) if row.id in current else None,
                        )
                        for row in recent
                    ],
                    "budget_remaining": await remaining_write_budget(session, actor),
                }

    @mcp.tool
    async def get_athlete() -> dict[str, Any]:
        """Read the athlete's profile, plan state and illness/injury flag.

        The session opener is `get_coaching_context`, which carries this
        whole answer; call this when the profile alone is the question.
        `plan_state` is `active` or `paused` — while paused the athlete has
        stopped training and nothing is being scored, so suggesting a busier
        week answers a question nobody asked.

        `red_flag.active` means illness or injury: while it is up, any
        proposal that **adds or intensifies** training is refused (see
        `propose_plan_change`). It is on every read from this server, so there
        is never a call where you did not know.

        Requires a `read` key.
        """
        require_scope(Scope.READ)
        with tool_errors():
            async with session_scope() as session:
                profile = await current_profile(session)
                return {
                    "athlete": views.athlete(profile, today=dt.date.today()),
                    "red_flag": views.red_flag(profile),
                }

    @mcp.tool
    async def get_anchors(
        anchor_type: AnchorType | None = None, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        """Read the athlete's physiological anchors, newest version first.

        Anchors (FTP in watts, LTHR / max HR / resting HR in bpm) are the
        numbers every prescription is scaled against, and their history is
        **append-only**: nothing here is ever edited or deleted, and
        `append_anchor` is the only write. A wrong value is corrected by
        appending a right one with a later `effective_date`, so the old
        prescriptions that were computed against it still make sense.

        Each version carries `provenance` — `tested`, `estimated`,
        `athlete_reported` or `assumed` — plus the `protocol` it was measured
        by and a confidence interval where one is known. Read those before
        trusting a number: an `assumed` FTP is a placeholder, not a
        measurement.

        Args:
            anchor_type: Restrict to one anchor (`ftp`, `lthr`, `max_hr`,
                `resting_hr`); omit for all of them.
            limit: How many versions to return, newest first.
            offset: How many to skip. With `total` in the answer, this is how
                you read past the first page.

        Requires a `read` key.
        """
        require_scope(Scope.READ)
        with tool_errors():
            async with session_scope() as session:
                rows, total = await AnchorService.from_session(session).list(
                    anchor_type=anchor_type,
                    offset=_offset(offset),
                    limit=_limit(limit),
                )
                return {
                    **views.page(
                        [views.anchor(row) for row in rows],
                        total=total,
                        limit=_limit(limit),
                        offset=_offset(offset),
                    ),
                    "red_flag": views.red_flag(await current_profile(session)),
                }

    @mcp.tool
    async def get_plan_week(start: str | None = None) -> dict[str, Any]:
        """Read one week of the committed plan, Monday to Sunday.

        This is **the plan**, not a proposal: it is what the athlete has
        agreed to and what their sessions are scored against. Each planned
        session carries an `intent_version` — that is the concurrency token
        `propose_plan_change` needs as `expected_intent_version`, so read the
        week before proposing changes to it and send back the versions you
        saw.

        Durations are seconds, cycling load is TSS-equivalent, strength volume
        is kilograms. A `planned_load` of null with sessions present means the
        week could not be priced, not that it is easy.

        `agent_notes` is what has been said **about this week** — the
        annotations `annotate` filed under this Monday, oldest first, with the
        athlete's `dispute` on each. Commentary about one *session* is not
        here even when that session falls inside the week: it is read on the
        session, with `get_session_detail`.

        It is **null**, not `[]`, when `start` is not a Monday. A plan week is
        keyed by the Monday it starts on, so a window beginning on any other
        day has no key to ask under — and answering with the overlapped
        Monday's notes would attach a different week's commentary to the
        window you asked for. Null means "there was nothing to ask"; `[]`
        means "asked, and nothing has been said".

        Args:
            start: The Monday to read, as `YYYY-MM-DD`. Taken literally, not
                snapped — pass a Monday. Defaults to the current week.

        Requires a `read` key.
        """
        require_scope(Scope.READ)
        with tool_errors():
            async with session_scope() as session:
                day = None if start is None else views.as_date(start, field="start")
                week = await PlanService.from_session(session).week(day)
                # `week.start` is the **resolved** start, so the default path
                # (`start=None`, which becomes the current Monday) asks under
                # a real key rather than falling into the null branch — the
                # branch answers one question about the window that was
                # actually served, not about the argument that was passed.
                notes = None
                if week.start.weekday() == 0:
                    notes = [
                        views.note(row)
                        for row in await AgentNoteService.from_session(session).list(
                            plan_week=week.start
                        )
                    ]
                return {
                    "week": views.plan_week(week),
                    "agent_notes": notes,
                    "red_flag": views.red_flag(await current_profile(session)),
                }

    @mcp.tool
    async def get_session_detail(session_id: str) -> dict[str, Any]:
        """Read everything computed about one recorded session.

        The full picture for a single session, assembled from four places:

        * `metrics` — what was measured: duration, training load and its
          basis, time in easy/moderate/hard zones (seconds), normalized power
          (watts), average heart rate.
        * `score` — how it went against what was planned: per-axis results
          with the individual criteria, plus the engine's `suggested_verdict`
          and the rule that produced it. Null when the recording is not linked
          to a planned session — an unplanned ride is not scored against
          anything.
        * `declaration` — **the athlete's own verdict**, and whether they
          `contested` the engine's suggestion. You may read this and you may
          not write it, ever: the verdict and its reasons are the athlete's
          word on their own training.
        * `match` — the link to a planned session, and how confident it is.
        * `wellness` — **what the athlete reported on this session's own day**,
          the same object `get_wellness` serves, or null when they reported
          nothing. This is here so that "does poor sleep predict poor execution
          for this athlete" is one read rather than two and a date match. Read
          `markers.actionable` before drawing anything from the numbers.
        * `weight_kg_in_force` — the body weight governing that date, so watts
          per kilogram is derivable. Null before the first weight was recorded,
          and w/kg is then absent rather than computed against a default.
        * `agent_notes` — **what has already been said about this session**,
          oldest first, with the athlete's `dispute` on each. Both kinds are
          here: the evaluations `write_session_evaluation` writes *and* the
          session-targeted annotations `annotate` writes, because they are
          two halves of one conversation about one ride and a coach reading
          only one half writes contradictions. Not to be confused with
          `notes`, one block up, which is the **athlete's own** text about
          their session — the two are never merged, or an opinion signed by a
          model becomes indistinguishable from the athlete's word.

          No author filter: a note written under another key is here too, with
          its own `created_by`. This block is the record of what has been said
          about this session, not one model's diary.

        Use this before writing an evaluation: an evaluation that contradicts
        the measured record is worse than none, and one that contradicts what
        you already said — permanently, under your own `model_id` — is worse
        again. `agent_notes` is where you find out.

        Args:
            session_id: The recorded session's id.

        Requires a `read` key.
        """
        require_scope(Scope.READ)
        with tool_errors():
            async with session_scope() as session:
                identifier = views.as_uuid(session_id, field="session_id")
                sessions = SessionService.from_session(session)
                metrics_service = SessionMetricsService.from_session(session)
                scoring = ScoringService.from_session(session)
                matching = MatchingService.from_session(session)
                wellness = WellnessService.from_session(session)
                notes = AgentNoteService.from_session(session)

                row = await sessions.get(identifier)
                day = (await wellness.days_for([row.local_date])).get(row.local_date)
                metrics_row = await metrics_service.get_current(identifier)
                summary = None if metrics_row is None else summarise(metrics_row)
                links = await matching.for_sessions([identifier])
                return {
                    "session": views.session_summary(row, summary),
                    "notes": row.notes,
                    "agent_notes": [
                        views.note(entry)
                        for entry in await notes.list(session_id=identifier)
                    ],
                    "metrics": views.metrics(summary, metrics_row),
                    "score": views.score(await scoring.get_current(identifier)),
                    "alignment": views.alignment(await scoring.alignment(identifier)),
                    "declaration": views.declaration(
                        await scoring.declaration(identifier)
                    ),
                    "match": views.match(links.get(identifier)),
                    "wellness": (
                        None
                        if day is None
                        else views.wellness_day(
                            day, subjective_recalled=wellness.is_recalled(day)
                        )
                    ),
                    "weight_kg_in_force": views.weight_in_force(
                        await wellness.weight_in_force(row.local_date)
                    ),
                    "red_flag": views.red_flag(await current_profile(session)),
                }

    @mcp.tool
    async def list_sessions(
        start: str | None = None,
        end: str | None = None,
        discipline: SessionDiscipline | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List recorded sessions, newest first.

        Summaries only — one line per session with its duration and training
        load. Call `get_session_detail` for the axes, the verdict and the
        alignment of any that look worth reading.

        Dates filter on the athlete-**local** day, which is the day they would
        say they trained on and the day the plan places work on.

        Args:
            start: Earliest local date, `YYYY-MM-DD`, inclusive.
            end: Latest local date, `YYYY-MM-DD`, inclusive.
            discipline: `cycling`, `strength` or `other`.
            limit: How many to return, newest first.
            offset: How many to skip. `total` says how many the filters
                matched, so `offset + limit < total` means there is more.

        Requires a `read` key.
        """
        require_scope(Scope.READ)
        with tool_errors():
            async with session_scope() as session:
                rows, total = await SessionService.from_session(session).list(
                    start=None
                    if start is None
                    else views.as_date(start, field="start"),
                    end=None if end is None else views.as_date(end, field="end"),
                    discipline=discipline,
                    offset=_offset(offset),
                    limit=_limit(limit),
                )
                metrics_service = SessionMetricsService.from_session(session)
                current = await metrics_service.current_for_sessions(
                    row.id for row in rows
                )
                items = [
                    views.session_summary(
                        row,
                        summarise(current[row.id]) if row.id in current else None,
                    )
                    for row in rows
                ]
                return {
                    **views.page(
                        items,
                        total=total,
                        limit=_limit(limit),
                        offset=_offset(offset),
                    ),
                    "red_flag": views.red_flag(await current_profile(session)),
                }

    @mcp.tool
    async def get_workout_library(
        query: str | None = None,
        folder: str | None = None,
        tag: str | None = None,
        discipline: Discipline | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Browse the athlete's saved workouts, newest first.

        These are reusable prescriptions a planned session can point at.
        Search here **before** writing a new one with `create_workout`: a
        library with four versions of the same 2x20 is worse than one with a
        single good one.

        The prescription document itself is not returned — only the name,
        folder, tags and step count; `get_workout` returns one workout with
        its full `structure`. To plan one, reference its `id` in a `create`
        change. A `step_count` of null means that workout's stored document
        no longer parses; the rest of the page is unaffected.

        Args:
            query: Free-text match on the name.
            folder: Restrict to one folder.
            tag: Restrict to one tag.
            discipline: `cycling` or `strength`.
            limit: How many to return.
            offset: How many to skip, for reading past the first page.

        Requires a `read` key.
        """
        require_scope(Scope.READ)
        with tool_errors():
            async with session_scope() as session:
                rows, total = await WorkoutService.from_session(session).list(
                    query=query,
                    folder=folder,
                    tag=tag,
                    discipline=discipline,
                    offset=_offset(offset),
                    limit=_limit(limit),
                )
                items = [
                    views.workout(row, step_count=step_count_of(row)) for row in rows
                ]
                return {
                    **views.page(
                        items,
                        total=total,
                        limit=_limit(limit),
                        offset=_offset(offset),
                    ),
                    "red_flag": views.red_flag(await current_profile(session)),
                }

    @mcp.tool
    async def search_history(start: str, end: str) -> dict[str, Any]:
        """Summarise recorded training over a date range, week by week.

        The tool for questions about *shape* — how much, in what, how it went
        — without pulling every session. Each Monday-to-Sunday week reports
        its session count, duration (seconds), training load and a tally of
        the athlete's **declared** verdicts, overall and per discipline.
        Weeks with nothing in them are included: a fortnight off is a fact
        about a block.

        A week the range only partly covers reports the **days it covers** as
        its `start` and `end` — ask from a Wednesday and the first week runs
        Wednesday to Sunday. Its totals are over those days alone, so a
        partial week is not a light week, and its bounds say which it is.

        `load` is summed over the sessions that could be priced;
        `load_sessions_uncounted` says how many could not, and a total with
        uncounted sessions behind it is a floor, not the answer.

        At most 371 days, and it refuses a range holding more than 800
        sessions rather than returning a summary that quietly omits some.

        Args:
            start: First local date, `YYYY-MM-DD`, inclusive.
            end: Last local date, `YYYY-MM-DD`, inclusive.

        Requires a `read` key.
        """
        require_scope(Scope.READ)
        with tool_errors():
            async with session_scope() as session:
                summary = await HistoryService.from_session(session).summarise(
                    start=views.as_date(start, field="start"),
                    end=views.as_date(end, field="end"),
                )
                return {
                    "history": views.history(summary),
                    "red_flag": views.red_flag(await current_profile(session)),
                }

    @mcp.tool
    async def list_proposals(
        status: ProposalStatus | None = None, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        """List plan-change proposals, newest first.

        Read this **before proposing** — a new proposal touching a session
        that already has an open one supersedes it, and this is where you see
        what you would displace — and at the **start of every weekly review**:
        it is what answers "was last week's proposal accepted, rejected, or
        did it lapse". `get_plan_week` cannot answer that; the plan only
        changes on acceptance, so an unanswered proposal is invisible there.

        A proposal is `pending` until the athlete accepts or rejects it; left
        unanswered it goes `lapsed` at its `expires_at`, a newer proposal
        about the same session makes it `superseded`, and the athlete
        training through the date it was about makes it
        `resolved_by_reality`. On every exit but acceptance the committed
        plan stands. `resolution_note` on a rejected one is the athlete's own
        words on why — read it; it is them telling you what you got wrong.

        Summaries only: status, timestamps, rationale and `change_count`.
        Call `get_proposal` for the stored diff.

        Args:
            status: Restrict to one status (`pending`, `accepted`,
                `rejected`, `lapsed`, `superseded`, `resolved_by_reality`);
                omit for all of them.
            limit: How many to return, newest first.
            offset: How many to skip, for reading past the first page.

        Requires a `read` key.
        """
        require_scope(Scope.READ)
        with tool_errors():
            async with session_scope() as session:
                rows, total = await ProposalService.from_session(session).list(
                    status=status,
                    offset=_offset(offset),
                    limit=_limit(limit),
                )
                return {
                    **views.page(
                        [views.proposal(row) for row in rows],
                        total=total,
                        limit=_limit(limit),
                        offset=_offset(offset),
                    ),
                    "red_flag": views.red_flag(await current_profile(session)),
                }

    @mcp.tool
    async def get_proposal(proposal_id: str) -> dict[str, Any]:
        """Read one proposal in full — rationale, status, and the stored diff.

        `diff` is the same document `propose_plan_change` returned when the
        proposal was written: per entity, before and after, computed against
        the plan as it stood then. For a `create` it shows the
        `success_criteria` the change's `purpose` resolved to — this is where
        you see what a session you proposed will be scored against.
        `supersedes_id` and `superseded_by_id` link the displacement chain in
        both directions, so a superseded proposal names its successor.

        Args:
            proposal_id: The proposal's id, from `list_proposals` or from
                `propose_plan_change`'s answer.

        Requires a `read` key.
        """
        require_scope(Scope.READ)
        with tool_errors():
            async with session_scope() as session:
                row = await ProposalService.from_session(session).get(
                    views.as_uuid(proposal_id, field="proposal_id")
                )
                return {
                    "proposal": views.proposal_detail(row),
                    "red_flag": views.red_flag(await current_profile(session)),
                }

    @mcp.tool
    async def get_workout(workout_id: str) -> dict[str, Any]:
        """Read one library workout **with its full prescription document**.

        This is the authoring reference: before writing a new structure with
        `create_workout`, read an existing workout of the same discipline
        here and follow its `structure` — the document is returned exactly as
        the validator accepted it, so its shape is a shape the validator will
        accept again. Durations are seconds, cycling targets are fractions of
        an anchor, strength loads are kilograms.

        Args:
            workout_id: The workout's id, from `get_workout_library` or a
                planned session's `workout_id`.

        Requires a `read` key.
        """
        require_scope(Scope.READ)
        with tool_errors():
            async with session_scope() as session:
                row = await WorkoutService.from_session(session).get(
                    views.as_uuid(workout_id, field="workout_id")
                )
                return {
                    "workout": views.workout_detail(row, step_count=step_count_of(row)),
                    "red_flag": views.red_flag(await current_profile(session)),
                }

    @mcp.tool
    async def get_exercise_catalogue(
        category: ExerciseCategory | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Browse the exercise catalogue strength prescriptions are written against.

        A strength structure names every movement by its **slug** — the `id`
        here, `back_squat` — and `create_workout` refuses a structure naming
        a slug that is not in this catalogue. So read this before authoring
        strength work: the slug you almost remember and the slug that exists
        are refused and accepted respectively.

        The catalogue is bundled reference data, identical on every
        deployment and read-only from everywhere: there is no tool that adds
        a movement, and a missing one is a change to the application, not a
        write.

        Args:
            category: Restrict to one movement family (`squat`, `hinge`,
                `lunge`, `press`, `pull`, `core`, `carry`, `mobility`,
                `conditioning`); omit for all of them.
            query: Case-insensitive substring of the name.
            limit: How many to return, by family then name.
            offset: How many to skip, for reading past the first page.

        Requires a `read` key.
        """
        require_scope(Scope.READ)
        with tool_errors():
            async with session_scope() as session:
                rows, total = await ExerciseService.from_session(session).list(
                    category=category,
                    query=query,
                    offset=_offset(offset),
                    limit=_limit(limit),
                )
                return {
                    **views.page(
                        [views.exercise(row) for row in rows],
                        total=total,
                        limit=_limit(limit),
                        offset=_offset(offset),
                    ),
                    "red_flag": views.red_flag(await current_profile(session)),
                }

    @mcp.tool
    async def get_zones() -> dict[str, Any]:
        """Read the training zones in force, per channel.

        Zones are **computed, never stored**: the `power` channel derives
        from the FTP version in force, the `hr` channel from the LTHR one,
        and both move the moment a new anchor version is appended. So never
        copy them anywhere — not into a prescription, a note, or your own
        notes-to-self. Write the anchor; read the zones.

        Each channel carries the anchor version and zone model it derives
        from — the provenance of every number in it. Bands are half-open
        `[lower, upper)` in the anchor's own unit, with `lower_pct` /
        `upper_pct` as fractions of the anchor value; the top zone has no
        ceiling. A channel whose anchor has no version in force is reported
        with null zones and a note, not a refusal — that the athlete has no
        LTHR yet is an answer, and it does not cost you the channel that
        exists.

        Requires a `read` key.
        """
        require_scope(Scope.READ)
        with tool_errors():
            async with session_scope() as session:
                service = ZoneService.from_session(session)
                channels = []
                for anchor_type in DEFAULT_ZONE_MODEL:
                    try:
                        resolved = await service.for_current_anchor(anchor_type)
                    except NotFoundError:
                        # "No version in force" is a fact about the record,
                        # not a failed call: rendered as an answer so the
                        # other channel still comes back.
                        channels.append(views.zones_unavailable(anchor_type))
                    else:
                        channels.append(views.zones(resolved))
                return {
                    "channels": channels,
                    "red_flag": views.red_flag(await current_profile(session)),
                }

    @mcp.tool
    async def get_purposes() -> dict[str, Any]:
        """Read the purpose vocabulary and what each purpose is judged on.

        Choosing a `purpose` — in a `create` change or a planned session —
        chooses the `default_criteria` the session starts with and the
        scoring axes it is judged on. Unless the plan overrides
        `success_criteria`, those defaults **are** what the session is scored
        against, so read this before proposing or creating: a purpose picked
        by its name alone is a scoring contract signed unread.

        The whole vocabulary is returned, unpaged — it is bundled reference
        data, one entry per purpose. `default_criteria` are in the same wire
        form a proposal diff carries, and they are a *default*: an `update`
        change may revise a session's `success_criteria` afterwards.

        Requires a `read` key.
        """
        require_scope(Scope.READ)
        with tool_errors():
            async with session_scope() as session:
                templates = sorted_templates(purpose_templates())
                return {
                    "items": [
                        views.purpose_template(template) for template in templates
                    ],
                    "red_flag": views.red_flag(await current_profile(session)),
                }

    @mcp.tool
    async def get_wellness_inputs() -> dict[str, Any]:
        """Read what a wellness day may carry, and what every value means.

        **Call this before your first `record_wellness`.** It is the whole
        vocabulary of the daily surface, so you never have to discover a
        confounder tag by submitting a guess and reading the refusal:

        * `tiers` — every writable field and how much it is worth asking for.
          `valuable` is what the morning question actually turns on; nothing is
          `required`, deliberately — a required daily input turns a missed
          morning into a failure state, and this is built for the real athlete
          rather than the compliant one.
        * `scales` — every subjective input with its range, its **polarity**
          and a descriptor for each point. Read the polarity: 5 motivation is
          good and 5 fatigue is not, and both are plausible numbers. Session
          RPE is in here too, on 0-10 with `higher_is_neither` — a 9 is a hard
          session, not a bad one, and it is a different scale from RIR.
        * `confounders` — the controlled vocabulary, each marked with whether
          it **invalidates the morning's markers**. Five of them do: the
          athlete's own pre-check, learned from a deload week once triggered by
          an alcohol artefact.
        * `body_regions` — the keys of `soreness_by_region`.
        * `max_backfill_days` — the ceiling on one `record_wellness_days` call.

        Bundled reference data, identical on every deployment.

        Requires a `read` key.
        """
        require_scope(Scope.READ)
        with tool_errors():
            async with session_scope() as session:
                return {
                    "inputs": views.wellness_inputs(),
                    "red_flag": views.red_flag(await current_profile(session)),
                }

    @mcp.tool
    async def get_wellness(
        start: str, end: str, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        """Read the athlete's reported wellness over a date range, day by day.

        Whole days, oldest first — a wellness series is read forwards, the way
        a chart is drawn. Two things on every day decide how to read its
        numbers:

        * `markers.actionable` — false when the athlete declared a confounder
          that voids the morning's objective readings. The values are still
          here (they are real, and they are part of the history); what is
          withheld is their standing as **evidence today**. `markers.statement`
          says it in one line.
        * `subjective_recalled` — true when the day was entered late enough
          that the *felt* ratings are memory rather than report. The device
          numbers are never discounted for this: the watch measured them on the
          day, whatever day they were typed in.

        A day the athlete did not answer is **absent from `items` and listed in
        `missing`**. It is never returned as a day of nulls, because "reported
        nothing" and "was not asked" are different facts. `missing` covers the
        **whole range you asked for**, not the page you got back — so a date in
        it is a date nobody answered, even when `total` says there is more to
        page through.

        A `null` field on a day that *is* here means **not provided**, never
        zero — do not average it in as one.

        Args:
            start: First local date, `YYYY-MM-DD`, inclusive.
            end: First local date **after** the range — it is half-open
                `[start, end)`. To read a single day, pass the next day as
                `end`. At most 371 days per call.
            limit: How many days to return.
            offset: How many to skip, for reading past the first page.

        Requires a `read` key.
        """
        require_scope(Scope.READ)
        with tool_errors():
            async with session_scope() as session:
                service = WellnessService.from_session(session)
                first = views.as_date(start, field="start")
                last = views.as_date(end, field="end")
                resolved = await service.range(
                    start=first,
                    end=last,
                    offset=_offset(offset),
                    limit=_limit(limit),
                )
                items = [
                    views.wellness_day(
                        row, subjective_recalled=service.is_recalled(row)
                    )
                    for row in resolved.days
                ]
                return {
                    **views.page(
                        items,
                        total=resolved.total,
                        limit=_limit(limit),
                        offset=_offset(offset),
                    ),
                    # From the service, over the whole range — never derived
                    # from `items`, which is one page of it.
                    "missing": [day.isoformat() for day in resolved.missing],
                    "weight_in_force": views.weight_in_force(
                        await service.weight_in_force(last - dt.timedelta(days=1))
                    ),
                    "red_flag": views.red_flag(await current_profile(session)),
                }

    @mcp.tool
    async def get_wellness_trend(
        start: str, end: str, metrics: list[str] | None = None
    ) -> dict[str, Any]:
        """Read what the athlete's numbers *mean*, not just what they are.

        `resting_hr: 54` is not a fact you can act on: fifty-four is alarming
        for one athlete and a Tuesday for another. This answers every metric
        against **this** athlete's own trailing-60-day baseline — the mean, the
        normal band, and how far the last seven days sit from it in standard
        deviations. It is the read to use before saying anything about a
        trend.

        Four things it is careful about, each of them a way these numbers could
        be read as more than they are:

        * **An immature baseline abstains.** Under 14 readings spanning 28
          days, `baseline.kind` is `abstention` and there is **no** `mean`, no
          `band` and no `deviation_sd` on it — only both counts and what it
          would take to have one. Do not fill that in from the series
          yourself; nine readings do not make a trend, and saying so is the
          most useful thing you can do with them.
        * **`deviation_sd` compares the seven-day mean to the baseline**, never
          today to yesterday. One bad night can move it by three sevenths of an
          SD at most. Every mean carries the `n` it was computed over.
        * **A gap is `null`, never zero.** A date the athlete did not answer is
          in the series with a null value; averaging it in as a zero is how a
          missed morning becomes a resting heart rate of nought.
        * **A voided morning still shows its numbers**, with `markers` on the
          same object saying they are not evidence about today. HRV is never
          pooled across statistic or context: `hrv_rmssd_ms` and `hrv_sdnn_ms`
          are separate metrics, and `by_context` splits sleeping from daytime
          spot samples, which are different distributions.

        `readiness` counts how many markers sit outside their own band and
        names each with a direction, and `joint_state` names the HRV x
        resting-HR quadrant when both are mature — a **label**, not a verdict.
        There is no readiness score here and there is not meant to be: whether
        today is a day to train is your call, made out loud, with the
        confounders and the gaps visible.

        Args:
            start: First local date of the series, `YYYY-MM-DD`, inclusive.
            end: First local date **after** it — half-open `[start, end)`. At
                most 371 days. The baseline still reaches 60 days back from
                the last day of the range whatever you ask for.
            metrics: Which metrics to answer for. Omit for all of them.
                `resting_hr_bpm`, `hrv_rmssd_ms`, `hrv_sdnn_ms`,
                `respiratory_rate_brpm`, `wrist_temperature_delta_c`, `spo2`,
                `sleep_duration_s`, `weight_kg`, `sleep_quality`, `fatigue`,
                `soreness`, `stress`, `motivation`.

        Requires a `read` key.
        """
        require_scope(Scope.READ)
        with tool_errors():
            async with session_scope() as session:
                resolved = await WellnessService.from_session(session).trend(
                    start=views.as_date(start, field="start"),
                    end=views.as_date(end, field="end"),
                    metrics=metrics,
                )
                return {
                    "trend": views.wellness_trend(resolved),
                    "red_flag": views.red_flag(await current_profile(session)),
                }

    @mcp.tool
    async def get_wellness_weeks(start: str, end: str) -> dict[str, Any]:
        """Summarise reported wellness over a range, week by week.

        The tool for questions about *shape* — how did sleep track against load
        last month, is resting heart rate drifting — without pulling thirty day
        objects and folding them by hand. Monday to Sunday, the same weeks
        `search_history` folds training into, so the two reads line up.

        Every mean carries the **`n` it was computed over**, and that is not
        decoration: a weekly mean over three readings and one over seven are
        different objects, and comparing them without knowing which is which is
        being misled by arithmetic that looks identical. `days_recorded` says
        how many days the week holds at all.

        Two exclusions, both stated on the week:

        * `days_invalidated` — days a confounder voided. Their **objective**
          markers are left out of the means; their subjective ratings still
          count, because a hot room makes a resting heart rate say nothing
          about readiness and does not make "I felt tired" untrue.
        * `days_recalled` — days entered late enough for the felt ratings to be
          memory.

        HRV is **never pooled** across statistic or context: each
        (metric, context) pair is its own entry, named `hrv_ms[rmssd,sleeping]`
        and so on. A sleeping RMSSD mean and a daytime SDNN mean averaged
        together belong to neither.

        This is description, not interpretation. There is no readiness score
        here and there is not meant to be.

        Args:
            start: First local date, `YYYY-MM-DD`, inclusive.
            end: First local date **after** the range (half-open). At most 371
                days.

        Requires a `read` key.
        """
        require_scope(Scope.READ)
        with tool_errors():
            async with session_scope() as session:
                summary = await WellnessService.from_session(session).weeks(
                    start=views.as_date(start, field="start"),
                    end=views.as_date(end, field="end"),
                )
                return {
                    "wellness": views.wellness_weeks(summary),
                    "red_flag": views.red_flag(await current_profile(session)),
                }

    # --- writes ---------------------------------------------------------------

    @mcp.tool
    async def append_anchor(
        anchor_type: AnchorType,
        value: float,
        provenance: Provenance,
        protocol: str | None = None,
        effective_date: str | None = None,
        unit: AnchorUnit | None = None,
        ci_low: float | None = None,
        ci_high: float | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Append a new version to an anchor's history. Never edits one.

        **Never guess an anchor.** If you do not have a measurement or a
        report from the athlete, the answer is to ask them to test, not to
        append an estimate — every prescription from now until the next
        version is scaled against whatever you write here.

        `provenance` is required and says how you know:

        * `tested` — a real test. **Requires `protocol`**, a short string
          naming what was done ("20-minute test x0.95", "ramp"). Refused
          without it.
        * `estimated` — derived from training data. Say how in `protocol`.
        * `athlete_reported` — the athlete told you.
        * `assumed` — a placeholder. Prefer asking to assuming.

        This is append-only: nothing is edited or deleted, and a value you
        regret is corrected by appending a better one. `cp` and `w_prime` are
        reserved and refused. `protocol` is free text of at most 200
        characters — a citation of what was done, not the write-up. Every
        answer carries `budget_remaining` — how many writes the hourly cap
        still admits, after this one if it was real (a dry run is free, so
        there it is simply the current standing).

        **Appending reprices the recorded history the version governs.**
        Sessions whose stored metrics were computed against a different
        measurement of this anchor for their date — including sessions
        ingested before any anchor existed — get a new metric version, and
        `reprice` in the answer reports it: `examined` sessions checked,
        `repriced` recomputed, `unchanged` already priced right, `failed`
        recomputes that errored (logged; each stays recomputable). A
        `reprice.note` means the scan itself failed after the anchor was
        safely written and the counts are unknown, not zero. On a dry run
        nothing is recomputed; `reprice` instead predicts `would_reprice`.

        Args:
            anchor_type: `ftp` (watts), `lthr`, `max_hr`, `resting_hr` (bpm).
            value: The measurement, in the anchor's own unit.
            provenance: How the value was arrived at.
            protocol: How it was measured, at most 200 characters. Required
                for `tested`.
            effective_date: The date it applies from, `YYYY-MM-DD`. Defaults
                to today. Backdating is how a test is recorded late.
            unit: The anchor's own unit is used when omitted; a different one
                is an error, not a conversion request.
            ci_low: Lower bound of the confidence interval, same unit.
            ci_high: Upper bound, same unit.
            dry_run: Validate and return what would be appended and how many
                sessions it would reprice, writing nothing and costing no
                rate-cap budget.

        Requires a `write` key.
        """
        actor = require_scope(Scope.WRITE)
        with tool_errors():
            async with session_scope() as session:
                day = (
                    None
                    if effective_date is None
                    else views.as_date(effective_date, field="effective_date")
                )
                if dry_run:
                    draft, prediction = await preview_anchor_append(
                        session,
                        anchor_type=anchor_type,
                        value=value,
                        provenance=provenance,
                        source=AnchorSource.AGENT,
                        effective_date=day,
                        unit=unit,
                        protocol=protocol,
                        ci_low=ci_low,
                        ci_high=ci_high,
                    )
                    return {
                        "dry_run": True,
                        "anchor": views.anchor_draft(draft),
                        "reprice": views.reprice_prediction(prediction),
                        "budget_remaining": await remaining_write_budget(
                            session, actor
                        ),
                    }
                row, report = await append_anchor_and_reprice(
                    session,
                    actor=actor,
                    anchor_type=anchor_type,
                    value=value,
                    provenance=provenance,
                    source=AnchorSource.AGENT,
                    effective_date=day,
                    unit=unit,
                    protocol=protocol,
                    ci_low=ci_low,
                    ci_high=ci_high,
                )
                return {
                    "dry_run": False,
                    "anchor": views.anchor(row),
                    "reprice": views.reprice(report),
                    "budget_remaining": await remaining_write_budget(session, actor),
                }

    @mcp.tool
    async def create_workout(
        name: str,
        structure: dict[str, Any],
        description: str | None = None,
        folder: str | None = None,
        tags: list[str] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Add a reusable workout to the athlete's library.

        Search `get_workout_library` first — a near-duplicate of something
        already there costs the athlete more than it saves.

        Creating a workout does **not** put it on the calendar. To plan it,
        propose a `create` change referencing the returned `id`
        (`propose_plan_change`) — and read `get_purposes` before choosing
        that change's `purpose`, because the purpose decides the success
        criteria the session is scored against.

        `structure` is the prescription document — the shape the athlete's
        own workout editor produces and `get_workout` returns. A minimal but
        complete document per discipline (durations in seconds, power as
        fractions of the anchor):

        Cycling — a steady warm-up, then 4 x (8 min at 88-93 % of FTP, 4 min
        easy):

        ```json
        {
          "discipline": "cycling",
          "steps": [
            {"kind": "steady", "duration_s": 900, "role": "warmup",
             "targets": {"power": {"kind": "percent_of_anchor",
                                   "anchor_type": "ftp",
                                   "pct_low": 0.5, "pct_high": 0.65}}},
            {"kind": "repeat", "times": 4, "children": [
              {"kind": "steady", "duration_s": 480, "role": "work",
               "targets": {"power": {"kind": "percent_of_anchor",
                                     "anchor_type": "ftp",
                                     "pct_low": 0.88, "pct_high": 0.93}}},
              {"kind": "steady", "duration_s": 240, "role": "recovery",
               "targets": {"power": {"kind": "percent_of_anchor",
                                     "anchor_type": "ftp",
                                     "pct_low": 0.5, "pct_high": 0.55}}}
            ]}
          ]
        }
        ```

        Strength — `groups` of `items`, every exercise named by its slug from
        `get_exercise_catalogue`, loads in kg / fraction of e1RM / RPE /
        bodyweight:

        ```json
        {
          "discipline": "strength",
          "groups": [
            {"label": "A", "items": [
              {"exercise_id": "back_squat", "sets": 5, "reps": 5,
               "load": {"kind": "kg", "value": 100}, "rir": 2, "rest_s": 180}
            ]},
            {"label": "B", "items": [
              {"exercise_id": "romanian_deadlift", "sets": 3, "reps": 8,
               "load": {"kind": "percent_e1rm", "value": 0.7}, "rir": 3},
              {"exercise_id": "single_arm_dumbbell_row", "sets": 3, "reps": 11,
               "per_side": true, "load": {"kind": "kg", "value": 15}},
              {"exercise_id": "front_plank", "sets": 3, "duration_s": 45,
               "load": {"kind": "bodyweight"}}
            ]}
          ]
        }
        ```

        Three rules about a strength item that decide what the numbers mean:

        * **`sets` counts rounds.** `per_side: true` says each round is
          performed one side at a time, so three rounds of eleven is **six**
          working sets and six is what volume and completion count. Set it
          only on movements the catalogue marks `unilateral`; on any other it
          is refused by name.
        * **`kg` is the load moved in one rep as prescribed.** For a per-side
          item that is the load on *that side* — one 15 kg dumbbell is `15`,
          and the example above is 990 kg of volume. For a two-handed item
          held with two implements it is the total: two 15 kg dumbbells are
          `30`.
        * **An item prescribes `reps` or `duration_s`, never both and never
          neither.** A 45-second plank is `duration_s: 45`; writing `reps: 1`
          for it puts an invented number into volume arithmetic.

        An unparseable structure, or one naming an exercise the catalogue
        does not have, is refused with the reason. Every answer carries
        `budget_remaining` — how many writes the hourly cap still admits,
        after this one if it was real (a dry run is free, so there it is the
        current standing).

        Args:
            name: What to call it.
            structure: The prescription document.
            description: Free text about the workout.
            folder: A folder to file it under.
            tags: Short labels for searching.
            dry_run: Validate the structure and tags without writing, costing
                no rate-cap budget. Returns no `id`, because nothing was made
                — and returns the **normalized** tags and `structure`, which
                is what a real call would store, with every default filled
                in.

        Requires a `write` key.
        """
        actor = require_scope(Scope.WRITE)
        with tool_errors():
            async with session_scope() as session:
                service = WorkoutService.from_session(session)
                if dry_run:
                    # Pure delegation: `create` builds its row from this same
                    # method, so the dry run cannot validate less than the
                    # write does. It used to parse the structure here and let
                    # the tags through untouched, which passed calls the real
                    # write refused and echoed tags the real write rewrote.
                    draft = await service.preview(
                        name=name,
                        structure=structure,
                        description=description,
                        folder=folder,
                        tags=tags or (),
                    )
                    return {
                        "dry_run": True,
                        "workout": views.workout_draft(draft),
                        "budget_remaining": await remaining_write_budget(
                            session, actor
                        ),
                    }
                row = await service.create(
                    actor=actor,
                    name=name,
                    structure=structure,
                    description=description,
                    folder=folder,
                    tags=tags or (),
                )
                return {
                    "dry_run": False,
                    "workout": views.workout(row, step_count=step_count_of(row)),
                    "budget_remaining": await remaining_write_budget(session, actor),
                }

    @mcp.tool
    async def propose_plan_change(
        changes: list[dict[str, Any]],
        rationale: str,
        expires_at: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Suggest changes to the plan — you cannot edit the plan yourself.

        This writes a *proposal*. The athlete accepts it, rejects it, or lets
        it lapse — and if it lapses, the committed plan stands unchanged. That
        is the design, not a limitation to route around.

        **Dry-run first whenever more than one session is involved.** The dry
        run computes and returns the same diff the stored proposal would carry
        — every target resolved, every concurrency token checked, the red-flag
        rule applied — and writes nothing. `superseded` on a dry run lists the
        open proposals the real call would displace, so you can see what a
        write would throw away before writing it.

        Each entry in `changes` is one of four shapes:

        * `{"kind": "create", "date": "2026-08-11", "purpose": "endurance",
          "workout_id": "<id>" | null, "structure": {...} | null,
          "intent_text": "...", "coach_notes": "..."}` — exactly one of
          `workout_id` and `structure`.
        * `{"kind": "update", "planned_session_id": "<id>",
          "expected_intent_version": 3, "updates": {...}}` — the fields you
          may revise are `purpose`, `intent_text`, `coach_notes`,
          `success_criteria`, `workout_id`, `structure`, `date`. `status` is
          not one of them: it is derived from what the athlete actually did.
        * `{"kind": "move", "planned_session_id": "<id>",
          "expected_intent_version": 3, "date": "2026-08-13"}`
        * `{"kind": "delete", "planned_session_id": "<id>",
          "expected_intent_version": 3}`

        `expected_intent_version` is the `intent_version` from
        `get_plan_week`, and it is checked **twice** — now, and again when the
        athlete accepts. If the athlete edited that session meanwhile you get
        a `conflict`: re-read the week and propose again. At most one change
        per planned session, and at most 20 changes in a proposal.

        Refusals you should expect and how to answer them:

        * `red_flag` — the athlete is ill or injured and this proposal adds or
          intensifies work. Propose a reduction, a move or a deletion, or
          wait.
        * `rate_limited` — the hourly write cap is spent. Stop writing.
        * `conflict` — a concurrency token is stale. Re-read, re-propose.
        * `invalid` — the request is malformed; the message names the change.

        `get_purposes` enumerates the `purpose` vocabulary and the success
        criteria each purpose maps to — read it before choosing one. Every
        answer carries `budget_remaining`: how many writes the hourly cap
        still admits, after this one if it was real (a dry run is free, so
        there it is the current standing).

        Args:
            changes: One to twenty change objects, as above.
            rationale: Why, in the athlete's terms. Required, and the thing
                they will actually read — a proposal they cannot weigh is one
                they will reject.
            expires_at: When the suggestion stops standing, as an ISO datetime
                **with a timezone offset**. On expiry the committed plan
                stands.
            dry_run: Compute and return the diff, writing nothing and costing
                no rate-cap budget.

        Requires a `write` key.
        """
        actor = require_scope(Scope.WRITE)
        with tool_errors():
            async with session_scope() as session:
                outcome = await ProposalService.from_session(session).propose(
                    actor=actor,
                    changes=changes_from_json(changes),
                    rationale=rationale,
                    expires_at=views.as_datetime(expires_at, field="expires_at"),
                    dry_run=dry_run,
                )
                return {
                    **views.proposal_outcome(outcome, dry_run=dry_run),
                    "budget_remaining": await remaining_write_budget(session, actor),
                }

    @mcp.tool
    async def record_session_context(
        session_id: str,
        rpe: float | None = None,
        temperature_c: float | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Record the conditions one recorded session was performed under.

        These are the facts that decide whether a session's numbers are
        comparable later — the athlete's reported exertion and the ambient
        temperature. Without them, "was that Q4 fade fatigue or a fluid
        deficit at 29 °C" lives in prose nothing can filter; recorded here,
        next week's comparison is a query (`list_sessions`,
        `get_session_detail`) instead of a re-read of every note.

        * `rpe` (0-10) is the athlete's **own report** of how hard it felt.
          Write it only when they told you — never infer it from power or
          heart rate, which is exactly what the measured channels are for.
        * `temperature_c` is the ambient temperature in °C. Values outside
          −30…50 °C are refused as implausible (a Fahrenheit number will be
          caught by this — convert first).

        This writes *context only*. The measured record — recordings,
        streams, computed metrics — stays unwritable from here, and the
        athlete's verdict stays theirs. Give at least one of the two fields;
        a call with neither is refused. Setting a field again overwrites it;
        neither can be cleared from this surface. Every answer carries
        `budget_remaining` — how many writes the hourly cap still admits,
        after this one if it was real (a dry run is free, so there it is the
        current standing).

        Args:
            session_id: The recorded session this context is about.
            rpe: The athlete's reported session RPE, 0-10.
            temperature_c: Ambient temperature in °C, −30…50.
            dry_run: Run every check and return what would be set, writing
                nothing and costing no rate-cap budget. `session` then shows
                the row as it still stands, and `would_set` what a real call
                would change.

        Requires a `write` key.
        """
        actor = require_scope(Scope.WRITE)
        with tool_errors():
            if rpe is None and temperature_c is None:
                raise ValueError(
                    "give at least one of rpe or temperature_c — a context "
                    "write with neither would record nothing"
                )
            updates: dict[str, Any] = {}
            if rpe is not None:
                updates["rpe"] = rpe
            if temperature_c is not None:
                updates["temperature_c"] = temperature_c
            async with session_scope() as session:
                identifier = views.as_uuid(session_id, field="session_id")
                row = await SessionService.from_session(session).update(
                    identifier, updates, actor=actor, dry_run=dry_run
                )
                current = await SessionMetricsService.from_session(
                    session
                ).current_for_sessions([row.id])
                summary = summarise(current[row.id]) if row.id in current else None
                answer: dict[str, Any] = {
                    "dry_run": dry_run,
                    "session": views.session_summary(row, summary),
                    "budget_remaining": await remaining_write_budget(session, actor),
                }
                if dry_run:
                    answer["would_set"] = updates
                return answer

    @mcp.tool
    async def record_manual_session(
        start_time: str,
        duration_s: int,
        timezone: str = "UTC",
        discipline: SessionDiscipline = SessionDiscipline.STRENGTH,
        rpe: float | None = None,
        temperature_c: float | None = None,
        notes: str | None = None,
        sets: list[dict[str, Any]] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Record a session that has no device file — a gym session, usually.

        This is how a session the athlete reported in chat becomes a
        *recorded* session: it lands as the same row a device file would
        produce, and matching and scoring treat it like any recording.
        Matching runs inside this call — the new session is compared against
        the plan exactly as an ingested file is, a strength metric artefact
        is computed from its sets, and `match_status` in the answer says
        where it stood when the call returned (`matched` when a planned
        session claimed it, `unplanned` when nothing could).

        **Record what the athlete reported, not what you assume they did**:
        the sets, loads and RPE here are their account of the session, and a
        set they did not mention is a set that did not happen. Each set
        object is `{"exercise_id": "back_squat", "reps": 5, "load_kg": 100,
        "rir": 2, "notes": "..."}` — `exercise_id` must be a slug from
        `get_exercise_catalogue`, or give `exercise_name` (free text) for a
        movement the catalogue lacks; exactly one of the two per set.
        `load_kg` is absent for bodyweight sets.

        Three fields decide what a set *counts* as, and getting them wrong
        misstates the session's volume:

        * a set carries **`reps` or `duration_s`, never both and never
          neither** — a 45-second plank is `{"duration_s": 45}`, not
          `{"reps": 1}`;
        * **`per_side: true`** says the row is one side of a two-sided
          movement, so it counts as two working sets. One row per round, not
          one per limb: eleven reps each arm is a single
          `{"reps": 11, "per_side": true}`. It is refused on a movement the
          catalogue does not mark unilateral;
        * **`load_kg` on a per-side set is the load on that side** — one
          15 kg dumbbell is `15`, and the volume comes out at 990 kg for three
          such rounds. For a two-handed set held with two implements it is the
          total.

        Every answer carries `budget_remaining` — how many writes the hourly
        cap still admits, after this one if it was real (a dry run is free,
        so there it is the current standing).

        Args:
            start_time: When it started, ISO datetime **with a timezone
                offset** (e.g. `2026-08-12T17:30:00+02:00`).
            duration_s: Wall-clock length in seconds, 60 s to 24 h.
            timezone: The athlete-local timezone (IANA name, `UTC+02:00`, or
                `UTC`) — it fixes which day the session belongs to.
            discipline: `strength` (the default — that is what has no device
                file), `cycling` or `other`.
            rpe: The athlete's reported session RPE, 0-10.
            temperature_c: Ambient temperature in °C, −30…50.
            notes: The athlete's own words about the session.
            sets: The sets they reported, in order, as above.
            dry_run: Run every check — timezone, bounds, every catalogue
                slug — and return the session that would be stored, writing
                nothing and costing no rate-cap budget. No `id` and no
                `match_status`: nothing was written or matched.

        Requires a `write` key.
        """
        actor = require_scope(Scope.WRITE)
        with tool_errors():
            async with session_scope() as session:
                row = await SessionService.from_session(session).create_manual(
                    actor=actor,
                    start_time=views.as_datetime(start_time, field="start_time"),
                    timezone=timezone,
                    duration_s=duration_s,
                    discipline=discipline,
                    rpe=rpe,
                    temperature_c=temperature_c,
                    notes=notes,
                    sets=_logged_sets(sets),
                    dry_run=dry_run,
                )
                if dry_run:
                    return {
                        "dry_run": True,
                        "session": views.manual_session_draft(row),
                        "budget_remaining": await remaining_write_budget(
                            session, actor
                        ),
                    }
                current = await SessionMetricsService.from_session(
                    session
                ).current_for_sessions([row.id])
                summary = summarise(current[row.id]) if row.id in current else None
                return {
                    "dry_run": False,
                    "session": views.session_summary(row, summary),
                    "sets": [views.logged_set(entry) for entry in row.logged_sets],
                    "budget_remaining": await remaining_write_budget(session, actor),
                }

    @mcp.tool
    async def write_session_evaluation(
        session_id: str,
        text: str,
        model_id: str,
        cites: list[str] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Write your read of one recorded session, attributed to you.

        Interpretive text, stored apart from everything the application
        computed and always carrying your `model_id` — so the athlete can tell
        your opinion from a measurement, and can disagree with it. They can
        rate it up or down with one tap; you cannot rate it, and you cannot
        edit or delete it once written.

        Call `get_session_detail` first. An evaluation that contradicts the
        measured record is worse than no evaluation, and the measurements are
        right there — as is `agent_notes`, everything already said about this
        session, so you can see whether you are repeating yourself, disagreeing
        with yourself, or answering something the athlete disputed.

        This does **not** set the verdict. `declared_verdict` and its reasons
        are the athlete's, always.

        Every answer carries `budget_remaining`: how many writes the hourly
        cap still admits, after this one if it was real (a dry run is free,
        so there it is the current standing).

        Args:
            session_id: The recorded session this is about.
            text: The evaluation. Written for the athlete to read.
            model_id: Your model identifier, e.g. `claude-opus-4-6`. Required
                — an unattributed note reads as something the application
                itself believes.
            cites: Ids of the artefacts this rests on (the session, its
                planned session, an anchor version). May be empty; each must
                be a uuid.
            dry_run: Return the note that would be written, writing nothing
                and costing no rate-cap budget.

        Requires a `write` key.
        """
        actor = require_scope(Scope.WRITE)
        with tool_errors():
            async with session_scope() as session:
                row = await AgentNoteService.from_session(session).create(
                    actor=actor,
                    kind=NoteKind.EVALUATION,
                    text=text,
                    model_id=model_id,
                    session_id=views.as_uuid(session_id, field="session_id"),
                    cites=cites or (),
                    dry_run=dry_run,
                )
                return {
                    "dry_run": dry_run,
                    "note": views.note(row),
                    "budget_remaining": await remaining_write_budget(session, actor),
                }

    @mcp.tool
    async def annotate(
        text: str,
        model_id: str,
        session_id: str | None = None,
        plan_week: str | None = None,
        cites: list[str] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Leave free commentary on a session or a plan week.

        The lighter sibling of `write_session_evaluation`: an observation
        rather than a verdict on how one session went — "that is three weeks
        of threshold with no easy week", "the power meter reads low since the
        battery change". Same storage, same attribution, same one-tap dispute.

        Exactly one target. A note about a week is filed under the **Monday**
        the week starts on; any other day is refused, so that one week has one
        key and every read of it finds every note.

        Read what is already there first: a week's notes come back on
        `get_plan_week` (and on `get_coaching_context` for the current week),
        a session's on `get_session_detail`. Nothing here is editable or
        deletable, so a second note that repeats or contradicts the first
        stands beside it for good.

        Every answer carries `budget_remaining`: how many writes the hourly
        cap still admits, after this one if it was real (a dry run is free,
        so there it is the current standing).

        Args:
            text: The comment.
            model_id: Your model identifier. Required.
            session_id: The session it is about, if it is about one.
            plan_week: The Monday of the week it is about, `YYYY-MM-DD`, if it
                is about a week.
            cites: Artefact ids this rests on; may be empty.
            dry_run: Return the note that would be written, writing nothing
                and costing no rate-cap budget.

        Requires a `write` key.
        """
        actor = require_scope(Scope.WRITE)
        with tool_errors():
            async with session_scope() as session:
                row = await AgentNoteService.from_session(session).create(
                    actor=actor,
                    kind=NoteKind.ANNOTATION,
                    text=text,
                    model_id=model_id,
                    session_id=(
                        None
                        if session_id is None
                        else views.as_uuid(session_id, field="session_id")
                    ),
                    plan_week=(
                        None
                        if plan_week is None
                        else views.as_date(plan_week, field="plan_week")
                    ),
                    cites=cites or (),
                    dry_run=dry_run,
                )
                return {
                    "dry_run": dry_run,
                    "note": views.note(row),
                    "budget_remaining": await remaining_write_budget(session, actor),
                }

    @mcp.tool
    async def record_wellness(
        date: str | None = None,
        sleep_duration_s: int | None = None,
        sleep_start_local: str | None = None,
        sleep_end_local: str | None = None,
        sleep_quality: int | None = None,
        resting_hr_bpm: int | None = None,
        hrv_ms: float | None = None,
        hrv_metric: HrvMetric | None = None,
        hrv_context: HrvContext | None = None,
        respiratory_rate_brpm: float | None = None,
        spo2: float | None = None,
        wrist_temperature_delta_c: float | None = None,
        weight_kg: float | None = None,
        fatigue: int | None = None,
        soreness: int | None = None,
        stress: int | None = None,
        motivation: int | None = None,
        soreness_by_region: dict[str, int] | None = None,
        confounders: list[str] | None = None,
        note: str | None = None,
        clear: list[str] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Record what the athlete reported about one day. One day, one row.

        **Never infer a value the athlete did not report.** An HRV you
        estimated, a sleep figure you reconstructed from what time they said
        they went to bed, a fatigue rating you inferred from their tone — all
        of it is the confabulation this whole surface exists to end. If they
        did not say it, leave the field out; an absent value reads as "not
        provided" everywhere and costs nothing, and an invented one enters a
        baseline and stays there.

        **Provenance is `athlete_reported`; source is `agent`.** You record
        what you were told and you never sign as the athlete. Both are on every
        read, so the difference stays visible.

        Call `get_wellness_inputs` first if you have not this session: it is
        the scales, their polarity, the confounder vocabulary and the tiers.

        Two ways a day changes, and they are different:

        * an **omitted** field is left exactly as it was — so you can record
          the HRV in the morning and the motivation at lunch;
        * a field named in **`clear`** is removed. That is how a typo is
          retracted, and there is no other way: a wrong reading you cannot
          take back is a permanent lie in every mean computed after it.

        `date` may be **any past date**, so backfilling one day needs no other
        tool — but for a file of history use `record_wellness_days`, which
        costs one write instead of one per day. A **future** date is refused: a
        reading for tomorrow is not a late entry, it is a typo.

        Bounds are typo guards, not clinical limits: SpO2 is a **fraction**
        (`0.97`, not `97`), sleep is **seconds**, wrist temperature is the
        **delta** from the device's own baseline, and the subjective scales are
        1-5 with declared directions.

        An HRV reading must say **which statistic** (`rmssd` or `sdnn`) and
        **how it was taken** (`sleeping`, `waking_spot`, `manual`). The two
        statistics are not on one scale and the two contexts are not one
        distribution, so a reading missing either cannot join a baseline
        honestly and is refused.

        Recording works normally while the red flag is up. A day's readings are
        testimony, not an intensification, and an ill athlete is exactly who
        most needs them recorded.

        Args:
            date: The day these readings describe, `YYYY-MM-DD`. Defaults to
                today on the athlete's clock.
            sleep_duration_s: Seconds slept.
            sleep_start_local: Clock time sleep began, `HH:MM` — no date, no
                offset. An overnight reading belongs to the **wake** day.
            sleep_end_local: Clock time sleep ended, `HH:MM`.
            sleep_quality: 1-5, higher is better.
            resting_hr_bpm: Resting heart rate.
            hrv_ms: Heart-rate variability in milliseconds.
            hrv_metric: `rmssd` or `sdnn`. Required with `hrv_ms`.
            hrv_context: `sleeping`, `waking_spot` or `manual`. Required with
                `hrv_ms`.
            respiratory_rate_brpm: Breaths per minute.
            spo2: Blood oxygen as a fraction, e.g. `0.97`.
            wrist_temperature_delta_c: Deviation from the device's baseline.
            weight_kg: Body weight. The most recent one on or before a date is
                what watts per kilogram is computed against.
            fatigue: 1-5, higher is worse.
            soreness: 1-5 overall, higher is worse.
            stress: 1-5, higher is worse.
            motivation: 1-5, higher is better.
            soreness_by_region: `{"quads": 3}` — regions from
                `get_wellness_inputs`.
            confounders: Tags from the controlled vocabulary. Some of them make
                the morning's objective markers unusable as evidence, and the
                read says which.
            note: The athlete's own words. Never parsed — put anything the
                vocabulary has no tag for here, and tag what it does have.
            clear: Field names to remove from the day.
            dry_run: Validate everything and return what would be stored,
                writing nothing and costing no rate-cap budget.

        Requires a `write` key.
        """
        actor = require_scope(Scope.WRITE)
        with tool_errors():
            supplied: dict[str, Any] = {
                "sleep_duration_s": sleep_duration_s,
                "sleep_start_local": sleep_start_local,
                "sleep_end_local": sleep_end_local,
                "sleep_quality": sleep_quality,
                "resting_hr_bpm": resting_hr_bpm,
                "hrv_ms": hrv_ms,
                "hrv_metric": hrv_metric,
                "hrv_context": hrv_context,
                "respiratory_rate_brpm": respiratory_rate_brpm,
                "spo2": spo2,
                "wrist_temperature_delta_c": wrist_temperature_delta_c,
                "weight_kg": weight_kg,
                "fatigue": fatigue,
                "soreness": soreness,
                "stress": stress,
                "motivation": motivation,
                "soreness_by_region": soreness_by_region,
                "confounders": confounders,
                "note": note,
            }
            # An argument left at its default means "not given", which is why
            # `clear` has to exist: one word cannot mean both "leave it alone"
            # and "remove it".
            updates = _wellness_updates(
                {name: value for name, value in supplied.items() if value is not None},
                where="",
            )
            cleared = _cleared(clear)
            overlap = sorted(set(updates) & set(cleared))
            if overlap:
                raise ValueError(
                    f"cannot both set and clear {', '.join(overlap)} in one "
                    "call — say which you meant"
                )
            updates |= cleared
            if not updates:
                raise ValueError(
                    "give at least one field — a wellness write with nothing "
                    "in it would record nothing, and an absent day already "
                    "says that"
                )
            async with session_scope() as session:
                service = WellnessService.from_session(session)
                day = (
                    service.local_today()
                    if date is None
                    else views.as_date(date, field="date")
                )
                result = await service.record(
                    day,
                    updates,
                    actor=actor,
                    source=WellnessSource.AGENT,
                    dry_run=dry_run,
                )
                answer: dict[str, Any] = {
                    "dry_run": dry_run,
                    "day": views.wellness_day_result(result),
                    "budget_remaining": await remaining_write_budget(session, actor),
                }
                if not dry_run:
                    # Null when the write cleared the day's last value: the day
                    # was retracted, and there is nothing to read back.
                    answer["wellness"] = (
                        None
                        if result.day is None
                        else views.wellness_day(
                            (row := await service.get(day)),
                            subjective_recalled=service.is_recalled(row),
                        )
                    )
                return answer

    @mcp.tool
    async def record_wellness_days(
        days: list[dict[str, Any]], dry_run: bool = False
    ) -> dict[str, Any]:
        """Record many days at once — **this is the tool for migrating history**.

        When the athlete hands you a file of past readings, a watch export, or
        weeks of numbers from a note, send them here. Do **not** loop
        `record_wellness`: that spends one write per day against the hourly cap
        and will strand a sixty-day migration around day sixty. One call to
        this tool is **one** write however many days it carries, bounded
        instead by a ceiling of 366 days — a year, the natural unit of
        "here is my history".

        Everything `record_wellness` says applies to every day in here, and two
        of them bear repeating because a batch makes them cheaper to get wrong:
        **never infer a value the athlete did not report**, and provenance is
        `athlete_reported` with source `agent` — you are transcribing, not
        testifying.

        The batch **lands whole or not at all**. One day that breaks a rule
        refuses the entire call and writes nothing, and the refusal names the
        date and the field: a partial migration would leave the athlete unable
        to tell which days made it, and your retry unable to reason about the
        overlap. So **dry-run first**: it reports exactly the per-day outcomes
        the real call would produce, costs no budget, and is the difference
        between one clean import and three half ones.

        Days that already exist are **updated, not replaced** — a field this
        batch does not mention is left alone — and every day comes back marked
        `created` or `updated`, so a re-run is legible. A field given as
        **`null`** on one of these days *clears* it, the same as
        `record_wellness`'s `clear`; a day whose last value is cleared that way
        is `retracted` and its row goes.

        Backfilled days count from the date they **describe**, not the date you
        sent them. That is what makes this worth doing: an athlete who imports
        ninety days of watch HRV has ninety days of real history, not ninety
        days entered today.

        Args:
            days: One object per date. Each carries `date` (`YYYY-MM-DD`) plus
                any of the fields `record_wellness` takes — for example
                `{"date": "2026-06-01", "resting_hr_bpm": 48, "hrv_ms": 61,
                "hrv_metric": "rmssd", "hrv_context": "sleeping",
                "sleep_duration_s": 27000}`. A date may appear only once.
            dry_run: Report the per-day outcomes without writing, costing no
                rate-cap budget.

        Requires a `write` key.
        """
        actor = require_scope(Scope.WRITE)
        with tool_errors():
            if not days:
                raise ValueError(
                    "give at least one day — an empty batch records nothing"
                )
            entries = []
            for index, payload in enumerate(days, start=1):
                if not isinstance(payload, dict):
                    raise ValueError(
                        f"day {index} must be an object with a date and some "
                        f"fields, got {payload!r}"
                    )
                raw = dict(payload)
                given = raw.pop("date", None)
                if not isinstance(given, str):
                    raise ValueError(
                        f"day {index} needs a `date` in YYYY-MM-DD form: every "
                        "reading is dated by the day it describes, which is "
                        "what makes this a migration rather than a bulk entry "
                        "for today"
                    )
                local_date = views.as_date(given, field=f"day {index} date")
                entries.append(
                    DayInput(
                        local_date=local_date,
                        updates=_wellness_updates(
                            raw, where=f"{local_date.isoformat()}: "
                        ),
                    )
                )
            async with session_scope() as session:
                results = await WellnessService.from_session(session).record_many(
                    entries,
                    actor=actor,
                    source=WellnessSource.AGENT,
                    dry_run=dry_run,
                )
                return {
                    "dry_run": dry_run,
                    "day_count": len(results),
                    # One count per outcome — `created`, `updated`,
                    # `retracted` — so a re-run of an import is legible at a
                    # glance without reading three hundred day objects.
                    "outcomes": dict(Counter(day.outcome.value for day in results)),
                    "days": [views.wellness_day_result(day) for day in results],
                    "budget_remaining": await remaining_write_budget(session, actor),
                }

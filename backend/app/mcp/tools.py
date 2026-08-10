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
from collections.abc import Iterator
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
from app.domain.anchors import AnchorSource, AnchorType, AnchorUnit, Provenance
from app.domain.athlete import Discipline
from app.domain.proposals import changes_from_json
from app.domain.workout import discipline_of
from app.mcp import views
from app.mcp.auth import Scope
from app.mcp.identity import require_scope
from app.persistence.db import session_scope
from app.services.activity import SessionService
from app.services.agent_notes import AgentNoteService
from app.services.anchors import AnchorService
from app.services.guardrails import current_profile
from app.services.history import HistoryService
from app.services.matching import MatchingService
from app.services.metrics import SessionMetricsService, summarise
from app.services.plan import PlanService
from app.services.proposals import ProposalService
from app.services.scoring import ScoringService
from app.services.workouts import WorkoutService, WorkoutSummary, summarize

logger = get_logger(__name__)

#: Most rows any one read tool will return. A model that asks for everything
#: gets a page and a total, so it can see that there was more.
MAX_LIMIT = 200

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
            "The server failed to handle this call. It has been logged; "
            "nothing was written. Try again, or ask the athlete to check the "
            "server log."
        ) from exc


def _limit(limit: int) -> int:
    """Clamp a caller-supplied page size into what the surface will serve."""
    return max(1, min(limit, MAX_LIMIT))


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
    async def get_athlete() -> dict[str, Any]:
        """Read the athlete's profile, plan state and illness/injury flag.

        Start here. `plan_state` is `active` or `paused` — while paused the
        athlete has stopped training and nothing is being scored, so
        suggesting a busier week answers a question nobody asked.

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
        anchor_type: AnchorType | None = None, limit: int = 50
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

        Requires a `read` key.
        """
        require_scope(Scope.READ)
        with tool_errors():
            async with session_scope() as session:
                rows, total = await AnchorService.from_session(session).list(
                    anchor_type=anchor_type, offset=0, limit=_limit(limit)
                )
                return {
                    **views.page(
                        [views.anchor(row) for row in rows],
                        total=total,
                        limit=_limit(limit),
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
                return {
                    "week": views.plan_week(week),
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

        Use this before writing an evaluation: an evaluation that contradicts
        the measured record is worse than none.

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

                row = await sessions.get(identifier)
                metrics_row = await metrics_service.get_current(identifier)
                summary = None if metrics_row is None else summarise(metrics_row)
                links = await matching.for_sessions([identifier])
                return {
                    "session": views.session_summary(row, summary),
                    "notes": row.notes,
                    "metrics": views.metrics(summary, metrics_row),
                    "score": views.score(await scoring.get_current(identifier)),
                    "alignment": views.alignment(await scoring.alignment(identifier)),
                    "declaration": views.declaration(
                        await scoring.declaration(identifier)
                    ),
                    "match": views.match(links.get(identifier)),
                    "red_flag": views.red_flag(await current_profile(session)),
                }

    @mcp.tool
    async def list_sessions(
        start: str | None = None,
        end: str | None = None,
        discipline: SessionDiscipline | None = None,
        limit: int = 50,
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
                    offset=0,
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
                    **views.page(items, total=total, limit=_limit(limit)),
                    "red_flag": views.red_flag(await current_profile(session)),
                }

    @mcp.tool
    async def get_workout_library(
        query: str | None = None,
        folder: str | None = None,
        tag: str | None = None,
        discipline: Discipline | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Browse the athlete's saved workouts, newest first.

        These are reusable prescriptions a planned session can point at.
        Search here **before** writing a new one with `create_workout`: a
        library with four versions of the same 2x20 is worse than one with a
        single good one.

        The prescription document itself is not returned — only the name,
        folder, tags and step count. To plan one, reference its `id` in a
        `create` change.

        Args:
            query: Free-text match on the name.
            folder: Restrict to one folder.
            tag: Restrict to one tag.
            discipline: `cycling` or `strength`.
            limit: How many to return.

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
                    offset=0,
                    limit=_limit(limit),
                )
                items = [views.workout(row, step_count=_steps(row)) for row in rows]
                return {
                    **views.page(items, total=total, limit=_limit(limit)),
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
        reserved and refused.

        Args:
            anchor_type: `ftp` (watts), `lthr`, `max_hr`, `resting_hr` (bpm).
            value: The measurement, in the anchor's own unit.
            provenance: How the value was arrived at.
            protocol: How it was measured. Required for `tested`.
            effective_date: The date it applies from, `YYYY-MM-DD`. Defaults
                to today. Backdating is how a test is recorded late.
            unit: The anchor's own unit is used when omitted; a different one
                is an error, not a conversion request.
            ci_low: Lower bound of the confidence interval, same unit.
            ci_high: Upper bound, same unit.
            dry_run: Validate and return what would be appended, writing
                nothing and costing no rate-cap budget.

        Requires a `write` key.
        """
        actor = require_scope(Scope.WRITE)
        with tool_errors():
            async with session_scope() as session:
                service = AnchorService.from_session(session)
                day = (
                    None
                    if effective_date is None
                    else views.as_date(effective_date, field="effective_date")
                )
                if dry_run:
                    draft = service.preview(
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
                    return {"dry_run": True, "anchor": views.anchor_draft(draft)}
                row = await service.append(
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
                return {"dry_run": False, "anchor": views.anchor(row)}

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
        (`propose_plan_change`).

        `structure` is the prescription document. Its shape is the one the
        athlete's own workout editor produces; the safest way to write a new
        one is to read an existing workout of the same discipline and follow
        it. An unparseable structure, or one naming an exercise the library
        does not have, is refused with the reason.

        Args:
            name: What to call it.
            structure: The prescription document.
            description: Free text about the workout.
            folder: A folder to file it under.
            tags: Short labels for searching.
            dry_run: Validate the structure and tags without writing, costing
                no rate-cap budget. Returns no `id`, because nothing was made.

        Requires a `write` key.
        """
        actor = require_scope(Scope.WRITE)
        with tool_errors():
            async with session_scope() as session:
                service = WorkoutService.from_session(session)
                if dry_run:
                    # Parsing the structure is the whole of what could fail,
                    # and the service exposes it because the athlete's own
                    # editor validates the same way. Running it without the
                    # write is therefore the honest preview, computed by the
                    # code the real write would have used.
                    body = await service.parse_structure(structure)
                    return {
                        "dry_run": True,
                        "workout": {
                            "name": name,
                            "description": description,
                            "discipline": discipline_of(body).value,
                            "folder": folder,
                            "tags": list(tags or ()),
                            "step_count": WorkoutSummary(body).step_count,
                        },
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
                    "workout": views.workout(row, step_count=_steps(row)),
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
        rule applied — and writes nothing.

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
                return views.proposal_outcome(outcome, dry_run=dry_run)

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
        right there.

        This does **not** set the verdict. `declared_verdict` and its reasons
        are the athlete's, always.

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
                return {"dry_run": dry_run, "note": views.note(row)}

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
                return {"dry_run": dry_run, "note": views.note(row)}


def _steps(row: Any) -> int | None:
    """The step count of a stored workout, or None if it no longer parses.

    A library the agent cannot list because one old document went stale is
    worse than a list with a null in it.
    """
    try:
        return summarize(row).step_count
    except ValueError:
        return None

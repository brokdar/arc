"""JSON projections for the MCP tool surface.

What `app/api/schemas/` is to the HTTP adapter, this is to the MCP one: the
place where domain objects and ORM rows become the wire shape *this* adapter
serves. It exists rather than reusing the pydantic schemas because `app.mcp`
may not import `app.api` (they are siblings, and neither may depend on the
other), and it would exist anyway because the two audiences want different
things:

* the HTTP client is a UI rendering every coverage counter the week has;
* the caller here is a language model paying for every token, and a tool that
  answers a question about last Tuesday with four hundred fields has answered
  a different question.

So these are **compact**. Where a field is dropped it is because the agent has
a tool that gets it (`get_session_detail` for one session's detail), and where
one is kept that looks redundant — `load_sessions_uncounted` next to `load` —
it is because the number above it is a lie without it.

Nothing here decides anything: every value comes from a service. Rendering is
what an adapter is for.
"""

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from app.domain.anchors import AnchorVersion
from app.domain.athlete import AthleteProfile
from app.persistence.activity import SessionRow, session_duration_s
from app.persistence.agent_notes import AgentNoteRow
from app.persistence.anchors import AnchorVersionRow
from app.persistence.matching import SessionMatchRow
from app.persistence.metrics import SessionMetricsRow
from app.persistence.proposals import PlanProposalRow
from app.persistence.scoring import (
    SessionAlignmentRow,
    SessionScoreRow,
    VerdictDeclarationRow,
)
from app.persistence.workouts import WorkoutRow
from app.services.history import HistorySummary, HistoryWeek
from app.services.metrics import MetricSummary
from app.services.plan import PlanWeek, WeekSession
from app.services.proposals import ProposalOutcome
from app.services.workouts import WorkoutDraft


def red_flag(profile: AthleteProfile) -> dict[str, Any]:
    """The illness/injury state, as it appears on **every** read.

    On every read on purpose (WP-8.4): an agent that has to remember to ask
    whether the athlete is unwell will one day not ask, and the refusal it
    then walks into will look like a bug rather than a rule. `note` and
    `severity` are null whenever the flag is down — the domain refuses to hold
    a stale note behind a cleared flag.
    """
    return {
        "active": profile.red_flag_active,
        "severity": (
            None
            if profile.red_flag_severity is None
            else profile.red_flag_severity.value
        ),
        "note": profile.red_flag_note,
    }


def athlete(profile: AthleteProfile, *, today: dt.date) -> dict[str, Any]:
    """The athlete profile the agent plans against."""
    return {
        "name": profile.name,
        "sex": profile.sex.value,
        "date_of_birth": (
            None if profile.date_of_birth is None else profile.date_of_birth.isoformat()
        ),
        "age": profile.age_on(today),
        "height_cm": profile.height_cm,
        #: Per-discipline experience and equipment, as the athlete filled it in.
        "capabilities": dict(profile.capabilities),
        #: `paused` means the athlete has stopped: nothing is scored against
        #: the plan, and proposing a busier week is answering a question
        #: nobody asked.
        "plan_state": profile.plan_state.value,
    }


def anchor(row: AnchorVersionRow) -> dict[str, Any]:
    """One version in an anchor's append-only history."""
    return {
        "id": str(row.id),
        "anchor_type": row.anchor_type.value,
        "value": row.value,
        "unit": row.unit.value,
        "provenance": row.provenance.value,
        "protocol": row.protocol,
        "effective_date": row.effective_date.isoformat(),
        "ci_low": row.ci_low,
        "ci_high": row.ci_high,
        "source": row.source.value,
        "staleness_state": row.staleness_state.value,
        "created_at": row.created_at.isoformat(),
    }


def anchor_draft(version: AnchorVersion) -> dict[str, Any]:
    """The version an append *would* add — the dry run's answer.

    No `id`: nothing was written, and inventing one would let a caller store
    a reference to a row that does not exist.
    """
    return {
        "anchor_type": version.anchor_type.value,
        "value": version.value,
        "unit": version.unit.value,
        "provenance": version.provenance.value,
        "protocol": version.protocol,
        "effective_date": version.effective_date.isoformat(),
        "ci_low": version.ci_low,
        "ci_high": version.ci_high,
        "source": version.source.value,
    }


def week_session(card: WeekSession) -> dict[str, Any]:
    """One planned session on the week grid.

    ``intent_version`` is the optimistic-concurrency token: it is what a
    change in `propose_plan_change` must carry as `expected_intent_version`,
    which is why a compact view keeps it.
    """
    return {
        "id": str(card.id),
        "date": card.date.isoformat(),
        "discipline": card.discipline.value,
        "purpose": card.purpose.value,
        "status": card.status.value,
        "completion_state": card.completion_state.value,
        "title": card.title,
        "workout_id": None if card.workout_id is None else str(card.workout_id),
        "intent_text": card.intent_text,
        "intent_version": card.intent_version,
        "planned_duration_s": card.planned_duration_s,
        "total_sets": card.total_sets,
        "predicted_load": card.predicted_load,
        "predicted_volume_load_kg": card.predicted_volume_load_kg,
        "matched_session_id": (
            None if card.matched_session_id is None else str(card.matched_session_id)
        ),
    }


def plan_week(week: PlanWeek) -> dict[str, Any]:
    """One Monday-to-Sunday plan week, flattened.

    The day grid is dropped and the sessions are listed with their dates: an
    empty Wednesday is a fact the reader can see from the dates, and seven
    nested objects to say it is six of them saying nothing.
    """
    return {
        "start": week.start.isoformat(),
        "end": week.end.isoformat(),
        "session_count": week.session_count,
        "planned_duration_s": week.planned_duration_s,
        "planned_load": week.planned_load,
        "planned_load_sessions_uncounted": week.load_sessions_uncounted,
        "completed_session_count": week.completed_session_count,
        "completed_duration_s": week.completed_duration_s,
        "completed_load": week.completed_load,
        "sessions": [week_session(card) for day in week.days for card in day.sessions],
        "by_discipline": [
            {
                "discipline": part.discipline.value,
                "session_count": part.session_count,
                "planned_duration_s": part.planned_duration_s,
                "planned_load": part.planned_load,
                "total_sets": part.total_sets,
                "completed_session_count": part.completed_session_count,
                "completed_duration_s": part.completed_duration_s,
                "completed_load": part.completed_load,
            }
            for part in week.by_discipline
        ],
    }


def session_summary(row: SessionRow, summary: MetricSummary | None) -> dict[str, Any]:
    """One recorded session in a list.

    ``local_date`` rather than the UTC timestamp is the day the athlete would
    name, and the day the plan places work on — the two must agree or a
    question about "Tuesday" has two answers.
    """
    return {
        "id": str(row.id),
        "local_date": row.local_date.isoformat(),
        "start_time": row.start_time.isoformat(),
        "timezone": row.timezone,
        "discipline": row.discipline.value,
        "recording_kind": row.recording_kind.value,
        "match_status": row.status.value,
        "context": row.session_context.value,
        "duration_s": session_duration_s(row),
        "training_load": None if summary is None else summary.training_load,
        "rpe": row.rpe,
    }


def metrics(summary: MetricSummary | None, row: SessionMetricsRow | None) -> Any:
    """The computed metrics of one session, or null if it has none."""
    if summary is None or row is None:
        return None
    return {
        "version": summary.version,
        "recording_time_s": summary.recording_time_s,
        "training_load": summary.training_load,
        "load_basis": None if summary.load_basis is None else summary.load_basis.value,
        "zone_channel": (
            None if summary.zone_channel is None else summary.zone_channel.value
        ),
        "easy_s": summary.easy_s,
        "moderate_s": summary.moderate_s,
        "hard_s": summary.hard_s,
        "normalized_power": summary.normalized_power,
        "average_hr": summary.average_hr,
        "distance_km": summary.distance_km,
        "interval_count": summary.interval_count,
        "computed_at": row.as_of.isoformat(),
    }


def score(row: SessionScoreRow | None) -> Any:
    """The current score of one session, axes and criteria included.

    The stored payload is passed through rather than re-rendered: it is
    already the serialized `app.domain.scoring.SessionScore`, and a second
    rendering of it here would be a second thing to keep in step with the
    scoring engine.

    Null when the session has no standing score — an unmatched recording is
    not scored against anything, which is a real answer and not a gap.
    """
    if row is None:
        return None
    payload = dict(row.payload)
    return {
        "version": row.version,
        "computed_at": row.as_of.isoformat(),
        "planned_session_id": (
            None if row.planned_session_id is None else str(row.planned_session_id)
        ),
        "intent_version": row.intent_version,
        "suggested_verdict": row.suggested_verdict.value,
        "verdict_rule": payload.get("verdict_rule"),
        "verdict_rationale": payload.get("verdict_rationale"),
        "purpose": payload.get("purpose"),
        "standalone": payload.get("standalone"),
        "axes": payload.get("axes", []),
        "other_criteria": payload.get("other_criteria", []),
    }


def declaration(row: VerdictDeclarationRow | None) -> Any:
    """The athlete's declared verdict, or null if they have not declared.

    **Read-only to the agent, always.** There is no tool that writes this and
    there must not be: the verdict is the athlete's word on their own session
    (invariant: the agent may never write `declared_verdict` or reasons).
    """
    if row is None:
        return None
    return {
        "declared_verdict": row.declared_verdict.value,
        "declared_at": row.declared_at.isoformat(),
        "suggested_at_declaration": (
            None
            if row.suggested_at_declaration is None
            else row.suggested_at_declaration.value
        ),
        #: True when the athlete disagreed with the engine. The most
        #: interesting thing on this object: it is where the model of the
        #: athlete and the athlete part company.
        "contested": row.contested,
        "contested_verdict": (
            None if row.contested_verdict is None else row.contested_verdict.value
        ),
    }


def alignment(row: SessionAlignmentRow | None) -> Any:
    """How the recording lines up against the prescription, or null."""
    if row is None:
        return None
    return {
        "version": row.version,
        "offset_s": row.offset_s,
        "computed_at": row.as_of.isoformat(),
        **dict(row.payload),
    }


def match(row: SessionMatchRow | None) -> Any:
    """The link between a recording and a planned session, or null."""
    if row is None:
        return None
    return {
        "id": str(row.id),
        "status": row.status.value,
        "planned_session_id": str(row.planned_session_id),
        "similarity": row.similarity,
        "created_by": row.created_by,
        "confirmed_at": (
            None if row.confirmed_at is None else row.confirmed_at.isoformat()
        ),
    }


def workout(row: WorkoutRow, *, step_count: int | None) -> dict[str, Any]:
    """One library workout, without its structure.

    The prescription document is deliberately absent from the list view: it is
    the largest thing in the library and the agent asking "what have I got"
    does not need it. ``step_count`` is null when the stored document no
    longer parses, which is a thing worth seeing rather than an exception.
    """
    return {
        "id": str(row.id),
        "name": row.name,
        "description": row.description,
        "discipline": row.discipline.value,
        "folder": row.folder,
        "tags": list(row.tag_names),
        "step_count": step_count,
    }


def workout_draft(draft: WorkoutDraft) -> dict[str, Any]:
    """The workout a create *would* add — the dry run's answer.

    No `id`, for the reason :func:`anchor_draft` has none: nothing was written.
    ``tags`` are the normalized ones the write would store, not the ones the
    caller sent, so a dry run and the call after it agree about what is in the
    library.
    """
    return {
        "name": draft.name,
        "description": draft.description,
        "discipline": draft.discipline.value,
        "folder": draft.folder,
        "tags": list(draft.tags),
        "step_count": draft.step_count,
    }


def history_week(week: HistoryWeek) -> dict[str, Any]:
    """One week of the training history summary."""
    return {
        "start": week.start.isoformat(),
        "end": week.end.isoformat(),
        "session_count": week.session_count,
        "duration_s": week.duration_s,
        "load": week.load,
        "load_sessions_counted": week.load_sessions_counted,
        "load_sessions_uncounted": week.load_sessions_uncounted,
        "verdicts": dict(week.verdicts),
        "by_discipline": [
            {
                "discipline": part.discipline.value,
                "session_count": part.session_count,
                "duration_s": part.duration_s,
                "load": part.load,
                "load_sessions_uncounted": part.load_sessions_uncounted,
            }
            for part in week.by_discipline
        ],
    }


def history(summary: HistorySummary) -> dict[str, Any]:
    """A date range of recorded training, folded into weeks."""
    return {
        "start": summary.start.isoformat(),
        "end": summary.end.isoformat(),
        "session_count": summary.session_count,
        "duration_s": summary.duration_s,
        "load": summary.load,
        "load_sessions_counted": summary.load_sessions_counted,
        "load_sessions_uncounted": summary.load_sessions_uncounted,
        "verdicts": dict(summary.verdicts),
        "weeks": [history_week(week) for week in summary.weeks],
    }


def note(row: AgentNoteRow) -> dict[str, Any]:
    """One agent note. ``id`` and ``created_at`` are null on a dry run."""
    return {
        "id": None if row.id is None else str(row.id),
        "kind": row.kind.value,
        "session_id": None if row.session_id is None else str(row.session_id),
        "plan_week": None if row.plan_week is None else row.plan_week.isoformat(),
        "text": row.text,
        "model_id": row.model_id,
        "created_by": row.created_by,
        "created_at": None if row.created_at is None else row.created_at.isoformat(),
        "cites": list(row.cites),
        "dispute": None if row.dispute is None else row.dispute.value,
    }


def proposal(row: PlanProposalRow) -> dict[str, Any]:
    """One stored plan-change proposal."""
    return {
        "id": str(row.id),
        "status": row.status.value,
        "rationale": row.rationale,
        "expires_at": row.expires_at.isoformat(),
        "created_at": row.created_at.isoformat(),
        "created_by": row.created_by,
        "supersedes_id": (
            None if row.supersedes_id is None else str(row.supersedes_id)
        ),
    }


def proposal_outcome(outcome: ProposalOutcome, *, dry_run: bool) -> dict[str, Any]:
    """What `propose_plan_change` answers with.

    ``proposal`` is null on a dry run and the diff is the whole answer — the
    same diff the stored proposal would have carried, computed by the same
    code path, which is what makes dry-running worth doing.

    ``superseded`` reads the same way: on a dry run it is what the real call
    *would* displace, still pending and untouched. Same key either way, because
    ``dry_run`` already says which of the two this is.
    """
    return {
        "dry_run": dry_run,
        "diff": list(outcome.diff),
        "proposal": None if outcome.proposal is None else proposal(outcome.proposal),
        "superseded": [proposal(row) for row in outcome.superseded],
    }


def page(
    items: Sequence[Mapping[str, Any]], *, total: int, limit: int, offset: int = 0
) -> dict[str, Any]:
    """A page of results, with enough around it to ask for the next one.

    ``total`` is what the filters matched and ``offset`` is where this page
    starts, so a caller can tell that there is more *and* say what to skip to
    get it. A total on its own tells an agent it is missing rows and gives it
    no way to read them.
    """
    return {
        "items": list(items),
        "total": total,
        "returned": len(items),
        "limit": limit,
        "offset": offset,
    }


def as_uuid(value: str, *, field: str) -> uuid.UUID:
    """Parse a uuid a tool was given, or say which argument was wrong.

    Tools take ids as strings because that is what a JSON tool call carries,
    and the error a model needs back names the argument rather than quoting a
    parser.

    Raises:
        ValueError: When it is not a uuid.
    """
    try:
        return uuid.UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a uuid, got {value!r}") from exc


def as_date(value: str, *, field: str) -> dt.date:
    """Parse an ISO date a tool was given, or say which argument was wrong.

    Raises:
        ValueError: When it is not an ISO date.
    """
    try:
        return dt.date.fromisoformat(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(
            f"{field} must be an ISO date (YYYY-MM-DD), got {value!r}"
        ) from exc


def as_datetime(value: str, *, field: str) -> dt.datetime:
    """Parse an ISO datetime a tool was given.

    A naive value is refused rather than assumed to be UTC: the one caller
    here is a model reasoning about the athlete's local clock, and guessing
    its timezone is how a proposal expires a day early.

    Raises:
        ValueError: When it is not an ISO datetime, or carries no timezone.
    """
    try:
        parsed = dt.datetime.fromisoformat(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO datetime, got {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(
            f"{field} must carry a timezone offset (e.g. "
            "2026-08-17T18:00:00+02:00), so it means one moment everywhere"
        )
    return parsed

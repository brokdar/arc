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

from app.domain.activity import parse_timezone
from app.domain.anchors import AnchorType, AnchorVersion
from app.domain.athlete import AthleteProfile
from app.domain.criteria import criteria_to_json
from app.domain.purpose import discipline_of
from app.domain.templates import PurposeTemplate
from app.domain.wellness import (
    INPUT_TIERS,
    INVALIDATES_MARKERS,
    MAX_BACKFILL_DAYS,
    SUBJECTIVE_SCALES,
    BodyRegion,
    Confounder,
    HrvContext,
    WeightInForce,
    WellnessProvenance,
    WellnessSource,
)
from app.domain.wellness_baseline import (
    Abstention,
    Baseline,
    Count,
    MetricTrend,
    Readiness,
)
from app.domain.workout import workout_body_to_json
from app.domain.zones import Zone
from app.ingest.repricing import RepricePrediction, RepriceReport
from app.persistence.activity import LoggedSetRow, SessionRow, session_duration_s
from app.persistence.agent_notes import AgentNoteRow
from app.persistence.anchors import AnchorVersionRow
from app.persistence.exercises import ExerciseRow
from app.persistence.matching import SessionMatchRow
from app.persistence.metrics import SessionMetricsRow
from app.persistence.proposals import PlanProposalRow
from app.persistence.scoring import (
    SessionAlignmentRow,
    SessionScoreRow,
    VerdictDeclarationRow,
)
from app.persistence.wellness import WellnessDayRow
from app.persistence.wellness_prompt import WellnessPromptRow
from app.persistence.workouts import WorkoutRow
from app.services.connections import (
    FeedStatus,
    IngestStatus,
    IntegrationIngestStatus,
)
from app.services.history import HistorySummary, HistoryWeek
from app.services.metrics import MetricSummary, measured_channels
from app.services.plan import CompletedSession, PlanWeek, WeekSession
from app.services.proposals import ProposalOutcome
from app.services.wellness import (
    DayResult,
    WellnessTrend,
    WellnessWeek,
    WellnessWeeks,
)
from app.services.workouts import WorkoutDraft
from app.services.zones import ResolvedZones


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


def athlete(
    profile: AthleteProfile, *, today: dt.date, timezone: str
) -> dict[str, Any]:
    """The athlete profile the agent plans against.

    ``today`` and ``timezone`` are the same clock, passed in rather than read
    here: this is a projection, and a view that asked the process what day it
    was would be the fourth such answer in one payload (issue #62). The zone is
    on the profile because every bare date the surface returns is a day on it,
    and the agent cannot tell that from the payload otherwise.
    """
    return {
        "name": profile.name,
        "sex": profile.sex.value,
        "date_of_birth": (
            None if profile.date_of_birth is None else profile.date_of_birth.isoformat()
        ),
        #: On the athlete's own clock, from `timezone` — a birthday arrives
        #: when it arrives for them, not when it arrives in Greenwich.
        "age": profile.age_on(today),
        #: The athlete's home timezone (`MATCHING__TIMEZONE`): the clock every
        #: `date`, `local_date`, `effective_date` and `plan_week` on this
        #: surface is on, and the one to render any `*_at` instant in.
        "timezone": timezone,
        #: Today on that clock. What "this week" and "yesterday" mean in
        #: anything the athlete says.
        "today": today.isoformat(),
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


def reprice(report: RepriceReport) -> dict[str, Any]:
    """What appending an anchor version did to the recorded history."""
    return {
        "examined": report.examined,
        "repriced": report.repriced,
        "unchanged": report.unchanged,
        "failed": report.failed,
        "note": report.note,
    }


def reprice_prediction(prediction: RepricePrediction) -> dict[str, Any]:
    """What a dry-run append **would** do — nothing was recomputed."""
    return {
        "examined": prediction.examined,
        "would_reprice": prediction.would_reprice,
        "unchanged": prediction.unchanged,
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


def week_completed_session(entry: CompletedSession) -> dict[str, Any]:
    """One recorded session on the week, in `session_summary`'s vocabulary.

    The same field names, so a ride reads the same way here as it does in
    `list_sessions` and the agent never has to learn a second spelling of a
    session. Fewer of them: this is a line in a week, and everything a session
    read adds — the start time, the context, the RPE — is one
    `get_session_detail` away by the `id` this carries.

    ``discipline`` is the one value that can differ from the same session's row
    in `list_sessions`, and deliberately: it is the **planning** discipline the
    week totals this recording under, so a sport nothing is ever planned as (a
    walk, a swim) is null here where the session list says `other`. A week is
    organised by what can be planned; the session list is not.
    """
    return {
        "id": str(entry.id),
        "local_date": entry.date.isoformat(),
        "discipline": None if entry.discipline is None else entry.discipline.value,
        "duration_s": entry.duration_s,
        "training_load": entry.load,
        "match_status": entry.match_status.value,
    }


def plan_week(week: PlanWeek) -> dict[str, Any]:
    """One Monday-to-Sunday plan week, flattened.

    The day grid is dropped and the sessions are listed with their dates: an
    empty Wednesday is a fact the reader can see from the dates, and seven
    nested objects to say it is six of them saying nothing.

    `unplanned_sessions` is the other half of the account (#49): every
    recording in the window that no card here references, with its id. A
    matched ride is **never** in both — the card carries it as
    `matched_session_id`, and a session listed twice is a week that reads as
    busier than it was. Between the two, every session
    `completed_session_count` counts is named somewhere, so "2.8 hours
    happened" is never an answer the agent has to go and join
    `list_sessions` to understand.

    **The week's agent notes are not in here.** A view renders one domain
    object, and a `PlanWeek` is the plan; the notes are a second read from a
    second service, so `get_plan_week` assembles them as a sibling block the
    way `get_session_detail` assembles `wellness` beside `metrics`. Threading
    them through this function would make the projection of the plan depend on
    a service the plan knows nothing about.
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
        "unplanned_sessions": [
            week_completed_session(entry) for entry in week.unplanned_sessions
        ],
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


def start_time_local(start_time: dt.datetime, timezone: str) -> str:
    """One session's start on the athlete's own clock, offset included.

    Resolved through `app.domain.activity.parse_timezone` — the single
    resolver `local_date` is already derived through — rather than by doing
    arithmetic on an offset string here. That is what makes an IANA zone come
    out right across a DST boundary, and it is deliberately **not** guarded:
    the stored timezone is validated at every write boundary, so a value this
    cannot resolve is a corrupt row and should say so rather than quietly
    render a UTC time as if it were local.

    The offset stays on the string. A naive local timestamp would repeat the
    mistake this field exists to fix, in the other direction.
    """
    return start_time.astimezone(parse_timezone(timezone)).isoformat()


def session_summary(row: SessionRow, summary: MetricSummary | None) -> dict[str, Any]:
    """One recorded session in a list.

    ``local_date`` rather than the UTC timestamp is the day the athlete would
    name, and the day the plan places work on — the two must agree or a
    question about "Tuesday" has two answers.

    ``start_time`` and ``start_time_local`` are the same instant twice, and
    both are here on purpose. The UTC one is what orders two sessions ridden
    in two timezones, so re-rendering it in the session's own offset — the
    other reading the issue offered — would cost the only field that compares
    across them. The local one is what the athlete's own clock said, so a
    coach reading a session planned for "06:30, window non-negotiable" does
    not have to add an offset to a UTC instant to find out whether they made
    it. Answering with the date already local and the time still UTC was the
    asymmetry that produced a wrong statement to the athlete.
    """
    return {
        "id": str(row.id),
        "local_date": row.local_date.isoformat(),
        "start_time": row.start_time.isoformat(),
        "start_time_local": start_time_local(row.start_time, row.timezone),
        "timezone": row.timezone,
        "discipline": row.discipline.value,
        "recording_kind": row.recording_kind.value,
        "match_status": row.status.value,
        "context": row.session_context.value,
        "duration_s": session_duration_s(row),
        "training_load": None if summary is None else summary.training_load,
        "rpe": row.rpe,
        "temperature_c": row.temperature_c,
    }


def logged_set(row: LoggedSetRow) -> dict[str, Any]:
    """One set of a manually recorded session, as it will be read back.

    No row id: nothing reads a set by id on this surface, and on a dry run
    there is none to show.
    """
    return {
        "set_index": row.set_index,
        "exercise_id": row.exercise_id,
        "exercise_name": row.exercise_name,
        "reps": row.reps,
        "duration_s": row.duration_s,
        "per_side": row.per_side,
        "load_kg": row.load_kg,
        "rir": row.rir,
        "notes": row.notes,
    }


def manual_session_draft(row: SessionRow) -> dict[str, Any]:
    """The session `record_manual_session` *would* store — the dry run's answer.

    Rendered from the same transient row the real call persists, so the dry
    run and the write cannot drift. No ``id`` and no ``match_status``:
    nothing was written, so there is nothing to reference and nothing has
    been matched.
    """
    return {
        "start_time": row.start_time.isoformat(),
        "start_time_local": start_time_local(row.start_time, row.timezone),
        "local_date": row.local_date.isoformat(),
        "timezone": row.timezone,
        "discipline": row.discipline.value,
        "duration_s": row.duration_s,
        "rpe": row.rpe,
        "temperature_c": row.temperature_c,
        "notes": row.notes,
        "sets": [logged_set(entry) for entry in row.logged_sets],
    }


def metrics(summary: MetricSummary | None, row: SessionMetricsRow | None) -> Any:
    """The computed metrics of one session, or null if it has none.

    The measured channels — `max_hr` through the three temperatures — are
    plain numbers or ``null``, not the `{value, explanation, not_assessed}`
    object the REST artefact serves. REST answers a UI that renders *why* a
    channel is absent; the agent asks "what did this ride touch", and three
    keys per number would triple the block to carry a reason it does not act
    on. ``null`` therefore says only "not measured" — which is still never a
    zero, and is why every key is present even for a session with no streams.
    """
    if summary is None or row is None:
        return None
    channels = measured_channels(row)
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
        "max_hr": channels.max_hr,
        "max_power": channels.max_power,
        "average_cadence": channels.average_cadence,
        "max_cadence": channels.max_cadence,
        "distance_km": summary.distance_km,
        "elevation_gain_m": channels.elevation_gain_m,
        "average_temp_c": channels.average_temp_c,
        "min_temp_c": channels.min_temp_c,
        "max_temp_c": channels.max_temp_c,
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


def workout_detail(row: WorkoutRow, *, step_count: int | None) -> dict[str, Any]:
    """One library workout **with** its prescription document.

    The one projection that carries ``structure``, and it carries it verbatim
    from storage: this is the authoring reference, and a paraphrase of the
    document is exactly what an agent must not imitate. Everything else
    matches :func:`workout`, so a row from the list and the same row in
    detail never disagree.
    """
    return {**workout(row, step_count=step_count), "structure": dict(row.structure)}


def exercise(row: ExerciseRow) -> dict[str, Any]:
    """One catalogue movement.

    ``id`` is the slug — the exact string a strength structure's
    ``exercise_id`` must carry — not a uuid, and that is the point of the
    catalogue: the identifier is stable and readable across every deployment.
    """
    return {
        "id": row.id,
        "name": row.name,
        "category": row.category.value,
        "unilateral": row.unilateral,
    }


#: The channel label each anchor type's zones govern — the same vocabulary
#: `metrics.zone_channel` answers with, so a zone read and a time-in-zone
#: read join without a translation table.
ZONE_CHANNEL: dict[AnchorType, str] = {
    AnchorType.FTP: "power",
    AnchorType.LTHR: "hr",
}


def zone(band: Zone) -> dict[str, Any]:
    """One half-open zone band ``[lower, upper)``."""
    return {
        "index": band.index,
        "name": band.name,
        "lower_pct": band.lower_pct,
        "upper_pct": band.upper_pct,
        "lower": band.lower,
        "upper": band.upper,
        "unit": band.unit.value,
    }


def zones(resolved: ResolvedZones) -> dict[str, Any]:
    """One channel's zones, with the two inputs every number derives from.

    The anchor version rides along whole because it is the provenance: a zone
    list without it is a copy waiting to go stale, which is the exact failure
    `get_zones` exists to end.
    """
    row = resolved.anchor_version
    return {
        "channel": ZONE_CHANNEL[row.anchor_type],
        "anchor_type": row.anchor_type.value,
        "model": resolved.model.value,
        "anchor": anchor(row),
        "zones": [zone(band) for band in resolved.zones],
    }


def zones_unavailable(anchor_type: AnchorType) -> dict[str, Any]:
    """A channel whose anchor has no version in force yet.

    An answer rather than a refusal: "there are no heart-rate zones yet" is a
    fact about the athlete's record, and hiding it behind a `not_found` would
    cost the caller the channel that *does* exist.
    """
    return {
        "channel": ZONE_CHANNEL[anchor_type],
        "anchor_type": anchor_type.value,
        "model": None,
        "anchor": None,
        "zones": None,
        "note": (
            f"no {anchor_type.value} version is in force, so this channel has "
            "no zones yet; they exist the moment one is appended"
        ),
    }


def purpose_template(template: PurposeTemplate) -> dict[str, Any]:
    """One purpose, with what it starts with and how it is judged.

    ``default_criteria`` are serialized by the domain's own encoder — the same
    wire form a planned session's ``success_criteria`` carry in a proposal
    diff — so what this read shows is literally what an unoverridden session
    will be scored against.
    """
    return {
        "purpose": template.purpose.value,
        "discipline": discipline_of(template.purpose).value,
        "description": template.description,
        "axes": [axis.value for axis in template.axes],
        "default_criteria": criteria_to_json(template.default_criteria),
    }


def workout_draft(draft: WorkoutDraft) -> dict[str, Any]:
    """The workout a create *would* add — the dry run's answer.

    No `id`, for the reason :func:`anchor_draft` has none: nothing was written.
    ``tags`` and ``structure`` are the **normalized** forms the write would
    store — tags cleaned, the document re-serialized from the parsed
    prescription with every default filled in — not what the caller sent, so
    a dry run and the call after it agree about what is in the library, and
    the caller sees how their document was interpreted before writing it.
    """
    return {
        "name": draft.name,
        "description": draft.description,
        "discipline": draft.discipline.value,
        "folder": draft.folder,
        "tags": list(draft.tags),
        "step_count": draft.step_count,
        "structure": workout_body_to_json(draft.body),
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
    """One agent note. ``id`` and ``created_at`` are null on a dry run.

    Rendered **whole** wherever it appears — as the answer to the write that
    made it, in `get_session_detail`'s ``agent_notes``, in `get_plan_week`'s —
    including ``session_id`` and ``plan_week`` even where the block it came
    back in already fixes them. One note has one rendering: a trimmed
    per-context variant would mean a reader comparing two calls has to diff
    two shapes to decide whether they are looking at the same note.
    """
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
    """One stored plan-change proposal, without its diff.

    Enough to answer the weekly-review question — did it land, and if not,
    how did it die — and to decide whether to drill in with `get_proposal`.
    ``resolution_note`` is on the summary rather than behind the detail
    because it is the athlete's own words on a rejection, and a coach that
    has to ask a second question to hear "no, and here is why" mostly won't.
    """
    return {
        "id": str(row.id),
        "status": row.status.value,
        "rationale": row.rationale,
        "change_count": len(row.changes),
        "expires_at": row.expires_at.isoformat(),
        "created_at": row.created_at.isoformat(),
        "created_by": row.created_by,
        "resolved_at": None if row.resolved_at is None else row.resolved_at.isoformat(),
        "resolution_note": row.resolution_note,
        "supersedes_id": (
            None if row.supersedes_id is None else str(row.supersedes_id)
        ),
        "superseded_by_id": (
            None if row.superseded_by_id is None else str(row.superseded_by_id)
        ),
    }


def proposal_detail(row: PlanProposalRow) -> dict[str, Any]:
    """One proposal in full: the summary plus its stored diff.

    ``diff`` is passed through verbatim, the way :func:`score` passes a stored
    payload through: it is the same document `propose_plan_change` computed
    and returned when the proposal was written, and a second rendering here
    would be a second thing to keep in step with the diff builder.
    """
    return {**proposal(row), "diff": list(row.diff)}


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


def as_time(value: str, *, field: str) -> dt.time:
    """Parse a wall-clock time a tool was given, or say which argument was wrong.

    No date and no offset: these are the clock times a watch exports, and the
    day they belong to is the wellness day's own (see
    `app.domain.wellness.wellness_day_date`).

    Raises:
        ValueError: When it is not an ISO time.
    """
    try:
        return dt.time.fromisoformat(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(
            f"{field} must be a clock time (HH:MM or HH:MM:SS) with no date "
            f"and no offset, got {value!r}"
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


# --- wellness -----------------------------------------------------------------


def wellness_day(row: WellnessDayRow, *, subjective_recalled: bool) -> dict[str, Any]:
    """One recorded day, whole.

    ``markers`` rides on the **same object as the readings** rather than in a
    block of its own, because a coach that has to remember to look elsewhere
    for last night's beer will one day not remember, and the cost is a training
    week. The numbers are still all here: what is withheld is their standing as
    evidence today, not the values.
    """
    day = row.to_domain()
    standing = day.standing
    return {
        "local_date": day.local_date.isoformat(),
        "sleep_duration_s": day.sleep_duration_s,
        "sleep_start_local": (
            None if day.sleep_start_local is None else day.sleep_start_local.isoformat()
        ),
        "sleep_end_local": (
            None if day.sleep_end_local is None else day.sleep_end_local.isoformat()
        ),
        "sleep_quality": day.sleep_quality,
        "resting_hr_bpm": day.resting_hr_bpm,
        "hrv_ms": day.hrv_ms,
        "hrv_metric": None if day.hrv_metric is None else day.hrv_metric.value,
        "hrv_context": None if day.hrv_context is None else day.hrv_context.value,
        "respiratory_rate_brpm": day.respiratory_rate_brpm,
        "spo2": day.spo2,
        "wrist_temperature_delta_c": day.wrist_temperature_delta_c,
        "weight_kg": day.weight_kg,
        "fatigue": day.fatigue,
        "soreness": day.soreness,
        "stress": day.stress,
        "motivation": day.motivation,
        "soreness_by_region": {
            region.value: rating for region, rating in day.soreness_by_region.items()
        },
        "confounders": [member.value for member in day.confounders],
        "note": day.note,
        "markers": {
            "actionable": standing.actionable,
            "invalidated_by": [member.value for member in standing.invalidated_by],
            "statement": standing.statement,
        },
        #: The subjective ratings were entered from memory. The objective
        #: readings are not discounted for it — the watch measured them on the
        #: day, whatever day they were typed in.
        "subjective_recalled": subjective_recalled,
        #: Where the numbers came from, and who wrote them down. Both, always:
        #: a coach that cannot tell the athlete's report from its own
        #: transcription will eventually cite its own echo back as evidence.
        "provenance": day.provenance.value,
        "source": day.source.value,
        "created_at": row.created_at.isoformat(),
    }


#: The fields the compact series carries — the `valuable` tier of
#: `app.domain.wellness.INPUT_TIERS`, which is exactly the set the coach's
#: morning question turns on. Everything else is a whole-day read away.
COMPACT_FIELDS = (
    "sleep_duration_s",
    "resting_hr_bpm",
    "hrv_ms",
    "weight_kg",
    "fatigue",
    "motivation",
)


def wellness_day_compact(
    row: WellnessDayRow, *, subjective_recalled: bool
) -> dict[str, Any]:
    """One day, cut down to what the one-call opener can afford.

    `get_coaching_context` is the call every session begins with, and seven
    full day objects at twenty-odd fields each is a hundred and forty fields
    spent before the coach has asked anything. So the series it carries holds
    the six `valuable`-tier inputs, the confounder standing when there is one,
    and nothing else — a field that was not reported is **absent** rather than
    null, because a null is a token spent saying nothing.

    **``source`` and ``provenance`` appear only when they are not the
    ordinary ones.** Every read has to let the coach tell the athlete's own
    report from its own transcription, or it will eventually cite its own echo
    back as evidence — but spelling `athlete` / `athlete_reported` on all seven
    days spends fourteen fields saying "as usual" on the one call every session
    begins with. So the default is silence and a departure from it is stated,
    which is the convention this object already uses for the confounder
    standing and the recall flag. The rule is written into
    `get_coaching_context`'s own docstring, so the agent reads it before the
    data.

    Whole days are `get_wellness`, which spells both out on every day.
    """
    whole = wellness_day(row, subjective_recalled=subjective_recalled)
    compact: dict[str, Any] = {"local_date": whole["local_date"]}
    compact.update(
        {name: whole[name] for name in COMPACT_FIELDS if whole[name] is not None}
    )
    if not whole["markers"]["actionable"]:
        compact["not_actionable"] = whole["markers"]["invalidated_by"]
    if subjective_recalled:
        compact["subjective_recalled"] = True
    if row.source is not WellnessSource.ATHLETE:
        compact["source"] = row.source.value
    if row.provenance is not WellnessProvenance.ATHLETE_REPORTED:
        compact["provenance"] = row.provenance.value
    return compact


def weight_in_force(resolved: WeightInForce | None) -> Any:
    """The weight governing a date, or null before the first one was recorded.

    Null is the answer, not a gap: watts per kilogram is then **absent** rather
    than computed against a default weight that is nobody's.
    """
    if resolved is None:
        return None
    return {
        "weight_kg": resolved.weight_kg,
        "effective_date": resolved.effective_date.isoformat(),
    }


def wellness_inputs() -> dict[str, Any]:
    """The whole vocabulary of the wellness surface, self-describing.

    Tiers, scales with their polarity and anchor words, the confounder
    vocabulary with its invalidating half marked, and the body regions. It
    exists so the agent never discovers a vocabulary by submitting guesses and
    reading the refusals.
    """
    return {
        "tiers": [
            {"field": name, "tier": tier.value} for name, tier in INPUT_TIERS.items()
        ],
        "scales": [
            {
                "field": scale.field,
                "low": scale.low,
                "high": scale.high,
                #: Load-bearing: 5 motivation is good and 5 fatigue is not, so
                #: no reader may assume a direction. `higher_is_neither` is
                #: session RPE — a 9 is a hard session, not a bad one.
                "polarity": scale.polarity.value,
                "prompt": scale.prompt,
                "anchors": {
                    str(point): label for point, label in sorted(scale.anchors.items())
                },
            }
            for scale in SUBJECTIVE_SCALES.values()
        ],
        "confounders": [
            {
                "value": member.value,
                "invalidates_markers": member in INVALIDATES_MARKERS,
            }
            for member in Confounder
        ],
        "body_regions": [member.value for member in BodyRegion],
        "max_backfill_days": MAX_BACKFILL_DAYS,
    }


def wellness_week(week: WellnessWeek) -> dict[str, Any]:
    """One folded week. Every mean carries the ``n`` it was computed over."""
    return {
        "start": week.start.isoformat(),
        "end": week.end.isoformat(),
        "days_recorded": week.days_recorded,
        "days_invalidated": week.days_invalidated,
        "days_recalled": week.days_recalled,
        "metrics": [
            {"metric": mean.metric, "mean": round(mean.mean, 3), "n": mean.n}
            for mean in week.metrics
        ],
    }


def wellness_weeks(summary: WellnessWeeks) -> dict[str, Any]:
    """A date range of reported wellness, folded into weeks."""
    return {
        "start": summary.start.isoformat(),
        "end": summary.end.isoformat(),
        "weeks": [wellness_week(week) for week in summary.weeks],
    }


def wellness_day_result(result: DayResult) -> dict[str, Any]:
    """What one day of a write was, or would have been.

    ``changed`` is the before/after diff the audit row carries, returned so a
    dry run shows what a real call would actually move — an empty diff means
    the day already said exactly this, which is a legal and unremarkable thing
    for a re-run of an import to find.
    """
    return {
        "local_date": result.local_date.isoformat(),
        "outcome": result.outcome.value,
        "changed": dict(result.changed),
    }


def wellness_baseline(value: Baseline | Abstention) -> dict[str, Any]:
    """A baseline, or an abstention that names what it still needs.

    **An immature baseline carries no ``mean``, no ``band`` and no
    ``deviation_sd`` key at all.** Not null — absent. A null in a number's slot
    is a zero to the next reader, and a caveat beside a number is advice a
    model under pressure to be helpful will drop. What the abstention does
    carry is both counts and its own unlock condition, which is something a
    coach can act on.
    """
    if isinstance(value, Abstention):
        return {
            "kind": "abstention",
            "mature": False,
            "metric": value.metric.value,
            **_context(value.hrv_context),
            "readings": _count(value.readings),
            "span_days": _count(value.span_days),
            "reason": value.reason,
        }
    answer: dict[str, Any] = {
        "kind": "banded" if value.band is not None else "trend",
        "mature": True,
        "metric": value.metric.value,
        **_context(value.hrv_context),
        #: `ln` for HRV, `linear` for everything else. Every statistic on this
        #: object is in it, so `deviation_sd` is `(rolling_mean_7d - mean) / sd`
        #: on the numbers as printed.
        "space": value.space.value,
        "unit": value.unit,
        #: Readings **after** exclusions: a confounder-voided day and a recalled
        #: rating are not in it, so a thin `n` has a visible reason.
        "n": value.n,
        "span_days": value.span_days,
        "mean": round(value.mean, 4),
        "mean_native": round(value.mean_native, 4),
        "sd": round(value.sd, 4),
        "cv": None if value.cv is None else round(value.cv, 4),
        "trend_per_week": round(value.trend.per_week, 4),
    }
    if value.band is not None:
        answer["band"] = {
            "low": round(value.band.low, 4),
            "high": round(value.band.high, 4),
            "half_width": round(value.band.half_width, 4),
            "low_native": round(value.band.low_native, 4),
            "high_native": round(value.band.high_native, 4),
        }
        answer["deviation_sd"] = (
            None if value.deviation_sd is None else round(value.deviation_sd, 3)
        )
        answer["direction"] = None if value.direction is None else value.direction.value
    return answer


def _context(context: HrvContext | None) -> dict[str, Any]:
    """Name the HRV context a baseline reports on, when there is one."""
    return {} if context is None else {"hrv_context": context.value}


def _count(count: Count) -> dict[str, Any]:
    """A count against its bar, with the `have of need` line spelled out."""
    return {"have": count.have, "need": count.need, "statement": str(count)}


def wellness_metric_trend(found: MetricTrend) -> dict[str, Any]:
    """One metric's dated readings, seven-day mean and baseline."""
    return {
        "metric": found.metric.value,
        "unit": found.unit,
        "space": found.space.value,
        "series": [
            {
                "local_date": point.local_date.isoformat(),
                #: Null on a date with no reading — never zero and never
                #: interpolated. The gap is the honest picture.
                "value": point.value,
                **(
                    {}
                    if point.standing is None
                    else {
                        "markers": {
                            "actionable": point.standing.actionable,
                            "invalidated_by": [
                                member.value for member in point.standing.invalidated_by
                            ],
                            "statement": point.standing.statement,
                        }
                    }
                ),
            }
            for point in found.series
        ],
        "today": found.today,
        "rolling_mean_7d": {
            "mean": (
                None
                if found.rolling_mean_7d.mean is None
                else round(found.rolling_mean_7d.mean, 4)
            ),
            "mean_native": (
                None
                if found.rolling_mean_7d.mean_native is None
                else round(found.rolling_mean_7d.mean_native, 4)
            ),
            #: Never absent. A seven-day mean over three readings and one over
            #: seven are different objects.
            "n": found.rolling_mean_7d.n,
        },
        "baseline": wellness_baseline(found.baseline),
        **(
            {}
            if not found.by_context
            else {
                "by_context": {
                    context.value: wellness_baseline(value)
                    for context, value in found.by_context.items()
                }
            }
        ),
    }


def wellness_readiness(projection: Readiness) -> dict[str, Any]:
    """How many markers are outside their band, which, and which way.

    **A count, never a score.** There is no `readiness_score` here, no
    `recommendation` and no `verdict`, and `test_readiness_field_inventory`
    fails if one ever appears. Whether today is a day to train is the coach's
    call, made out loud, with the confounders and the gaps visible.
    """
    outside = projection.markers_outside_band
    answer: dict[str, Any] = {
        "as_of": projection.as_of.isoformat(),
        "markers_outside_band": {
            "count": outside.count,
            #: The denominator excludes markers whose baseline is immature and
            #: says so: `2 of 4`, not `2 of 5`.
            "of": outside.of,
            "statement": str(outside),
            "markers": [
                {
                    "metric": marker.metric.value,
                    "direction": marker.direction.value,
                    "deviation_sd": round(marker.deviation_sd, 3),
                }
                for marker in outside.markers
            ],
        },
    }
    if projection.joint_state is not None:
        answer["joint_state"] = {
            "key": projection.joint_state.key.value,
            "label": projection.joint_state.label,
            "hrv_deviation_sd": round(projection.joint_state.hrv_deviation_sd, 3),
            "resting_hr_deviation_sd": round(
                projection.joint_state.resting_hr_deviation_sd, 3
            ),
        }
    return answer


def wellness_trend(trend: WellnessTrend) -> dict[str, Any]:
    """The whole trend read: metrics keyed by name, plus the projection."""
    return {
        "start": trend.start.isoformat(),
        "end": trend.end.isoformat(),
        "as_of": trend.as_of.isoformat(),
        "metrics": {
            name.value: wellness_metric_trend(found)
            for name, found in trend.metrics.items()
        },
        "readiness": wellness_readiness(trend.readiness),
    }


def wellness_prompt(row: WellnessPromptRow | None) -> Any:
    """The standing of today's question, or null when nobody was asked.

    Null is an answer and not a gap, the same way `weight_in_force` is null
    before the first weigh-in. What this field exists for is the difference
    between the three states: a coach that cannot tell "the athlete felt fine"
    from "nobody asked" will read an empty morning as assent, which is the one
    reading of silence this surface is built to prevent.

    `expired` is therefore a *fact* and not a missing answer: we asked, the
    window closed, and no follow-up was raised.
    """
    if row is None:
        return None
    return {
        "local_date": row.local_date.isoformat(),
        "status": row.status.value,
        "expires_at": row.expires_at.isoformat(),
        "resolved_at": None if row.resolved_at is None else row.resolved_at.isoformat(),
    }


def ingest_status(status: IngestStatus) -> dict[str, Any]:
    """Whether arc's supply of activity files is working, and from where.

    Keyed on `integrations` and not on folders: the coach's sentence is
    "Wahoo has stopped delivering", and a flat list of remote paths makes it
    unsayable. There is deliberately **no** `feeds` key any more — a second
    spelling of the same folders would let the two disagree.

    `local_inbox_only` is the shape that keeps the answer honest when there is
    nothing connected: a list holding only the local drop reads like every
    source broke, and the two configurations need different conclusions from a
    coach looking at a thin week.
    """
    return {
        "integrations": [_integration_status(row) for row in status.integrations],
        "local_inbox_only": status.local_inbox_only,
    }


def _integration_status(integration: IntegrationIngestStatus) -> dict[str, Any]:
    """One source: what it brings in, and how each of its folders is doing."""
    return {
        "kind": None if integration.kind is None else integration.kind.value,
        "display_name": integration.display_name,
        "data_kinds": [kind.value for kind in integration.data_kinds],
        "folders": [_feed_status(feed) for feed in integration.folders],
    }


def _feed_status(feed: FeedStatus) -> dict[str, Any]:
    """One watched folder: where it points, and what has come through it."""
    return {
        "feed_id": str(feed.feed_id),
        "folder": feed.folder or "/",
        "enabled": feed.enabled,
        "state": feed.state.value,
        "last_delivery_at": (
            feed.last_delivery_at.isoformat() if feed.last_delivery_at else None
        ),
        "deliveries_7d": feed.deliveries,
        "last_error": feed.last_error,
        "connection_status": feed.connection_status.value,
        "account_label": feed.account_label,
    }

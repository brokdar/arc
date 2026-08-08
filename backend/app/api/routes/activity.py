"""HTTP endpoints for completed sessions. Thin over `app.services.activity`.

Named `activity`, not `sessions`, for the reason D92 gives: `sessions` in this
codebase means the *planned* one, and no name is used for both. The routes it
serves are `/api/v1/sessions` — the athlete-facing noun, where there is no
ambiguity to protect against.

Manual entry lives at `/api/v1/manual-sessions` rather than
`/api/v1/sessions/manual`: a facet of the collection under the id namespace
shadows `GET /sessions/{id}`, which then answers 422 about uuid syntax where
405 is the truth (`.claude/rules/api-collection-facets.md`, D50).

`GET /sessions/{id}` answers with the session, the metadata of the recordings
behind it — sources, stops, repairs — and the **current metric version**. The
per-second samples are a separate resource (`/sessions/{id}/streams`): they are
1-2 MB for a long ride, and every page that merely lists sessions would pay for
them otherwise.

Recompute is `POST /sessions/{id}/metrics/recompute` — a sub-resource of one
member, which has one more path segment than the id route and therefore
collides with nothing (`.claude/rules/api-collection-facets.md`). It appends a
version; it never overwrites one.
"""

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic.json_schema import SkipJsonSchema

from app.api.deps import ActorDep
from app.api.pagination import PageParamsDep
from app.api.routes.matching import to_summary
from app.api.schemas.activity import (
    LoggedSetRead,
    ManualSessionCreate,
    RecordingRead,
    RecordingStopRead,
    SessionListItem,
    SessionRead,
    SessionsPage,
    SessionUpdate,
)
from app.api.schemas.matching import SessionMerge
from app.api.schemas.metrics import (
    AnchorPinRead,
    MetricsRecompute,
    SessionMetricsRead,
    SessionStreamsRead,
    StreamAnomalyRead,
    StreamChannelRead,
    StreamStopRead,
)
from app.core.exceptions import ErrorDetail, ValidationErrorDetail
from app.domain.activity import SessionDiscipline
from app.domain.anchors import AnchorType
from app.domain.streams import AnomalyKind
from app.ingest.analysis import SessionAnalyser, SessionStreams, load_streams
from app.persistence.activity import (
    RecordingRepository,
    RecordingRow,
    SessionRow,
    session_duration_s,
)
from app.persistence.anchors import AnchorVersionRow
from app.persistence.db import SessionDep
from app.persistence.matching import SessionMatchRow
from app.persistence.metrics import SessionMetricsRow
from app.services.activity import LoggedSetInput, SessionService
from app.services.matching import MatchingService
from app.services.metrics import SessionMetricsService

router = APIRouter(prefix="/sessions", tags=["sessions"])
#: Manual entry, outside the id namespace — see the module docstring.
manual_router = APIRouter(prefix="/manual-sessions", tags=["sessions"])

type Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {404: {"model": ErrorDetail, "description": "No such session"}}
BAD_BODY: Responses = {400: {"model": ErrorDetail, "description": "Malformed body"}}
INVALID: Responses = {
    422: {
        "model": ValidationErrorDetail,
        "description": "The session violates a schema or domain rule",
    }
}


def get_service(session: SessionDep) -> SessionService:
    """Bind the service to a request-scoped session."""
    return SessionService.from_session(session)


def get_metrics(session: SessionDep) -> SessionMetricsService:
    """Bind the metric-artefact service to a request-scoped session."""
    return SessionMetricsService.from_session(session)


def get_matching(session: SessionDep) -> MatchingService:
    """Bind the matching service to a request-scoped session."""
    return MatchingService.from_session(session)


def get_analyser(session: SessionDep) -> SessionAnalyser:
    """Bind the stream-reading analyser to a request-scoped session.

    In `app.ingest` rather than in a service because it reads parquet, which
    the service layer may not (import-linter enforces the direction). A route
    is allowed to reach the ingest layer, and `routes/ingest.py` already does.
    """
    return SessionAnalyser.from_session(session)


ServiceDep = Annotated[SessionService, Depends(get_service)]
MetricsDep = Annotated[SessionMetricsService, Depends(get_metrics)]
MatchingDep = Annotated[MatchingService, Depends(get_matching)]
AnalyserDep = Annotated[SessionAnalyser, Depends(get_analyser)]

# `SkipJsonSchema[None]`: optional by omission, never `null` — see
# `.claude/rules/api-optional-query-params.md`.
StartFilter = Annotated[
    dt.date | SkipJsonSchema[None],
    Query(description="Earliest athlete-local date to include (inclusive)."),
]
EndFilter = Annotated[
    dt.date | SkipJsonSchema[None],
    Query(description="Latest athlete-local date to include (inclusive)."),
]
DisciplineFilter = Annotated[
    SessionDiscipline | SkipJsonSchema[None],
    Query(description="Restrict to one discipline; omit for all of them."),
]


def _duration(row: SessionRow) -> tuple[float, float | None]:
    """What to render as the session's length, and the load-bearing duration.

    A device session's length is its **recording time** — elapsed with the
    pauses taken out (A4.4) — because that is what training load is computed
    over and what the athlete would call the ride. A manual session has no
    recording and therefore no pauses, so its wall-clock duration is both
    answers, and the second one is null to say there was nothing to subtract.
    """
    duration = session_duration_s(row)
    return duration, (duration if row.recordings else None)


def _load(metrics: SessionMetricsRow | None) -> tuple[float | None, Any]:
    """The selected load and its basis, from an artefact that may not exist.

    Three states collapse to ``(None, None)`` on a list row and they are not
    the same thing: no artefact yet, an artefact whose load block is
    `not_assessed`, and a load of zero (which cannot happen). The row keeps
    its slot for all of them and the detail endpoint carries the reason —
    a list is not where an explanation fits.
    """
    if metrics is None:
        return None, None
    load = metrics.payload.get("load")
    if not isinstance(load, dict):
        return None, None
    value = load.get("training_load")
    return (value if isinstance(value, float | int) else None), load.get("load_basis")


def to_list_item(
    row: SessionRow,
    metrics: SessionMetricsRow | None = None,
    link: SessionMatchRow | None = None,
) -> SessionListItem:
    """Project a stored session onto its list-row shape."""
    duration_s, recording_time_s = _duration(row)
    load, basis = _load(metrics)
    return SessionListItem(
        match=to_summary(link) if link is not None else None,
        id=row.id,
        local_date=row.local_date,
        start_time=row.start_time,
        timezone=row.timezone,
        discipline=row.discipline,
        classification_source=row.classification_source,
        discipline_overridden=row.discipline_overridden,
        recording_kind=row.recording_kind,
        status=row.status,
        duration_s=duration_s,
        recording_time_s=recording_time_s,
        rpe=row.rpe,
        load=load,
        load_basis=basis,
    )


def to_recording(row: RecordingRow, *, anomaly_count: int) -> RecordingRead:
    """Project a stored recording, its sources and its stops."""
    return RecordingRead(
        id=row.id,
        file_hash=row.file_hash,
        file_sport_index=row.file_sport_index,
        original_ext=row.original_ext,
        sport=row.sport,
        elapsed_time_s=row.elapsed_time_s,
        recording_time_s=row.recording_time_s,
        recording_stops=[
            RecordingStopRead(start_index=start, end_index=end)
            for start, end in row.recording_stops
        ],
        median_time_delta_s=row.median_time_delta_s,
        moving_time_s=row.moving_time_s,
        power_source_candidates=list(row.power_source_candidates),
        power_source=row.power_source,
        power_source_rule=row.power_source_rule,
        hr_source_candidates=list(row.hr_source_candidates),
        hr_source=row.hr_source,
        hr_source_rule=row.hr_source_rule,
        channels=list(row.channels),
        anomaly_count=anomaly_count,
        created_at=row.created_at,
    )


def to_pin(anchor_type: AnchorType, version: AnchorVersionRow) -> AnchorPinRead:
    """Render one pinned anchor version, resolved rather than as an id."""
    return AnchorPinRead(
        anchor_type=anchor_type,
        version_id=version.id,
        value=version.value,
        unit=version.unit.value,
        provenance=version.provenance,
        effective_date=version.effective_date,
        ci_low=version.ci_low,
        ci_high=version.ci_high,
    )


def to_metrics(
    row: SessionMetricsRow,
    pins: Sequence[tuple[AnchorType, AnchorVersionRow]],
) -> SessionMetricsRead:
    """Project one metric version, payload and pins together.

    The stored payload's keys are exactly this schema's field names — both
    come from `app.domain.session_analysis` — so it validates straight
    through. Extra keys are ignored, which is what lets an artefact written by
    an earlier version of the metric set still be read.
    """
    return SessionMetricsRead.model_validate(
        dict(row.payload)
        | {
            "version": row.version,
            "computed_at": row.as_of,
            "recompute_reason": row.recompute_reason,
            "pins": [to_pin(anchor_type, version) for anchor_type, version in pins],
            "power_zone_model": row.power_zone_model,
            "hr_zone_model": row.hr_zone_model,
        }
    )


def to_streams(streams: SessionStreams) -> SessionStreamsRead:
    """Project one session's stored samples onto the chart payload.

    `resampled_only` anomalies are dropped: they certify that a channel needed
    no repair, which is worth storing and is not something to mark on a chart.
    """
    return SessionStreamsRead(
        recording_id=streams.recording_id,
        recording_ids=list(streams.recording_ids),
        t0=streams.t0,
        length=streams.length,
        channels=[
            StreamChannelRead(
                channel=channel,
                source=streams.sources.get(channel),
                values=list(values),
            )
            for channel, values in sorted(
                streams.channels.items(), key=lambda item: item[0].value
            )
        ],
        recording_stops=[
            StreamStopRead(start_index=start, end_index=end)
            for start, end in streams.recording_stops
        ],
        anomalies=[
            StreamAnomalyRead(
                channel=anomaly.channel,
                start_index=anomaly.start_index,
                end_index=anomaly.end_index,
                kind=anomaly.kind,
                substituted_value=anomaly.substituted_value,
            )
            for anomaly in streams.anomalies
            if anomaly.kind is not AnomalyKind.RESAMPLED_ONLY
        ],
    )


def to_read(
    row: SessionRow,
    repairs: Mapping[uuid.UUID, int],
    metrics: SessionMetricsRead | None = None,
    link: SessionMatchRow | None = None,
) -> SessionRead:
    """Project a stored session with the recordings behind it."""
    duration_s, recording_time_s = _duration(row)
    return SessionRead(
        match=to_summary(link) if link is not None else None,
        id=row.id,
        local_date=row.local_date,
        start_time=row.start_time,
        end_time=row.end_time,
        timezone=row.timezone,
        discipline=row.discipline,
        classification_source=row.classification_source,
        discipline_overridden=row.discipline_overridden,
        recording_kind=row.recording_kind,
        status=row.status,
        duration_s=duration_s,
        recording_time_s=recording_time_s,
        rpe=row.rpe,
        # The list row's two columns, taken off the artefact the detail
        # already carries — so a row and the page it opens cannot disagree.
        load=metrics.load.training_load if metrics is not None else None,
        load_basis=metrics.load.load_basis if metrics is not None else None,
        notes=row.notes,
        recordings=[
            to_recording(recording, anomaly_count=repairs.get(recording.id, 0))
            for recording in row.recordings
        ],
        logged_sets=[
            LoggedSetRead.model_validate(logged) for logged in row.logged_sets
        ],
        created_at=row.created_at,
        updated_at=row.updated_at,
        metrics=metrics,
    )


async def one_to_read(
    service: SessionService,
    metrics: SessionMetricsService,
    matching: MatchingService,
    row: SessionRow,
) -> SessionRead:
    """Resolve one session's repairs, metrics and match link, and project it."""
    return to_read(
        row,
        await service.repair_counts([row]),
        await current_metrics(metrics, row.id),
        (await matching.for_sessions([row.id])).get(row.id),
    )


async def current_metrics(
    metrics: SessionMetricsService, session_id: uuid.UUID
) -> SessionMetricsRead | None:
    """The metric version in force for one session, rendered, or ``None``."""
    row = await metrics.get_current(session_id)
    if row is None:
        return None
    return to_metrics(row, (await metrics.pins([row])).get(row.id, []))


def _sets(payload: ManualSessionCreate) -> Sequence[LoggedSetInput]:
    """Turn the request's sets into the service's input values."""
    return [
        LoggedSetInput(
            reps=entry.reps,
            exercise_id=entry.exercise_id,
            exercise_name=entry.exercise_name,
            load_kg=entry.load_kg,
            rir=entry.rir,
            notes=entry.notes,
        )
        for entry in payload.sets
    ]


@router.get("")
async def list_sessions(
    service: ServiceDep,
    metrics: MetricsDep,
    matching: MatchingDep,
    page: PageParamsDep,
    start: StartFilter = None,
    end: EndFilter = None,
    discipline: DisciplineFilter = None,
) -> SessionsPage:
    """List completed sessions, newest first, optionally within a date range.

    A log rather than a calendar, so it reads backwards — the opposite of
    `GET /planned-sessions`, and deliberately: what happened is read from the
    most recent, what is planned is read forwards.
    """
    sessions, total = await service.list(
        start=start,
        end=end,
        discipline=discipline,
        offset=page.offset,
        limit=page.limit,
    )
    # One query for the whole page's artefacts, not one per row: the load
    # column is on every line, and a per-row lookup would scale with the page.
    current = await metrics.current_for_sessions(row.id for row in sessions)
    links = await matching.for_sessions([row.id for row in sessions])
    return SessionsPage(
        items=[
            to_list_item(session, current.get(session.id), links.get(session.id))
            for session in sessions
        ],
        total=total,
        offset=page.offset,
        limit=page.limit,
    )


@router.get("/{session_id}", responses=NOT_FOUND)
async def get_session(
    service: ServiceDep,
    metrics: MetricsDep,
    matching: MatchingDep,
    session_id: uuid.UUID,
) -> SessionRead:
    """Get one completed session, its recordings' metadata and its metrics.

    Not the samples: those are 1-2 MB and live at
    `GET /sessions/{id}/streams`.
    """
    return await one_to_read(service, metrics, matching, await service.get(session_id))


@router.patch("/{session_id}", responses=NOT_FOUND | BAD_BODY | INVALID)
async def update_session(
    service: ServiceDep,
    metrics: MetricsDep,
    matching: MatchingDep,
    actor: ActorDep,
    session_id: uuid.UUID,
    payload: SessionUpdate,
) -> SessionRead:
    """Correct a session's discipline or its timezone.

    A discipline override is recorded as one (`discipline_overridden`), so no
    later re-classification can quietly undo it. A timezone override
    **re-derives** `local_date`, which is the point of storing the zone rather
    than the offset that happened to be true once (D93).
    """
    row = await service.update(
        session_id, payload.model_dump(exclude_unset=True), actor=actor
    )
    return await one_to_read(service, metrics, matching, row)


@manual_router.post(
    "", status_code=status.HTTP_201_CREATED, responses=BAD_BODY | INVALID | NOT_FOUND
)
async def create_manual_session(
    service: ServiceDep,
    metrics: MetricsDep,
    matching: MatchingDep,
    actor: ActorDep,
    payload: ManualSessionCreate,
) -> SessionRead:
    """Record a session performed without a device file — a gym session (B-6).

    Produces the same session row a file would, with
    `recording_kind=manual` and no recording: WP-6 matches both with one
    query, and the sets are stored so WP-7's strength alignment has something
    to read.
    """
    row = await service.create_manual(
        actor=actor,
        start_time=payload.start_time,
        timezone=payload.timezone,
        duration_s=payload.duration_s,
        discipline=payload.discipline,
        rpe=payload.rpe,
        notes=payload.notes,
        sets=_sets(payload),
    )
    return await one_to_read(service, metrics, matching, row)


@router.get("/{session_id}/streams", responses=NOT_FOUND)
async def get_session_streams(
    service: ServiceDep, session: SessionDep, session_id: uuid.UUID
) -> SessionStreamsRead:
    """The per-second samples behind one session, for the charts.

    Its own resource because it is 1-2 MB for a long ride (A4.1: 14 400 rows
    per channel for four hours). Every channel is the **cleaned** column with
    its nulls intact — a recording stop is a break in the trace, not a run of
    zeros — and the anomaly regions come with it so the chart can mark what
    was repaired (A4.2).

    404 for a session that has no recording: a gym session typed in by hand
    never had samples, and the detail says so because that sentence is the
    empty state the page renders.
    """
    row = await service.get(session_id)
    return to_streams(await load_streams(row, RecordingRepository(session)))


@router.post("/{session_id}/metrics/recompute", responses=NOT_FOUND | BAD_BODY)
async def recompute_session_metrics(
    analyser: AnalyserDep,
    metrics: MetricsDep,
    actor: ActorDep,
    session_id: uuid.UUID,
    payload: MetricsRecompute | None = None,
) -> SessionMetricsRead:
    """Recompute one session's metrics against the anchors in force now.

    Appends version *n+1* and supersedes *n*; the old version stays readable
    with the pins it was computed against (invariant 1). Appending a new FTP
    and recomputing therefore changes the **new** version's pin and leaves
    every earlier one exactly as it was.
    """
    reason = (payload.reason if payload else None) or "recomputed on request"
    row = await analyser.compute(session_id, actor=actor, reason=reason)
    return to_metrics(row, (await metrics.pins([row])).get(row.id, []))


@router.post("/{session_id}/merge", responses=NOT_FOUND | BAD_BODY | INVALID)
async def merge_sessions(
    service: ServiceDep,
    metrics: MetricsDep,
    matching: MatchingDep,
    analyser: AnalyserDep,
    actor: ActorDep,
    session_id: uuid.UUID,
    payload: SessionMerge,
) -> SessionRead:
    """Fold a second recording of one ride into this session (WP-6.5).

    The garage-door case: a head unit stopped and restarted leaves two files,
    two sessions and half a ride each. **Both recordings are kept** and both
    move onto this session, whose span widens to cover them; the other session
    row is removed.

    A metric version is then appended over the **joined** stream — the two
    grids laid end to end, with the gap between them left unrecorded and
    reported as a recording stop — so the numbers describe the whole ride
    rather than the first half of it. That recompute reads parquet, which is
    why it happens here rather than inside the merge itself.

    Here rather than under `/matches` because it is an edit to the session and
    it answers with the session.
    """
    row = await matching.merge(
        session_id, absorbed_session_id=payload.absorbed_session_id, actor=actor
    )
    await analyser.compute(
        row.id, actor=actor, reason="recordings merged into one session"
    )
    return await one_to_read(service, metrics, matching, await service.get(row.id))

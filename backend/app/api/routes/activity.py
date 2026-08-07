"""HTTP endpoints for completed sessions. Thin over `app.services.activity`.

Named `activity`, not `sessions`, for the reason D92 gives: `sessions` in this
codebase means the *planned* one, and no name is used for both. The routes it
serves are `/api/v1/sessions` — the athlete-facing noun, where there is no
ambiguity to protect against.

Manual entry lives at `/api/v1/manual-sessions` rather than
`/api/v1/sessions/manual`: a facet of the collection under the id namespace
shadows `GET /sessions/{id}`, which then answers 422 about uuid syntax where
405 is the truth (`.claude/rules/api-collection-facets.md`, D50).

Streams are not served here. `GET /sessions/{id}` answers with the session and
the metadata of the recordings behind it — sources, stops, repairs — and WP-5
adds the endpoints that read `data/streams/`.
"""

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic.json_schema import SkipJsonSchema

from app.api.deps import ActorDep
from app.api.pagination import PageParamsDep
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
from app.core.exceptions import ErrorDetail, ValidationErrorDetail
from app.domain.activity import SessionDiscipline
from app.persistence.activity import RecordingRow, SessionRow
from app.persistence.db import SessionDep
from app.services.activity import LoggedSetInput, SessionService

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


ServiceDep = Annotated[SessionService, Depends(get_service)]

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
    if not row.recordings:
        return row.duration_s, None
    recording_time = sum(recording.recording_time_s for recording in row.recordings)
    return recording_time, recording_time


def to_list_item(row: SessionRow) -> SessionListItem:
    """Project a stored session onto its list-row shape."""
    duration_s, recording_time_s = _duration(row)
    return SessionListItem(
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


def to_read(row: SessionRow, repairs: Mapping[uuid.UUID, int]) -> SessionRead:
    """Project a stored session with the recordings behind it."""
    duration_s, recording_time_s = _duration(row)
    return SessionRead(
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
    )


async def one_to_read(service: SessionService, row: SessionRow) -> SessionRead:
    """Resolve one session's repair counts and project it."""
    return to_read(row, await service.repair_counts([row]))


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
    return SessionsPage(
        items=[to_list_item(session) for session in sessions],
        total=total,
        offset=page.offset,
        limit=page.limit,
    )


@router.get("/{session_id}", responses=NOT_FOUND)
async def get_session(service: ServiceDep, session_id: uuid.UUID) -> SessionRead:
    """Get one completed session with its recordings' metadata.

    Not the samples: those are in `data/streams/` and WP-5 serves them.
    """
    return await one_to_read(service, await service.get(session_id))


@router.patch("/{session_id}", responses=NOT_FOUND | BAD_BODY | INVALID)
async def update_session(
    service: ServiceDep,
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
    return await one_to_read(service, row)


@manual_router.post(
    "", status_code=status.HTTP_201_CREATED, responses=BAD_BODY | INVALID | NOT_FOUND
)
async def create_manual_session(
    service: ServiceDep, actor: ActorDep, payload: ManualSessionCreate
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
    return await one_to_read(service, row)

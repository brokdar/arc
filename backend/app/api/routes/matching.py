"""HTTP endpoints for match links. Thin over `app.services.matching`.

Two routers, because the operations divide by what they are *about*.

`/api/v1/matches` is the link as a resource: list the proposals waiting,
confirm one, reject one, make one by hand, retarget one, remove one. The
proposal inbox is `GET /matches?status=pending`.

`/api/v1/sessions/{id}/…` is the session asking about its own state: run
matching again, or declare that nothing on the calendar is what this was. Both
are sub-resources of one member — one segment deeper than `GET /sessions/{id}`
— so neither shadows it (`.claude/rules/api-collection-facets.md`).

Merging two recordings is deliberately **not** here: it is an edit to the
session, it answers with the session, and it has to recompute the metrics over
the joined stream afterwards — so it lives beside the other session routes, in
`routes/activity.py`.
"""

import uuid
from collections.abc import Mapping, Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic.json_schema import SkipJsonSchema

from app.api.deps import ActorDep
from app.api.pagination import PageParamsDep
from app.api.schemas.matching import (
    MatchBreakdownRead,
    MatchComponentRead,
    MatchCreate,
    MatchesPage,
    MatchOutcomeRead,
    MatchPlannedContext,
    MatchRead,
    MatchRetarget,
    MatchSessionContext,
    MatchSummary,
    MatchUnassessedRead,
    SessionMatchState,
)
from app.core.exceptions import ErrorDetail, ValidationErrorDetail
from app.domain.matching import COMPONENT_WEIGHTS, MatchLinkStatus
from app.persistence.activity import SessionRow, session_duration_s
from app.persistence.db import SessionDep
from app.persistence.matching import SessionMatchRow
from app.persistence.planned_sessions import PlannedSessionRow
from app.services.matching import MatchContext, MatchingService, MatchOutcome

router = APIRouter(prefix="/matches", tags=["matches"])
#: The session-scoped half — see the module docstring.
session_router = APIRouter(prefix="/sessions", tags=["matches"])

type Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {404: {"model": ErrorDetail, "description": "No such match"}}
NO_SESSION: Responses = {404: {"model": ErrorDetail, "description": "No such session"}}
BAD_BODY: Responses = {400: {"model": ErrorDetail, "description": "Malformed body"}}
CONFLICT: Responses = {
    409: {"model": ErrorDetail, "description": "Already linked, or already ruled on"}
}
INVALID: Responses = {
    422: {
        "model": ValidationErrorDetail,
        "description": "The link violates a schema or domain rule",
    }
}


def get_service(session: SessionDep) -> MatchingService:
    """Bind the service to a request-scoped session."""
    return MatchingService.from_session(session)


ServiceDep = Annotated[MatchingService, Depends(get_service)]

# `SkipJsonSchema[None]`: optional by omission, never `null` — see
# `.claude/rules/api-optional-query-params.md`.
StatusFilter = Annotated[
    MatchLinkStatus | SkipJsonSchema[None],
    Query(
        alias="status",
        description=(
            "Restrict to one link status; omit for all of them. The proposal "
            "inbox is `pending`."
        ),
    ),
]


def to_summary(link: SessionMatchRow) -> MatchSummary:
    """Project one link onto the shape both joined resources carry."""
    return MatchSummary(
        id=link.id,
        session_id=link.session_id,
        planned_session_id=link.planned_session_id,
        status=link.status,
        similarity=link.similarity,
        confirmed_at=link.confirmed_at,
    )


def to_breakdown(document: Mapping[str, Any]) -> MatchBreakdownRead:
    """Project a stored breakdown.

    Tolerant of a document written by an earlier version of the score, for the
    reason `app.services.metrics.summarise` gives for the metric payload: the
    breakdown is stored, not recomputed, and a link created last month must
    still render. Missing keys become an empty breakdown carrying the score,
    never an error.
    """
    stored = document.get("weights")
    stored = stored if isinstance(stored, Mapping) else {}
    return MatchBreakdownRead(
        score=document.get("score"),
        # Keyed by the enum, and defaulted per component: a breakdown stored
        # before a weight was named still renders against today's constants
        # rather than against a hole.
        weights={
            component: float(stored.get(component.value, nominal))
            for component, nominal in COMPONENT_WEIGHTS.items()
        },
        components=[
            MatchComponentRead.model_validate(part)
            for part in document.get("components", [])
        ],
        not_assessed=[
            MatchUnassessedRead.model_validate(part)
            for part in document.get("not_assessed", [])
        ],
    )


def to_read(link: SessionMatchRow, context: MatchContext) -> MatchRead:
    """Project one link as its own resource, both sides included."""
    return MatchRead(
        **to_summary(link).model_dump(),
        breakdown=to_breakdown(link.breakdown or {}),
        created_by=link.created_by,
        previous_session_status=link.previous_session_status,
        previous_planned_status=link.previous_planned_status,
        session=_session_context(context.session),
        planned_session=_planned_context(context.planned),
        created_at=link.created_at,
        updated_at=link.updated_at,
    )


def _session_context(row: SessionRow) -> MatchSessionContext:
    """The completed side of a proposal row."""
    return MatchSessionContext(
        id=row.id,
        local_date=row.local_date,
        discipline=row.discipline,
        status=row.status,
        duration_s=session_duration_s(row),
    )


def _planned_context(row: PlannedSessionRow) -> MatchPlannedContext:
    """The planned side of a proposal row."""
    intent = row.current_intent
    return MatchPlannedContext(
        id=row.id,
        date=row.date,
        discipline=row.discipline,
        purpose=intent.purpose,
        status=row.status,
        intent_text=intent.intent_text,
    )


async def one_to_read(service: MatchingService, link: SessionMatchRow) -> MatchRead:
    """Resolve one link's two sides and project it."""
    contexts = await service.contexts([link])
    return to_read(link, contexts[link.id])


async def to_page(
    service: MatchingService, links: Sequence[SessionMatchRow]
) -> list[MatchRead]:
    """Project a whole page of links, both sides loaded in two queries."""
    contexts = await service.contexts(links)
    return [to_read(link, contexts[link.id]) for link in links if link.id in contexts]


def to_state(row: SessionRow, link: SessionMatchRow | None) -> SessionMatchState:
    """Project where a session stands after a link was made or removed."""
    return SessionMatchState(
        session_id=row.id,
        status=row.status,
        match=to_summary(link) if link is not None else None,
    )


def to_outcome(outcome: MatchOutcome) -> MatchOutcomeRead:
    """Project what one run of matching decided."""
    return MatchOutcomeRead(
        session_id=outcome.session.id,
        status=outcome.session.status,
        match=to_summary(outcome.link) if outcome.link is not None else None,
        candidates=outcome.candidates,
        sticky=outcome.sticky,
    )


@router.get("")
async def list_matches(
    service: ServiceDep,
    page: PageParamsDep,
    match_status: StatusFilter = None,
) -> MatchesPage:
    """List match links, newest first. `?status=pending` is the proposal inbox.

    Every row carries both sides, so the inbox renders and is answered without
    a request per proposal.
    """
    links, total = await service.list(
        status=match_status, offset=page.offset, limit=page.limit
    )
    return MatchesPage(
        items=await to_page(service, links),
        total=total,
        offset=page.offset,
        limit=page.limit,
    )


@router.get("/{match_id}", responses=NOT_FOUND)
async def get_match(service: ServiceDep, match_id: uuid.UUID) -> MatchRead:
    """One link, its score and the whole breakdown behind it."""
    return await one_to_read(service, await service.get(match_id))


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses=NO_SESSION | BAD_BODY | CONFLICT | INVALID,
)
async def create_match(
    service: ServiceDep, actor: ActorDep, payload: MatchCreate
) -> MatchRead:
    """Link a session to a planned session by hand (build plan WP-6.6).

    Always the athlete's own, and therefore **sticky**: no re-run of matching
    revises or removes it. With `displaced` set it is the executed-instead-of
    link — the planned session becomes `displaced` rather than completed, and
    the activity is scored standalone.

    The similarity is still computed and stored, however low: a deliberate link
    at 0.12 is worth being able to look at afterwards.
    """
    link = await service.link(
        session_id=payload.session_id,
        planned_session_id=payload.planned_session_id,
        actor=actor,
        displaced=payload.displaced,
    )
    return await one_to_read(service, link)


@router.post("/{match_id}/confirm", responses=NOT_FOUND | CONFLICT)
async def confirm_match(
    service: ServiceDep, actor: ActorDep, match_id: uuid.UUID
) -> MatchRead:
    """Accept a proposal (or an automatic link) as the athlete's own.

    The session becomes `matched` and the planned session `completed`, and the
    link stops being something matching may revise.
    """
    return await one_to_read(service, await service.confirm(match_id, actor=actor))


@router.post("/{match_id}/reject", responses=NOT_FOUND | CONFLICT)
async def reject_match(
    service: ServiceDep, actor: ActorDep, match_id: uuid.UUID
) -> SessionMatchState:
    """Refuse a proposal: drop the link and call the session unplanned.

    Not the same as unlinking. Unlinking restores exactly what was there
    before; rejecting is the athlete saying "that ride was not that session",
    which leaves the ride `unplanned` and the planned session open for
    something else.
    """
    row = await service.reject(match_id, actor=actor)
    return to_state(row, None)


@router.patch("/{match_id}", responses=NOT_FOUND | BAD_BODY | CONFLICT | INVALID)
async def retarget_match(
    service: ServiceDep,
    actor: ActorDep,
    match_id: uuid.UUID,
    payload: MatchRetarget,
) -> MatchRead:
    """Point a link at a different planned session (the swap, WP-6.6).

    One operation rather than an unlink and a link, because it is one decision:
    the old planned session goes back to exactly what it was, the new one takes
    the link, and the result is confirmed — a retarget is always the athlete's.
    """
    link = await service.swap(
        match_id, planned_session_id=payload.planned_session_id, actor=actor
    )
    return await one_to_read(service, link)


@router.delete("/{match_id}", responses=NOT_FOUND)
async def delete_match(
    service: ServiceDep, actor: ActorDep, match_id: uuid.UUID
) -> SessionMatchState:
    """Unlink, putting both sides back exactly as they were (WP-6.8).

    Exactly, not approximately: the link records the two statuses it displaced,
    so a session that was `displaced` before goes back to `displaced` and a
    planned session that was `missed` goes back to `missed`.
    """
    row = await service.unlink(match_id, actor=actor)
    return to_state(row, None)


@session_router.post("/{session_id}/rematch", responses=NO_SESSION)
async def rematch_session(
    service: ServiceDep, actor: ActorDep, session_id: uuid.UUID
) -> MatchOutcomeRead:
    """Run matching again for one session.

    Idempotent: over an unchanged session it reaches the same verdict and
    rewrites the same row. Being explicit, it **does** revise an open link and
    reconsider a session an earlier run called unplanned — and it never touches
    a confirmed or displaced link, which comes back with `sticky` set and
    nothing changed.
    """
    return to_outcome(
        await service.match_session(session_id, actor=actor, rematch=True)
    )


@session_router.post("/{session_id}/unplanned", responses=NO_SESSION | CONFLICT)
async def mark_session_unplanned(
    service: ServiceDep, actor: ActorDep, session_id: uuid.UUID
) -> SessionMatchState:
    """Declare that a session answers to nothing on the calendar (WP-6.6).

    Drops an open proposal on the way, restoring the planned session. A link
    the athlete already confirmed is a 409: two contradictory statements, and
    the second one should be an unlink.
    """
    row = await service.mark_unplanned(session_id, actor=actor)
    return to_state(row, None)

"""HTTP endpoints for scores, verdicts, reasons and the alignment offset.

Two routers, split by what each operation is *about*.

`/api/v1/sessions/{id}/…` is a recorded session asking about its own judgement:
its current score and the history behind it, the alignment it was scored
through, and the verdict the athlete declares on it. Every path is one segment
deeper than `GET /sessions/{id}`, so none of them shadows it.

`/api/v1/planned-sessions/{id}/reasons` is the other half of WP-7.3: a session
that was **missed** has no recording and no declaration, so the athlete answers
the evening prompt against the plan entry instead. Same reasons vocabulary,
same append-only chain, different subject.

Thin, like every adapter here. The athlete-only rule on declarations and
reasons is enforced in `app.services.scoring`, not below — an adapter-level
check would protect this adapter and nothing else, and WP-8's MCP tools reach
the same service.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status

from app.api.deps import ActorDep
from app.api.schemas.scoring import (
    AlignmentOffsetUpdate,
    MissedReasonsUpdate,
    ReasonsRead,
    ScoreRecompute,
    SessionAlignmentRead,
    SessionScoreRead,
    VerdictDeclarationRead,
    VerdictDeclare,
    VerdictReasonsUpdate,
)
from app.core.exceptions import ErrorDetail, NotFoundError, ValidationErrorDetail
from app.persistence.db import SessionDep
from app.persistence.scoring import (
    SessionAlignmentRow,
    SessionReasonsRow,
    SessionScoreRow,
    VerdictDeclarationRow,
)
from app.services.scoring import ScoringService

router = APIRouter(prefix="/sessions", tags=["scores"])
#: The planned side — see the module docstring.
planned_router = APIRouter(prefix="/planned-sessions", tags=["scores"])

type Responses = dict[int | str, dict[str, Any]]
NO_SESSION: Responses = {404: {"model": ErrorDetail, "description": "No such session"}}
NO_SCORE: Responses = {
    404: {"model": ErrorDetail, "description": "No such session, or nothing scored"}
}
NO_PLANNED: Responses = {
    404: {"model": ErrorDetail, "description": "No such planned session"}
}
FORBIDDEN: Responses = {
    403: {
        "model": ErrorDetail,
        "description": "Only the athlete may write this",
    }
}
BAD_BODY: Responses = {400: {"model": ErrorDetail, "description": "Malformed body"}}
INVALID: Responses = {
    422: {
        "model": ValidationErrorDetail,
        "description": "The request violates a schema or domain rule",
    }
}


def get_service(session: SessionDep) -> ScoringService:
    """Bind the service to a request-scoped session."""
    return ScoringService.from_session(session)


ServiceDep = Annotated[ScoringService, Depends(get_service)]


# --- projections ----------------------------------------------------------------


def to_score(row: SessionScoreRow) -> SessionScoreRead:
    """Project one stored score version onto the wire.

    The payload validates straight through: `app.domain.scoring.score_to_json`
    writes the field names this model reads, so the axes are not restated here.
    """
    return SessionScoreRead.model_validate(
        dict(row.payload)
        | {
            "version": row.version,
            "computed_at": row.as_of,
            "recompute_reason": row.recompute_reason,
            "planned_session_id": row.planned_session_id,
            "intent_version": row.intent_version,
            "pinned_anchor_versions": row.pinned_anchor_versions,
            "metrics_version_id": row.metrics_version_id,
            "alignment_version_id": row.alignment_version_id,
            "suggested_verdict": row.suggested_verdict,
        }
    )


def to_alignment(row: SessionAlignmentRow) -> SessionAlignmentRead:
    """Project one stored alignment version onto the wire."""
    return SessionAlignmentRead.model_validate(
        dict(row.payload)
        | {
            "version": row.version,
            "computed_at": row.as_of,
            "recompute_reason": row.recompute_reason,
            "planned_session_id": row.planned_session_id,
            "offset_s": row.offset_s,
        }
    )


def to_reasons(row: SessionReasonsRow) -> ReasonsRead:
    """Project one version of a reasons chain onto the wire."""
    return ReasonsRead(
        version=row.version,
        recorded_at=row.as_of,
        revision_reason=row.recompute_reason,
        reasons=list(row.reasons),
        note=row.note,
        recorded_by=row.recorded_by,
    )


def to_declaration(
    row: VerdictDeclarationRow, reasons: SessionReasonsRow | None
) -> VerdictDeclarationRead:
    """Project the athlete's declaration, with the reasons in force."""
    return VerdictDeclarationRead(
        session_id=row.session_id,
        planned_session_id=row.planned_session_id,
        declared_verdict=row.declared_verdict,
        declared_at=row.declared_at,
        suggested_at_declaration=row.suggested_at_declaration,
        score_version_id=row.score_version_id,
        contested=row.contested,
        contested_at=row.contested_at,
        contested_verdict=row.contested_verdict,
        reasons=to_reasons(reasons) if reasons is not None else None,
    )


def _tip(chain: list[SessionReasonsRow]) -> SessionReasonsRow | None:
    """The unsuperseded version of a reasons chain, or ``None``."""
    open_rows = [row for row in chain if row.superseded_by is None]
    return max(open_rows, key=lambda row: row.version) if open_rows else None


async def _declaration(
    service: ScoringService, session_id: uuid.UUID
) -> VerdictDeclarationRead:
    """Read the declaration in force, with its reasons.

    Raises:
        NotFoundError: When the athlete has not declared anything yet.
    """
    held = await service.declaration(session_id)
    if held is None:
        raise NotFoundError(
            f"Session {session_id} has no declared verdict yet. The suggested "
            "one is on its score."
        )
    return to_declaration(held, _tip(list(await service.reasons(session_id))))


# --- the score ------------------------------------------------------------------


@router.get("/{session_id}/score", responses=NO_SCORE)
async def get_session_score(
    service: ServiceDep, session_id: uuid.UUID
) -> SessionScoreRead:
    """The score version in force for one session.

    404 when nothing has been scored — an unlinked session, one whose link is
    still an unanswered proposal, or one ingested before scoring existed. The
    detail says which, because that sentence is the empty state the page
    renders.

    Also 404 once the link a score was computed against is **gone** — unlinked,
    rejected, called unplanned, or pointed at a different plan entry. The
    versions stay on `/score/history`, where they are the record of what was
    measured; what they stop being is this session's standing judgement.
    """
    row = await service.get_current(session_id)
    if row is None:
        raise NotFoundError(
            f"Session {session_id} has no score. A session is scored once it "
            "is linked to a planned session and that link is settled; a "
            "pending proposal is a question, not a link. A session that was "
            "scored and then unlinked keeps its versions on "
            f"/api/v1/sessions/{session_id}/score/history, but no longer "
            "answers to a prescription."
        )
    return to_score(row)


@router.get("/{session_id}/score/history", responses=NO_SESSION)
async def get_session_score_history(
    service: ServiceDep, session_id: uuid.UUID
) -> list[SessionScoreRead]:
    """Every version of one session's score, oldest first.

    Nothing is ever overwritten (invariant 1), so this is the record of how the
    machine's opinion moved — and of what each version was computed against.
    Empty for a session that has never been scored.
    """
    return [to_score(row) for row in await service.history(session_id)]


@router.post("/{session_id}/score/recompute", responses=NO_SESSION | BAD_BODY | INVALID)
async def recompute_session_score(
    service: ServiceDep,
    actor: ActorDep,
    session_id: uuid.UUID,
    payload: ScoreRecompute | None = None,
) -> SessionScoreRead:
    """Recompute one session's score and append version *n+1*.

    The old version stays readable with the intent version, pins and alignment
    version it was computed against. If a declared verdict now disagrees with
    the new suggestion, the **declaration** is flagged `contested` and left
    exactly as the athlete wrote it (WP-7.4).
    """
    return to_score(
        await service.recompute(
            session_id, actor=actor, reason=payload.reason if payload else None
        )
    )


# --- the alignment offset (A7.1) --------------------------------------------------


@router.get("/{session_id}/alignment", responses=NO_SCORE)
async def get_session_alignment(
    service: ServiceDep, session_id: uuid.UUID
) -> SessionAlignmentRead:
    """The alignment version in force: which effort answered which step.

    Gone with the link, like the score: a pairing between a recording and a
    prescription says nothing about a session that answers to no prescription.
    """
    row = await service.alignment(session_id)
    if row is None:
        raise NotFoundError(
            f"Session {session_id} has no alignment. Only a session linked to "
            "an endurance prescription is aligned — a strength session's sets "
            "are paired by position, not on a timeline, and an unlinked "
            "session has no prescription to pair against."
        )
    return to_alignment(row)


@router.put("/{session_id}/alignment", responses=NO_SESSION | BAD_BODY | INVALID)
async def set_session_alignment_offset(
    service: ServiceDep,
    actor: ActorDep,
    session_id: uuid.UUID,
    payload: AlignmentOffsetUpdate,
) -> SessionAlignmentRead:
    """Slide the planned timeline and rescore against the new pairing.

    The athlete started recording three minutes before starting the workout, or
    the warm-up ran long. Correcting it writes a new alignment version and a
    new score version in one transaction, so a score never points at an
    alignment that is not the one in force (A7.1).
    """
    return to_alignment(
        await service.set_alignment_offset(
            session_id, offset_s=payload.offset_s, actor=actor
        )
    )


# --- the athlete's verdict (WP-7.2, WP-7.3) ---------------------------------------


@router.get("/{session_id}/verdict", responses=NO_SCORE)
async def get_session_verdict(
    service: ServiceDep, session_id: uuid.UUID
) -> VerdictDeclarationRead:
    """What the athlete declared this session was, and why."""
    return await _declaration(service, session_id)


@router.put(
    "/{session_id}/verdict",
    responses=NO_SESSION | BAD_BODY | FORBIDDEN | INVALID,
)
async def declare_session_verdict(
    service: ServiceDep,
    actor: ActorDep,
    session_id: uuid.UUID,
    payload: VerdictDeclare,
) -> VerdictDeclarationRead:
    """Declare what the session was — confirming the suggestion, or overriding it.

    **Only the athlete may call this.** A 403 for anyone else, including a
    coaching agent holding a valid write-scoped key: this is the athlete's own
    account of the session, and an agent that could write it would be
    inventing testimony to read back later (WP-7.2).

    Declaring again replaces the standing declaration and clears any
    `contested` flag — the athlete has now ruled on the machine's current
    opinion.
    """
    await service.declare(
        session_id,
        verdict=payload.verdict,
        reasons=payload.reasons,
        note=payload.note,
        actor=actor,
    )
    return await _declaration(service, session_id)


@router.put(
    "/{session_id}/verdict/reasons",
    responses=NO_SCORE | BAD_BODY | FORBIDDEN | INVALID,
)
async def revise_session_reasons(
    service: ServiceDep,
    actor: ActorDep,
    session_id: uuid.UUID,
    payload: VerdictReasonsUpdate,
) -> ReasonsRead:
    """Revise the reasons behind a declared verdict.

    Append-only: this writes version *n+1* and leaves what was said before
    readable. Athlete-only, for the same reason the declaration is.
    """
    return to_reasons(
        await service.revise_reasons(
            session_id,
            reasons=payload.reasons,
            note=payload.note,
            revision_reason=payload.revision_reason,
            actor=actor,
        )
    )


# --- the missed side (WP-7.3) ------------------------------------------------------


@planned_router.get("/{planned_session_id}/reasons", responses=NO_PLANNED)
async def get_missed_session_reasons(
    service: ServiceDep, planned_session_id: uuid.UUID
) -> ReasonsRead:
    """Why a planned session was missed, as last answered.

    404 while the evening prompt stands unanswered: "nobody has said yet" and
    "the prompt expired, so the reason is `not_provided`" are different states,
    and the second one is a row.
    """
    chain = list(await service.missed_reasons(planned_session_id))
    tip = _tip(chain)
    if tip is None:
        raise NotFoundError(
            f"Planned session {planned_session_id} has no recorded reasons."
        )
    return to_reasons(tip)


@planned_router.put(
    "/{planned_session_id}/reasons",
    status_code=status.HTTP_200_OK,
    responses=NO_PLANNED | BAD_BODY | FORBIDDEN | INVALID,
)
async def answer_missed_session_prompt(
    service: ServiceDep,
    actor: ActorDep,
    planned_session_id: uuid.UUID,
    payload: MissedReasonsUpdate,
) -> ReasonsRead:
    """Answer the evening prompt about a missed session.

    Closes the prompt as `answered`. Answering again is a revision — a new
    version, with the earlier answer still readable. Athlete-only.
    """
    return to_reasons(
        await service.answer_prompt(
            planned_session_id,
            reasons=payload.reasons,
            note=payload.note,
            actor=actor,
        )
    )

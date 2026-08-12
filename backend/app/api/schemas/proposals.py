"""Request/response schemas for plan-change proposals (WP-8.2).

The diff is **typed**, not a free-form document. It is the thing the athlete
answers — "this ride becomes 20 minutes shorter and drops from threshold to
endurance" — so the client renders it field by field, and a client that has to
guess at the shape renders whatever the last version of the agent happened to
write. ``changes`` stays untyped by contrast: it is the instruction the service
replays, the agent's own words back to it, and nothing in the UI reads it.

There is no create schema here on purpose. Proposals are agent-authored: the
athlete's endpoints are the inbox (list, read) and the two answers (accept,
reject), and the way in is the MCP tool over the same service.
"""

import datetime as dt
import uuid
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from app.api.pagination import Page
from app.api.validation import PostgresText
from app.domain.athlete import Discipline
from app.domain.proposals import (
    MAX_RESOLUTION_NOTE_CHARS,
    ChangeKind,
    ProposalStatus,
)
from app.domain.purpose import Purpose
from app.domain.sessions import SessionStatus

RejectionReason = Annotated[
    PostgresText, Field(min_length=1, max_length=MAX_RESOLUTION_NOTE_CHARS)
]


class ProposalSessionSnapshot(BaseModel):
    """One planned session as it stands, or as a change would leave it.

    The two prediction axes are both here and exactly one is ever populated:
    an endurance session has a TSS-equivalent and no kilograms, a strength one
    has kilograms and no TSS. They are different quantities and must never be
    added or shown in one column.

    **Every field a change can touch is here, on both sides**. A
    snapshot that carried only the cheap scalars let a revision of the success
    criteria or of the prescription itself render as "no field differs" above
    an enabled Accept button — the athlete answering a question the diff had
    not asked. The two size fields are here for the same reason: a bodyweight
    lift and an unstructured ride have no computable cost at all, and the
    sets and the seconds are then the only honest way to say how much of it
    there is.
    """

    date: dt.date
    purpose: Purpose
    #: The discipline of *this* side of the change. On the entry it is the
    #: discipline the change leaves; a revision may change it, and both halves
    #: are what decide whether a recording settles the question
    #: (`resolved_by_reality`).
    discipline: Discipline
    #: Derived from reality, never proposed: an `update` change may not
    #: carry it, so it is the same on both sides of every diff.
    status: SessionStatus
    intent_text: str | None
    coach_notes: str | None
    workout_id: uuid.UUID | None
    #: The criteria the session is judged by, as stored. A revision may
    #: rewrite them without touching anything else on this snapshot.
    success_criteria: list[dict[str, Any]]
    #: The frozen prescription, as stored. Present on both sides so a
    #: structure-only revision is visible as one.
    structure: dict[str, Any]
    #: TSS-equivalent; null for strength and whenever it cannot be predicted.
    predicted_load: float | None
    #: Prescribed volume load in kilograms; null for endurance.
    predicted_volume_kg: float | None
    #: Prescribed seconds; null for strength and for a ride with a
    #: distance-based step.
    duration_s: int | None
    #: Prescribed working sets; null for endurance.
    total_sets: int | None


class ProposalChangeDiff(BaseModel):
    """What one change would do, computed when the proposal was written."""

    kind: ChangeKind
    #: The session the change addresses; null for a `create`.
    planned_session_id: uuid.UUID | None
    #: The date the change is about — the target date for a `create`, and the
    #: session's own date for every other kind. A move's target is therefore
    #: `after.date`, not this: this is the date it would be moved off.
    date: dt.date
    discipline: Discipline
    #: The intent version the change was computed against; null for a
    #: `create`. Re-checked on accept, so a proposal whose session has been
    #: edited since is refused rather than merged.
    expected_intent_version: int | None
    #: The session before the change; null for a `create`.
    before: ProposalSessionSnapshot | None
    #: The session after it; null for a `delete`.
    after: ProposalSessionSnapshot | None


class ProposalRead(BaseModel):
    """A plan-change proposal as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: ProposalStatus
    #: Why the agent is suggesting this. Always present and never empty.
    rationale: str
    #: The instruction, in the domain's tagged-union wire form. Replayed on
    #: accept; nothing in the UI reads it.
    changes: list[dict[str, Any]]
    #: What the instruction would do, per entity.
    diff: list[ProposalChangeDiff]
    #: When it stops standing. On expiry the committed plan simply stands.
    expires_at: dt.datetime
    #: `athlete` / `agent:<key-label>` / `system`.
    created_by: str
    created_at: dt.datetime
    #: When it left `pending`; null while it stands.
    resolved_at: dt.datetime | None
    #: The athlete's words on rejecting it.
    resolution_note: str | None
    supersedes_id: uuid.UUID | None
    superseded_by_id: uuid.UUID | None


class ProposalReject(BaseModel):
    """Payload for declining a proposal."""

    model_config = ConfigDict(extra="forbid")

    #: Why, in the athlete's own words. Optional, stored verbatim, never
    #: parsed — the seed of the coach-quality loop, not an input to a rule.
    reason: RejectionReason | None = None


ProposalsPage = Page[ProposalRead]

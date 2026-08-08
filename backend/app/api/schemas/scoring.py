"""Request and response schemas for scores, verdicts, alignment and reasons.

**Every axis is one shape**, the same one every metric already has: a value
with its explanation, *or* a `not_assessed` reason, never both and never
neither (`.claude/rules/frontend-ui-conventions.md` rule 4). The UI branches
once and renders the reason in the slot the number would have occupied,
instead of inventing an empty state per axis.

The field names mirror `app.domain.scoring.score_to_json` exactly, so the
stored payload validates straight into :class:`ScorePayloadRead` and the route
does not restate the score on the way out — the same contract
`app.api.schemas.metrics` keeps with the metric artefact. Extra keys are
ignored, which is what lets a later work package add an axis without
invalidating every score already written.

**The declaration is a separate resource from the score** because the two have
different writers: `PUT /sessions/{id}/verdict` is the athlete's, and nothing
else may call it (WP-7.2). ``contested`` is on the declaration rather than the
score for the same reason — it is a fact about the athlete's statement, not
about the machine's.
"""

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.metrics import ExplanationRead
from app.api.validation import PostgresText
from app.domain.criteria import CriterionKind
from app.domain.purpose import Purpose
from app.domain.scoring import (
    MAX_REASON_NOTE_CHARS,
    MAX_REASONS,
    CompletionState,
    Reason,
    Verdict,
    VerdictRule,
)
from app.domain.templates import ScoringAxis
from app.persistence.scoring import MAX_REASON_LENGTH
from app.services.scoring import MAX_ALIGNMENT_OFFSET_S

__all__ = [
    "AlignedStepRead",
    "AlignmentOffsetUpdate",
    "AxisRead",
    "CompletionState",
    "CriterionOutcomeRead",
    "ExcludedStepRead",
    "MissedReasonsUpdate",
    "ReasonsRead",
    "ScorePayloadRead",
    "ScoreRecompute",
    "SessionAlignmentRead",
    "SessionScoreRead",
    "VerdictDeclarationRead",
    "VerdictDeclare",
    "VerdictReasonsUpdate",
]


class CriterionOutcomeRead(BaseModel):
    """One success criterion, checked.

    ``passed`` has three states and the third is not a failure: ``null`` means
    the criterion could not be checked, and ``not_assessed`` says why. Render
    that as a reason, never as a red cross — a ride with no power meter did
    not fail its time-in-band criterion.
    """

    #: Position in the intent's frozen `success_criteria` — the join key back
    #: onto the prescription the athlete is looking at.
    index: int
    kind: CriterionKind
    passed: bool | None = None
    #: What was measured, in the criterion's own terms: a fraction for
    #: `time_in_band` and `sets_completed`, seconds for `ceiling` and
    #: `duration_floor`.
    observed: float | None = None
    #: What the criterion asked for, in the same terms.
    required: float | None = None
    #: One sentence for the athlete. Always present.
    detail: str
    not_assessed: str | None = None


class AxisRead(BaseModel):
    """One scoring axis: answered with a number, or refused with a reason.

    Exactly one of ``value`` and ``not_assessed`` is non-null, and
    ``explanation`` is present exactly when ``value`` is.
    """

    axis: ScoringAxis
    #: The score, in ``[0, 1]``.
    value: float | None = None
    explanation: ExplanationRead | None = None
    #: Why this axis has no score, in the athlete's terms and naming the
    #: missing input. ``deferred`` for `response` and `fuelling`, which are in
    #: the vocabulary and out of MVP scope.
    not_assessed: str | None = None
    #: The criteria this axis checked, with their pass/fail detail.
    criteria: list[CriterionOutcomeRead] = []


class ScorePayloadRead(BaseModel):
    """The computed half of a score: the axes and the suggested verdict.

    Validates the stored payload directly — see the module docstring.
    """

    purpose: Purpose
    #: True when the link is `displaced`: the athlete trained something else,
    #: so no axis compares the recording against the prescription (WP-6.4).
    standalone: bool = False
    suggested_verdict: Verdict
    #: Which row of the deterministic rule table produced the suggestion.
    verdict_rule: VerdictRule
    #: That row, said as a sentence. Render it beside the suggestion — a
    #: verdict without its reason is the machine asserting rather than showing.
    verdict_rationale: str
    #: One entry per axis the purpose template lists, in the template's order.
    axes: list[AxisRead] = []
    #: Criteria the prescription froze whose own axis this purpose is not
    #: scored on. Evaluated anyway, because a criterion nobody can see is a
    #: promise nobody kept.
    other_criteria: list[CriterionOutcomeRead] = []


class SessionScoreRead(ScorePayloadRead):
    """One version of one session's score, with what it was computed from."""

    #: 1-based position in the chain. A rescore writes n+1 and supersedes n;
    #: the old version stays readable (invariant 1).
    version: int
    computed_at: dt.datetime
    #: Why this version exists. Null on version 1.
    recompute_reason: str | None = None
    #: The prescription this score judged the recording against.
    planned_session_id: uuid.UUID | None = None
    #: Which intent version was scored — what tells two versions apart after a
    #: post-hoc edit.
    intent_version: int | None = None
    #: Anchor type -> the anchor version id the score resolved against, both
    #: as strings. A copy of what the intent pinned, frozen here.
    pinned_anchor_versions: dict[str, str] = {}
    #: The metric artefact the recorded numbers came from.
    metrics_version_id: uuid.UUID | None = None
    #: The alignment version the aligned work steps came from (A7.1).
    alignment_version_id: uuid.UUID | None = None


class ScoreRecompute(BaseModel):
    """Why a recomputation was asked for. The body is optional.

    The reason lands on the **new** version and is what a later reader sees
    when two score versions disagree.
    """

    model_config = ConfigDict(extra="forbid")

    reason: PostgresText | None = Field(
        default=None,
        max_length=MAX_REASON_LENGTH,
        description="Why the score is being recomputed.",
    )


# --- alignment (A7.1) -----------------------------------------------------------


class AlignedStepRead(BaseModel):
    """One planned work step paired with one detected effort."""

    #: `app.domain.workout.FlatStep.index` of the planned step.
    step_index: int
    #: Position of the detected interval in the metric artefact's list.
    interval_index: int
    #: How well the two agree, in ``[0, 1]``.
    confidence: float


class ExcludedStepRead(BaseModel):
    """A pair the assignment made and the confidence gate then refused.

    Different from a step that was never performed, and shown differently:
    "we matched this step to that effort and did not trust the match".
    """

    step_index: int
    interval_index: int
    confidence: float
    reason: str


class SessionAlignmentRead(BaseModel):
    """One version of how a recording lines up with its prescription."""

    version: int
    computed_at: dt.datetime
    recompute_reason: str | None = None
    planned_session_id: uuid.UUID | None = None
    #: Seconds the planned timeline was slid by. Positive means the workout
    #: began *later* than the recording did — the ordinary case.
    offset_s: int
    aligned: list[AlignedStepRead] = []
    excluded: list[ExcludedStepRead] = []
    #: Planned work steps no detected effort was assigned to.
    unmatched_steps: list[int] = []
    #: Detected efforts no planned step claimed.
    unmatched_intervals: list[int] = []


class AlignmentOffsetUpdate(BaseModel):
    """Slide the planned timeline along the recording.

    Functional, not cosmetic: the offset changes which detected effort answers
    which prescribed step, so it changes the adherence and pacing axes. Setting
    it creates a new alignment version and rescores the session (A7.1).
    """

    model_config = ConfigDict(extra="forbid")

    offset_s: int = Field(
        ge=-MAX_ALIGNMENT_OFFSET_S,
        le=MAX_ALIGNMENT_OFFSET_S,
        description=(
            "Seconds to slide the planned timeline by before the steps are "
            "assigned. Positive means the workout began later than the "
            "recording did."
        ),
    )


# --- the athlete's testimony (WP-7.2, WP-7.3) -----------------------------------


class ReasonsRead(BaseModel):
    """One version of the reasons behind a verdict, or behind a missed session."""

    version: int
    recorded_at: dt.datetime
    #: Why this revision was written. Null on version 1.
    revision_reason: str | None = None
    #: One to three reasons, **ordered by primacy** — the first is the main
    #: one. The order is data: a revision that only reorders them is a real
    #: revision.
    reasons: list[Reason] = []
    #: The athlete's own words beside the controlled list, never instead of it.
    note: str | None = None
    #: `app.domain.actor.Actor` in its stored form. `system` only for the
    #: auto-reason an expired evening prompt records.
    recorded_by: str


class VerdictDeclarationRead(BaseModel):
    """What the athlete said the session was, and whether it is contested."""

    session_id: uuid.UUID
    planned_session_id: uuid.UUID | None = None
    declared_verdict: Verdict
    declared_at: dt.datetime
    #: What the machine was suggesting when the athlete declared. Null when
    #: nothing had been computed yet.
    suggested_at_declaration: Verdict | None = None
    #: The score version the athlete was looking at.
    score_version_id: uuid.UUID | None = None
    #: A later score suggests something that contradicts this declaration and
    #: differs from what the athlete ruled on. **Surface it; never act on it** —
    #: the declaration stands (WP-7.4).
    contested: bool = False
    contested_at: dt.datetime | None = None
    contested_verdict: Verdict | None = None
    #: The reasons in force, or null when none were given.
    reasons: ReasonsRead | None = None


class VerdictDeclare(BaseModel):
    """Declare what the session was. The athlete's, and only the athlete's.

    Anything but `as_intended` needs one to three reasons, in order of
    primacy — pick `not_provided` rather than leaving the list empty if you
    would rather not say.
    """

    model_config = ConfigDict(extra="forbid")

    verdict: Verdict = Field(description="What the session actually was.")
    reasons: list[Reason] = Field(
        default=[],
        max_length=MAX_REASONS,
        description=(
            "One to three reasons, ordered by primacy, required for any "
            "verdict but `as_intended`. Each may appear once."
        ),
    )
    note: PostgresText | None = Field(
        default=None,
        max_length=MAX_REASON_NOTE_CHARS,
        description="Free text beside the reasons, never instead of them.",
    )


class VerdictReasonsUpdate(BaseModel):
    """Revise the reasons behind a declaration. Append-only: a new version."""

    model_config = ConfigDict(extra="forbid")

    reasons: list[Reason] = Field(
        min_length=1,
        max_length=MAX_REASONS,
        description="One to three reasons, ordered by primacy.",
    )
    note: PostgresText | None = Field(default=None, max_length=MAX_REASON_NOTE_CHARS)
    revision_reason: PostgresText | None = Field(
        default=None,
        max_length=MAX_REASON_LENGTH,
        description="Why the earlier answer is being revised.",
    )


class MissedReasonsUpdate(BaseModel):
    """Answer the evening prompt about a session that was missed."""

    model_config = ConfigDict(extra="forbid")

    reasons: list[Reason] = Field(
        min_length=1,
        max_length=MAX_REASONS,
        description="One to three reasons, ordered by primacy.",
    )
    note: PostgresText | None = Field(default=None, max_length=MAX_REASON_NOTE_CHARS)

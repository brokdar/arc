"""Writing and reading the scoring artefacts. WP-7's use-cases in one place.

Four things live here, and the split between them is the work package's whole
point.

**Computing a score** (:meth:`ScoringService.score_session`). Assembles the
frozen prescription, the metric artefact, the anchor versions the intent
pinned and the alignment in force into `app.domain.scoring.ScoringInputs`,
runs the pure scorer, and appends version *n+1*. It is **total**: an axis with
no inputs answers `not_assessed`, and nothing on this path raises for a session
that merely cannot be measured — scoring runs behind matching on the ingest
pipeline, and a scorer that threw would leave an athlete with a matched ride
and an exception.

**The alignment artefact** (A7.1). Recomputed on every score and written as a
new version **only when it differs** from the one in force — so a rescore after
an intent edit re-pairs the steps against the same offset, and an offset change
(:meth:`set_alignment_offset`) is what deliberately creates version *n+1* and
triggers a rescore through the normal path.

**Testimony** (:meth:`declare`, :meth:`revise_reasons`, :meth:`answer_prompt`).
Only `app.domain.actor.Actor.athlete` may write any of it — enforced here, in
the service, so the rule holds for the API, for an MCP tool that has not been
written yet, and for anything else that ever reaches this layer (WP-7.2). A
declaration is never rewritten by a recomputation: a later score that
contradicts it sets ``contested`` and stops.

**The expiry sweep** (:func:`run_prompt_expiry`). An evening prompt unanswered
after `app.domain.matching.PROMPT_TTL_HOURS` is closed as `expired` and records
the auto-reason `not_provided` — "we asked and got no answer" is a fact, and
storing it as one is what stops the coaching agent from reading silence as
assent.

**Streams arrive through a seam.** The adherence, discipline and pacing axes
read the cleaned 1 Hz columns, which live in parquet — and a service may not
import `app.ingest`, which is the only layer allowed to read them. So the
column source is a module-level callable with a null default
(:func:`set_stream_loader`), installed by `app.ingest.scoring` at application
wiring. Uninstalled, every stream-derived axis says so and the rest of the
score still computes; that is the same shape `app.services.metrics` takes when
it accepts a prepared analysis rather than reading a file.
"""

import datetime as dt
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from typing import Any, Self

from apscheduler.schedulers.base import BaseScheduler
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import (
    ForbiddenError,
    NotFoundError,
    ValidationError,
    domain_rules,
)
from app.core.logging import get_logger
from app.domain.actor import Actor, ActorKind
from app.domain.alignment import (
    Alignment,
    WorkInterval,
    align,
    alignment_to_json,
)
from app.domain.anchors import AnchorType
from app.domain.criteria import SuccessCriterion, criteria_from_json
from app.domain.matching import EveningPromptStatus, MatchLinkStatus
from app.domain.prediction import PinnedAnchor
from app.domain.purpose import Purpose
from app.domain.resolution import ResolvedStep, resolve_steps
from app.domain.scoring import (
    MAX_REASON_NOTE_CHARS,
    MAX_REASONS,
    MIN_REASONS,
    Reason,
    ScoredStep,
    ScoringInputs,
    SessionScore,
    Verdict,
    score_session,
    score_to_json,
)
from app.domain.strength import LoadKind, StrengthWorkout
from app.domain.templates import ScoringAxis
from app.domain.versioning import FIRST_VERSION, next_version
from app.domain.workout import (
    Channel,
    EnduranceWorkout,
    FlatStep,
    WorkoutBody,
    flatten,
    total_duration_s,
    workout_body_from_json,
)
from app.persistence.activity import (
    SessionRepository,
    SessionRow,
    session_duration_s,
)
from app.persistence.audit import AuditRepository
from app.persistence.db import commit, session_scope
from app.persistence.matching import (
    EveningPromptRepository,
    EveningPromptRow,
    SessionMatchRepository,
)
from app.persistence.metrics import SessionMetricsRow
from app.persistence.planned_sessions import (
    PlannedSessionRepository,
    PlannedSessionRow,
)
from app.persistence.scoring import (
    MAX_REASON_LENGTH,
    SessionAlignmentRepository,
    SessionAlignmentRow,
    SessionReasonsRepository,
    SessionReasonsRow,
    SessionScoreRepository,
    SessionScoreRow,
    VerdictDeclarationRepository,
    VerdictDeclarationRow,
)
from app.services.anchors import AnchorService, parse_pins, resolve_pins
from app.services.metrics import SessionMetricsService
from app.services.templates import purpose_templates

logger = get_logger(__name__)

#: `entity_type` written on the score artefact's audit rows.
SCORE_ENTITY_TYPE = "session_score"
#: …on the alignment artefact's.
ALIGNMENT_ENTITY_TYPE = "session_alignment"
#: …on the athlete's declaration.
DECLARATION_ENTITY_TYPE = "verdict_declaration"
#: …on a reasons revision.
REASONS_ENTITY_TYPE = "session_reasons"
#: …on an evening prompt.
PROMPT_ENTITY_TYPE = "evening_prompt"

#: Job id under which the prompt-expiry sweep is registered with APScheduler.
PROMPT_EXPIRY_JOB_ID = "scoring_prompt_expiry"

#: `recompute_reason` written when a post-hoc intent edit triggers a rescore.
REASON_INTENT_EDITED = "the session's intent was edited after it was matched"
#: …when the athlete moves the alignment offset.
REASON_OFFSET_CHANGED = "the alignment offset was changed"
#: …when a match settles into a link worth scoring.
REASON_MATCH_SETTLED = "the session was matched to a planned session"

#: What a session says when nothing on the calendar is linked to it.
NOT_SCOREABLE = (
    "this session is not linked to a planned session, so there is no "
    "prescription to score it against"
)

#: …and what a `displaced` link says when asked to slide its timeline.
NOT_ALIGNABLE_DISPLACED = (
    "this link says the athlete trained something else, so the session is "
    "scored standalone and its steps are never paired against the "
    "prescription. There is no alignment for an offset to move. Unlink it and "
    "link the session again if the recording really does answer to this "
    "prescription."
)

#: Furthest an alignment offset may slide the planned timeline, in seconds.
#: Six hours is longer than any lead-in or over-long warm-up; past it the
#: correction is not a correction, and the control would be a way to align a
#: prescription to a different ride entirely.
MAX_ALIGNMENT_OFFSET_S = 6 * 60 * 60


# --- the stream seam ------------------------------------------------------------

#: Channel -> the cleaned 1 Hz column, nulls preserved, on the joined grid.
type SessionColumns = Mapping[Channel, tuple[float | None, ...]]

#: Signature of "give me the columns behind this session".
type StreamLoader = Callable[
    [AsyncSession, uuid.UUID], Awaitable[SessionColumns | None]
]


async def _no_streams(
    _session: AsyncSession, _session_id: uuid.UUID
) -> SessionColumns | None:
    """The default: no columns, because reading parquet is a layer out.

    Every axis that needs a channel then answers `not_assessed` naming the
    channel it did not get, which is the honest answer for a session whose
    stream is genuinely absent as well.
    """
    return None


_stream_loader: StreamLoader = _no_streams


def set_stream_loader(loader: StreamLoader | None) -> None:
    """Install the stream loader; ``None`` restores the null default."""
    global _stream_loader  # noqa: PLW0603
    _stream_loader = loader or _no_streams


class ScoringService:
    """Use-cases for scores, verdicts and reasons. Raises AppError subclasses."""

    def __init__(
        self,
        session: AsyncSession,
        scores: SessionScoreRepository,
        alignments: SessionAlignmentRepository,
        declarations: VerdictDeclarationRepository,
        reasons: SessionReasonsRepository,
        links: SessionMatchRepository,
        prompts: EveningPromptRepository,
        planned: PlannedSessionRepository,
        sessions: SessionRepository,
        metrics: SessionMetricsService,
        anchors: AnchorService,
        audit: AuditRepository,
    ) -> None:
        self._session = session
        self._scores = scores
        self._alignments = alignments
        self._declarations = declarations
        self._reasons = reasons
        self._links = links
        self._prompts = prompts
        self._planned = planned
        self._sessions = sessions
        self._metrics = metrics
        self._anchors = anchors
        self._audit = audit

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and everything it reads to one database session."""
        return cls(
            session,
            SessionScoreRepository(session),
            SessionAlignmentRepository(session),
            VerdictDeclarationRepository(session),
            SessionReasonsRepository(session),
            SessionMatchRepository(session),
            EveningPromptRepository(session),
            PlannedSessionRepository(session),
            SessionRepository(session),
            SessionMetricsService.from_session(session),
            AnchorService.from_session(session),
            AuditRepository(session),
        )

    # --- reads ---------------------------------------------------------------

    async def standing_link(self, session_id: uuid.UUID) -> uuid.UUID | None:
        """The plan entry this session answers to right now, or ``None``.

        The judgement's subject. A score is *about* a link — this recording,
        against that prescription — so a stored score is only the standing
        judgement while that link is still the one in force. Unlinking,
        rejecting, marking a session unplanned and swapping it onto a different
        plan entry all change the answer here, and every current-score read
        goes through it.

        A **pending** link is not one: a proposal is a question, and that
        is why nothing scores against one in the first place.
        """
        link = await self._links.for_session(session_id)
        if link is None or link.status is MatchLinkStatus.PENDING:
            return None
        return link.planned_session_id

    async def _stands(self, row: SessionScoreRow | None) -> bool:
        """Whether one stored score is still the judgement in force."""
        if row is None:
            return False
        standing = await self.standing_link(row.session_id)
        return standing is not None and standing == row.planned_session_id

    async def get_current(self, session_id: uuid.UUID) -> SessionScoreRow | None:
        """The score version in force for one session, or ``None``.

        ``None`` for a session whose link has since been removed or retargeted,
        even though the chain behind it is intact and :meth:`history` still
        returns every version of it. The chain is the record of what was once
        measured (invariant 1); the *judgement* answers to a link, and
        presenting last week's verdict for a ride that now answers to nothing
        on the calendar would be the score outliving its own subject.
        """
        row = await self._scores.get_current(session_id)
        return row if await self._stands(row) else None

    async def history(self, session_id: uuid.UUID) -> Sequence[SessionScoreRow]:
        """Every version of one session's score, oldest first.

        Unfiltered, deliberately: nothing deletes a computed artefact, and the
        history is where an unlinked session's old verdicts stay readable.
        """
        return await self._scores.history(session_id)

    async def current_for_sessions(
        self, session_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, SessionScoreRow]:
        """The score in force for each of several sessions, in one query.

        Chain tips only — the caller holds the links this page is drawn from
        and pairs them itself (`app.services.plan.PlanService._verdicts`),
        which is the same test :meth:`get_current` applies without the second
        round trip per session.
        """
        return await self._scores.current_for_sessions(session_ids)

    async def alignment(self, session_id: uuid.UUID) -> SessionAlignmentRow | None:
        """The alignment version in force for one session, or ``None``.

        Gated on the standing link exactly as :meth:`get_current` is: the
        pairing is between a recording and a prescription, and a table of which
        effort answered which step is meaningless for a session that answers to
        no prescription.
        """
        row = await self._alignments.get_current(session_id)
        if row is None:
            return None
        standing = await self.standing_link(session_id)
        return (
            row if standing is not None and standing == row.planned_session_id else None
        )

    async def alignment_history(
        self, session_id: uuid.UUID
    ) -> Sequence[SessionAlignmentRow]:
        """Every version of one session's alignment, oldest first."""
        return await self._alignments.history(session_id)

    async def declaration(self, session_id: uuid.UUID) -> VerdictDeclarationRow | None:
        """The athlete's declaration on one session, or ``None``."""
        return await self._declarations.for_session(session_id)

    async def declarations_for(
        self, session_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, VerdictDeclarationRow]:
        """Declarations for several sessions, in one query."""
        return await self._declarations.for_sessions(session_ids)

    async def reasons(self, session_id: uuid.UUID) -> Sequence[SessionReasonsRow]:
        """Every version of a declared session's reasons, oldest first.

        Empty when nothing has been declared: reasons hang off the
        declaration, and there is nothing to explain until there is one.
        """
        held = await self._declarations.for_session(session_id)
        if held is None:
            return []
        return await self._reasons.for_declaration(held.id)

    async def missed_reasons(
        self, planned_session_id: uuid.UUID
    ) -> Sequence[SessionReasonsRow]:
        """Every version of a missed session's reasons, oldest first."""
        return await self._reasons.for_planned_session(planned_session_id)

    async def prompt(self, planned_session_id: uuid.UUID) -> EveningPromptRow | None:
        """The evening prompt raised about one planned session, or ``None``."""
        return await self._prompts.for_planned_session(planned_session_id)

    # --- computing a score ---------------------------------------------------

    async def score_session(
        self, session_id: uuid.UUID, *, actor: Actor, reason: str | None = None
    ) -> SessionScoreRow | None:
        """Compute and store a score version for one session, and commit.

        ``None`` when there is nothing to score: an unlinked session, or one
        whose link is still a **pending proposal**. A proposal is a question,
        and scoring a ride against a prescription the athlete has not agreed it
        answers to would put a verdict on a guess (the same rule, applied to
        WP-7).

        Raises:
            NotFoundError: When no session has that id.
        """
        row = await self._score(session_id, actor=actor, reason=reason)
        if row is not None:
            await commit(self._session)
            await self._session.refresh(row)
        return row

    async def recompute(
        self, session_id: uuid.UUID, *, actor: Actor, reason: str | None = None
    ) -> SessionScoreRow:
        """Recompute one session's score on request.

        Raises:
            NotFoundError: When no session has that id.
            ValidationError: When the session has nothing to be scored against.
        """
        row = await self.score_session(
            session_id, actor=actor, reason=reason or "recomputed on request"
        )
        if row is None:
            raise ValidationError(NOT_SCOREABLE)
        return row

    async def rescore_for_planned_session(
        self, planned_session_id: uuid.UUID, *, actor: Actor, reason: str
    ) -> SessionScoreRow | None:
        """Rescore whatever is linked to one planned session, without committing.

        The rescore seam `app.services.planned_sessions` fires inside the
        transaction that wrote the new intent version, so this one must not
        end it: the intent edit and the score it caused land together or not
        at all.
        """
        link = await self._links.for_planned_session(planned_session_id)
        if link is None:
            return None
        return await self._score(link.session_id, actor=actor, reason=reason)

    async def _score(
        self, session_id: uuid.UUID, *, actor: Actor, reason: str | None
    ) -> SessionScoreRow | None:
        """Compute one score version without committing."""
        row = await self._sessions.get(session_id)
        if row is None:
            raise NotFoundError(f"Session {session_id} not found")
        link = await self._links.for_session(session_id)
        if link is None or link.status is MatchLinkStatus.PENDING:
            return None
        planned = await self._planned.get(link.planned_session_id)
        if planned is None:  # pragma: no cover — the link's FK forbids it
            return None

        intent = planned.current_intent
        standalone = link.status is MatchLinkStatus.DISPLACED
        with domain_rules():
            body = workout_body_from_json(intent.structure)
            criteria = criteria_from_json(intent.success_criteria)
        anchors = await self._pins(intent.pinned_anchor_versions)
        metrics_row = await self._metrics.get_current(session_id)
        intervals = _intervals(metrics_row)
        columns = await _stream_loader(self._session, session_id) or {}

        alignment_row: SessionAlignmentRow | None = None
        aligned: Alignment | None = None
        if not standalone and isinstance(body, EnduranceWorkout):
            aligned, alignment_row = await self._align(
                row, planned, body, anchors, intervals, actor=actor
            )

        template = purpose_templates()[intent.purpose]
        inputs = _inputs(
            row=row,
            body=body,
            criteria=criteria,
            axes=template.axes,
            purpose=intent.purpose,
            anchors=anchors,
            columns=columns,
            alignment=aligned,
            intervals=intervals,
            standalone=standalone,
        )
        score = score_session(inputs)
        return await self._append(
            row,
            planned,
            score,
            intent_version=intent.version,
            pins=dict(intent.pinned_anchor_versions or {}),
            metrics_row=metrics_row,
            alignment_row=alignment_row,
            actor=actor,
            reason=reason,
        )

    async def _pins(
        self, stored: Mapping[str, Any] | None
    ) -> dict[AnchorType, PinnedAnchor]:
        """Resolve an intent's frozen pins to the anchor versions they name."""
        pins = parse_pins(stored)
        versions = await self._anchors.by_ids(pins.values())
        return resolve_pins(pins, versions)

    async def _align(
        self,
        row: SessionRow,
        planned: PlannedSessionRow,
        body: EnduranceWorkout,
        anchors: Mapping[AnchorType, PinnedAnchor],
        intervals: Sequence[WorkInterval],
        *,
        actor: Actor,
        offset_s: int | None = None,
    ) -> tuple[Alignment, SessionAlignmentRow]:
        """Align the prescription to the recording, versioning only real changes.

        The offset is carried forward from the version in force unless the
        caller names a new one, so a rescore re-pairs the steps against the
        correction the athlete already made. A new version is written only when
        the result differs from what is stored: a recompute that reaches the
        same pairing has not changed the alignment, and a chain that grew a
        link every time anything was rescored would make "which alignment was
        this score taken against" a question with a hundred identical answers.
        """
        current = await self._alignments.get_current(row.id)
        offset = offset_s if offset_s is not None else _offset_of(current)
        steps = tuple(flatten(body))
        result = align(
            steps,
            intervals,
            offset_s=offset,
            target_watts=_target_watts(body, anchors),
        )
        payload = alignment_to_json(result)
        if (
            current is not None
            and current.offset_s == offset
            and current.payload == payload
        ):
            return result, current

        # pyrefly: ignore[bad-specialization]
        # The row satisfies `VersionRecord` at runtime; pyrefly does not see
        # through SQLAlchemy's `Mapped[X]` descriptors when structurally
        # matching a protocol.
        version = next_version(await self._alignments.history(row.id))
        appended = await self._alignments.add(
            SessionAlignmentRow(
                session_id=row.id,
                planned_session_id=planned.id,
                version=version,
                as_of=dt.datetime.now(dt.UTC),
                recompute_reason=(
                    None
                    if version == FIRST_VERSION
                    else _bounded(
                        REASON_OFFSET_CHANGED
                        if offset_s is not None
                        else "the alignment was recomputed and differs"
                    )
                ),
                offset_s=offset,
                payload=payload,
                created_by=str(actor),
            )
        )
        if current is not None:
            current.superseded_by = appended.id
            await self._alignments.add(current)
        await self._audit.record(
            actor=actor,
            action=(
                "session.aligned" if version == FIRST_VERSION else "session.realigned"
            ),
            entity_type=ALIGNMENT_ENTITY_TYPE,
            entity_id=appended.id,
            payload={
                "session_id": str(row.id),
                "planned_session_id": str(planned.id),
                "version": version,
                "offset_s": offset,
                "aligned_steps": len(result.aligned),
                "excluded_steps": len(result.excluded),
            },
        )
        return result, appended

    async def _append(
        self,
        row: SessionRow,
        planned: PlannedSessionRow,
        score: SessionScore,
        *,
        intent_version: int,
        pins: Mapping[str, str],
        metrics_row: SessionMetricsRow | None,
        alignment_row: SessionAlignmentRow | None,
        actor: Actor,
        reason: str | None,
    ) -> SessionScoreRow:
        """Write version *n+1* of the score and close off version *n*."""
        chain = await self._scores.history(row.id)
        # pyrefly: ignore[bad-specialization]
        # See `_align` for why the protocol match is suppressed.
        version = next_version(chain)
        previous = await self._scores.get_current(row.id)
        appended = await self._scores.add(
            SessionScoreRow(
                session_id=row.id,
                planned_session_id=planned.id,
                version=version,
                as_of=dt.datetime.now(dt.UTC),
                recompute_reason=(
                    None
                    if version == FIRST_VERSION
                    else _bounded(reason or "recomputed")
                ),
                intent_version=intent_version,
                pinned_anchor_versions=dict(pins),
                metrics_version_id=metrics_row.id if metrics_row else None,
                alignment_version_id=alignment_row.id if alignment_row else None,
                suggested_verdict=score.suggested_verdict,
                payload=score_to_json(score),
                created_by=str(actor),
            )
        )
        if previous is not None:
            # Closed off in the same transaction as the new tip: a reader
            # holding the old id has to be able to walk forward.
            previous.superseded_by = appended.id
            await self._scores.add(previous)
        await self._audit.record(
            actor=actor,
            action=(
                "session.scored" if version == FIRST_VERSION else "session.rescored"
            ),
            entity_type=SCORE_ENTITY_TYPE,
            entity_id=appended.id,
            payload={
                "session_id": str(row.id),
                "planned_session_id": str(planned.id),
                "version": version,
                "superseded": str(previous.id) if previous is not None else None,
                "recompute_reason": appended.recompute_reason,
                "suggested_verdict": score.suggested_verdict.value,
                "verdict_rule": score.verdict_rule.value,
                "intent_version": intent_version,
            },
        )
        await self._contest(row.id, score.suggested_verdict, actor=actor)
        return appended

    async def _contest(
        self, session_id: uuid.UUID, suggested: Verdict, *, actor: Actor
    ) -> None:
        """Flag a declaration the machine has come to disagree with (WP-7.4).

        Contested means two things at once: the new suggestion contradicts what
        the athlete **declared**, *and* it differs from what the machine was
        suggesting when they declared it. The second half is what stops every
        deliberate override from flagging itself the moment anything is
        recomputed — the athlete overruling `as_intended` with `under` has not
        been contradicted by a rescore that still says `as_intended`. What is
        new is a new opinion.

        The declaration itself is never touched.
        """
        held = await self._declarations.for_session(session_id)
        if held is None:
            return
        contested = (
            suggested is not held.declared_verdict
            and suggested is not held.suggested_at_declaration
        )
        if contested == held.contested and (
            not contested or held.contested_verdict is suggested
        ):
            return
        held.contested = contested
        held.contested_at = dt.datetime.now(dt.UTC) if contested else None
        held.contested_verdict = suggested if contested else None
        await self._declarations.add(held)
        await self._audit.record(
            actor=actor,
            action="session.verdict_contested"
            if contested
            else "session.verdict_uncontested",
            entity_type=DECLARATION_ENTITY_TYPE,
            entity_id=held.id,
            payload={
                "session_id": str(session_id),
                "declared_verdict": held.declared_verdict.value,
                "suggested_verdict": suggested.value,
            },
        )

    # --- the alignment offset (A7.1) -----------------------------------------

    async def set_alignment_offset(
        self, session_id: uuid.UUID, *, offset_s: int, actor: Actor
    ) -> SessionAlignmentRow:
        """Slide the planned timeline and rescore against the new pairing.

        The control the addendum asks for, and it is functional rather than
        cosmetic: the offset goes into `app.domain.alignment.align`, which
        changes which detected effort answers which prescribed step, which
        changes the adherence and pacing axes. Creating the version and
        rescoring happen in one transaction, so a score never references an
        alignment version that is not the one in force.

        A `displaced` link is refused. It says the athlete trained something
        else, so the session is scored standalone: :meth:`_score` never aligns
        it and the score it writes references no alignment version. Sliding the
        offset there would append alignment version *n+1* that nothing points
        at — a 200 and a new version whose only effect is to falsify the
        promise this method makes about the one in force.

        Raises:
            NotFoundError: When no session has that id.
            ValidationError: When the session has no prescription to align to,
                when the link is `displaced`, or when the offset is
                implausible.
        """
        if abs(offset_s) > MAX_ALIGNMENT_OFFSET_S:
            raise ValidationError(
                f"An alignment offset of {offset_s} s is further than the "
                f"{MAX_ALIGNMENT_OFFSET_S} s a correction can plausibly be; "
                "the recording and the prescription are not the same session."
            )
        row = await self._sessions.get(session_id)
        if row is None:
            raise NotFoundError(f"Session {session_id} not found")
        link = await self._links.for_session(session_id)
        if link is None or link.status is MatchLinkStatus.PENDING:
            raise ValidationError(NOT_SCOREABLE)
        if link.status is MatchLinkStatus.DISPLACED:
            raise ValidationError(NOT_ALIGNABLE_DISPLACED)
        planned = await self._planned.get(link.planned_session_id)
        if planned is None:  # pragma: no cover — the link's FK forbids it
            raise ValidationError(NOT_SCOREABLE)
        intent = planned.current_intent
        with domain_rules():
            body = workout_body_from_json(intent.structure)
        if not isinstance(body, EnduranceWorkout):
            raise ValidationError(
                "A strength session's sets are paired by position, not on a "
                "timeline, so there is no offset to slide."
            )
        _, alignment_row = await self._align(
            row,
            planned,
            body,
            await self._pins(intent.pinned_anchor_versions),
            _intervals(await self._metrics.get_current(session_id)),
            actor=actor,
            offset_s=offset_s,
        )
        await self._score(session_id, actor=actor, reason=REASON_OFFSET_CHANGED)
        await commit(self._session)
        await self._session.refresh(alignment_row)
        return alignment_row

    # --- testimony (WP-7.2, WP-7.3) ------------------------------------------

    async def declare(
        self,
        session_id: uuid.UUID,
        *,
        verdict: Verdict,
        actor: Actor,
        reasons: Sequence[Reason] = (),
        note: str | None = None,
    ) -> VerdictDeclarationRow:
        """Record what the athlete says the session was.

        The one rule the coaching agent can never talk its way around: the
        actor must be the athlete. Checked here rather than in the adapter
        because an adapter-level check protects one adapter, and this has to
        hold for the API, for MCP and for anything after them (WP-7.2).

        A declaration of anything but `as_intended` needs one to three
        reasons, in order of primacy. Declaring again replaces the standing
        declaration — the athlete is allowed to change their mind — and clears
        any `contested` flag, because the athlete has now ruled on the
        machine's current opinion.

        Raises:
            ForbiddenError: When the actor is not the athlete.
            NotFoundError: When no session has that id.
            ValidationError: When the reasons do not fit WP-7.3's rule.
        """
        _require_athlete(actor, "declare a verdict")
        row = await self._sessions.get(session_id)
        if row is None:
            raise NotFoundError(f"Session {session_id} not found")
        ordered = _check_reasons(verdict, reasons, note)

        # The score **in force**, not the chain tip: `suggested_at_declaration`
        # is what the athlete was looking at when they ruled, and a judgement
        # whose link has gone is not on screen for them to rule on.
        current = await self.get_current(session_id)
        link = await self._links.for_session(session_id)
        held = await self._declarations.for_session(session_id)
        now = dt.datetime.now(dt.UTC)
        previous = held.declared_verdict if held is not None else None
        if held is None:
            held = VerdictDeclarationRow(session_id=session_id)
        held.planned_session_id = link.planned_session_id if link else None
        held.declared_verdict = verdict
        held.declared_at = now
        held.suggested_at_declaration = (
            current.suggested_verdict if current is not None else None
        )
        held.score_version_id = current.id if current is not None else None
        held.contested = False
        held.contested_at = None
        held.contested_verdict = None
        held = await self._declarations.add(held)

        # An `as_intended` declaration need not carry reasons, and one that
        # carries none appends no version rather than an empty one: a reasons
        # chain whose tip says nothing is indistinguishable from silence.
        appended = (
            await self._append_reasons(
                declaration_id=held.id,
                planned_session_id=None,
                reasons=ordered,
                note=note,
                actor=actor,
                revision_reason=None,
            )
            if ordered or note
            else None
        )
        await self._audit.record(
            actor=actor,
            action="session.verdict_declared",
            entity_type=DECLARATION_ENTITY_TYPE,
            entity_id=held.id,
            payload={
                "session_id": str(session_id),
                "declared_verdict": verdict.value,
                "previous_verdict": previous.value if previous else None,
                "suggested_verdict": (
                    current.suggested_verdict.value if current is not None else None
                ),
                "score_version": current.version if current is not None else None,
                "reasons": [one.value for one in ordered],
                "reasons_version": appended.version if appended else None,
            },
        )
        await commit(self._session)
        await self._session.refresh(held)
        return held

    async def revise_reasons(
        self,
        session_id: uuid.UUID,
        *,
        reasons: Sequence[Reason],
        actor: Actor,
        note: str | None = None,
        revision_reason: str | None = None,
    ) -> SessionReasonsRow:
        """Append a new version of a declared session's reasons.

        Append-only: what the athlete said last week stays readable beside
        what they say now, which is the whole difference between testimony and
        a form field.

        Raises:
            ForbiddenError: When the actor is not the athlete.
            NotFoundError: When the session has no declaration to explain.
            ValidationError: When the reasons do not fit WP-7.3's rule.
        """
        _require_athlete(actor, "revise the reasons for a session")
        held = await self._declarations.for_session(session_id)
        if held is None:
            raise NotFoundError(
                f"Session {session_id} has no declared verdict, so there is "
                "nothing for these reasons to explain. Declare one first."
            )
        ordered = _check_reasons(held.declared_verdict, reasons, note, revising=True)
        appended = await self._append_reasons(
            declaration_id=held.id,
            planned_session_id=None,
            reasons=ordered,
            note=note,
            actor=actor,
            revision_reason=revision_reason or "revised by the athlete",
        )
        await commit(self._session)
        await self._session.refresh(appended)
        return appended

    async def answer_prompt(
        self,
        planned_session_id: uuid.UUID,
        *,
        reasons: Sequence[Reason],
        actor: Actor,
        note: str | None = None,
    ) -> SessionReasonsRow:
        """Answer the evening prompt about a missed session (WP-7.3).

        A missed session has no recording and no declaration, so the reasons
        hang off the planned session itself. Appending is what closes the
        prompt; appending again after that is a revision, exactly as it is on
        the declared side.

        Raises:
            ForbiddenError: When the actor is not the athlete.
            NotFoundError: When no planned session has that id.
            ValidationError: When the reasons do not fit WP-7.3's rule.
        """
        _require_athlete(actor, "answer an evening prompt")
        planned = await self._planned.get(planned_session_id)
        if planned is None:
            raise NotFoundError(f"Planned session {planned_session_id} not found")
        ordered = _check_reasons(None, reasons, note)
        existing = await self._reasons.for_planned_session(planned_session_id)
        appended = await self._append_reasons(
            declaration_id=None,
            planned_session_id=planned_session_id,
            reasons=ordered,
            note=note,
            actor=actor,
            revision_reason="revised by the athlete" if existing else None,
        )
        prompt = await self._prompts.for_planned_session(planned_session_id)
        if prompt is not None and prompt.status is not EveningPromptStatus.ANSWERED:
            prompt.status = EveningPromptStatus.ANSWERED
            prompt.resolved_at = dt.datetime.now(dt.UTC)
            await self._prompts.add(prompt)
            await self._audit.record(
                actor=actor,
                action="evening_prompt.answered",
                entity_type=PROMPT_ENTITY_TYPE,
                entity_id=prompt.id,
                payload={
                    "planned_session_id": str(planned_session_id),
                    "reasons": [one.value for one in ordered],
                },
            )
        await commit(self._session)
        await self._session.refresh(appended)
        return appended

    async def _append_reasons(
        self,
        *,
        declaration_id: uuid.UUID | None,
        planned_session_id: uuid.UUID | None,
        reasons: Sequence[Reason],
        note: str | None,
        actor: Actor,
        revision_reason: str | None,
    ) -> SessionReasonsRow:
        """Append one reasons version and close the previous one off."""
        if declaration_id is not None:
            chain = await self._reasons.for_declaration(declaration_id)
        elif planned_session_id is not None:
            chain = await self._reasons.for_planned_session(planned_session_id)
        else:  # pragma: no cover — the check constraint forbids it
            raise ValidationError("Reasons must be about a session or a plan entry")
        # pyrefly: ignore[bad-specialization]
        # See `_align` for why the protocol match is suppressed.
        version = next_version(chain)
        # pyrefly: ignore[bad-specialization]
        current = _tip(chain)
        appended = await self._reasons.add(
            SessionReasonsRow(
                declaration_id=declaration_id,
                planned_session_id=planned_session_id,
                version=version,
                as_of=dt.datetime.now(dt.UTC),
                recompute_reason=(
                    None
                    if version == FIRST_VERSION
                    else _bounded(revision_reason or "revised")
                ),
                reasons=[one.value for one in reasons],
                note=note,
                recorded_by=str(actor),
            )
        )
        if current is not None:
            current.superseded_by = appended.id
            await self._reasons.add(current)
        await self._audit.record(
            actor=actor,
            action=(
                "session.reasons_recorded"
                if version == FIRST_VERSION
                else "session.reasons_revised"
            ),
            entity_type=REASONS_ENTITY_TYPE,
            entity_id=appended.id,
            payload={
                "declaration_id": str(declaration_id) if declaration_id else None,
                "planned_session_id": (
                    str(planned_session_id) if planned_session_id else None
                ),
                "version": version,
                "reasons": [one.value for one in reasons],
                "note": note,
            },
        )
        return appended

    # --- the expiry sweep (WP-7.3) -------------------------------------------

    async def expire_prompts(
        self, *, actor: Actor, now: dt.datetime | None = None
    ) -> Sequence[EveningPromptRow]:
        """Close every evening prompt whose 72 hours have run out.

        The deadline is stored on the prompt when it is raised, so this job
        agrees with no constant of its own. Each expiry records the auto-reason
        `not_provided` against the planned session: silence is an answer we
        asked for and did not get, and writing it down is what keeps the
        coaching agent from reading it as assent.

        Idempotent — an already-terminal prompt is not a candidate.

        The batch is taken over prompts that **have** expired, oldest deadline
        first, rather than over the newest pending ones: paging before
        filtering starved the overdue prompts the sweep exists for whenever the
        pending backlog was larger than one batch.

        Returns:
            The prompts expired, oldest deadline first.
        """
        moment = now or dt.datetime.now(dt.UTC)
        settings = get_settings()
        due = await self._prompts.expired(
            now=moment, limit=settings.scoring.prompt_expiry_batch
        )
        expired: list[EveningPromptRow] = []
        for prompt in due:
            prompt.status = EveningPromptStatus.EXPIRED
            prompt.resolved_at = moment
            await self._prompts.add(prompt)
            await self._append_reasons(
                declaration_id=None,
                planned_session_id=prompt.planned_session_id,
                reasons=(Reason.NOT_PROVIDED,),
                note=None,
                actor=actor,
                revision_reason="the evening prompt expired unanswered",
            )
            await self._audit.record(
                actor=actor,
                action="evening_prompt.expired",
                entity_type=PROMPT_ENTITY_TYPE,
                entity_id=prompt.id,
                payload={
                    "planned_session_id": str(prompt.planned_session_id),
                    "expires_at": prompt.expires_at.isoformat(),
                    "auto_reason": Reason.NOT_PROVIDED.value,
                },
            )
            expired.append(prompt)
        if expired:
            await commit(self._session)
        return expired


# --- assembling the domain's inputs ---------------------------------------------


def _inputs(
    *,
    row: SessionRow,
    body: WorkoutBody,
    criteria: Sequence[SuccessCriterion],
    axes: Sequence[ScoringAxis],
    purpose: Purpose,
    anchors: Mapping[AnchorType, PinnedAnchor],
    columns: SessionColumns,
    alignment: Alignment | None,
    intervals: Sequence[WorkInterval],
    standalone: bool,
) -> ScoringInputs:
    """Everything the pure scorer needs, read off the two sides.

    Strength and endurance fill different halves, exactly as the matching
    evidence does: a gym session has sets and no timeline, a ride has a
    timeline and no sets.
    """
    values = {
        anchor_type: pinned.version.value for anchor_type, pinned in anchors.items()
    }
    if isinstance(body, StrengthWorkout):
        logged = sorted(row.logged_sets, key=lambda one: one.set_index)
        return ScoringInputs(
            purpose=purpose,
            axes=tuple(axes),
            criteria=tuple(criteria),
            anchors=values,
            standalone=standalone,
            planned_sets=body.total_sets,
            performed_sets=len(logged) if logged else None,
            prescribed_loads_kg=_prescribed_loads(body),
            performed_loads_kg=tuple(one.load_kg for one in logged),
        )
    steps = tuple(flatten(body))
    return ScoringInputs(
        purpose=purpose,
        axes=tuple(axes),
        criteria=tuple(criteria),
        anchors=values,
        standalone=standalone,
        steps=steps,
        planned_duration_s=total_duration_s(body),
        actual_duration_s=session_duration_s(row),
        channels=columns,
        scored_steps=_scored_steps(steps, alignment, intervals, body, anchors),
        excluded_steps=(
            tuple(one.step_index for one in alignment.excluded) if alignment else ()
        ),
        unmatched_steps=alignment.unmatched_steps if alignment else (),
    )


def _prescribed_loads(body: StrengthWorkout) -> tuple[float | None, ...]:
    """One entry per prescribed **set**, in execution order.

    Expanded from the prescription lines because that is the unit the logged
    sets are paired against (`app.domain.alignment.align_strength`): "3 × 8 at
    80 kg" is three sets, and comparing it to one logged set would say the
    athlete lifted a third of what was asked.
    """
    loads: list[float | None] = []
    for prescription in body.prescriptions:
        load = prescription.load
        kilograms = load.value if load.kind is LoadKind.KG else None
        loads.extend([kilograms] * prescription.sets)
    return tuple(loads)


def _scored_steps(
    steps: Sequence[FlatStep],
    alignment: Alignment | None,
    intervals: Sequence[WorkInterval],
    body: EnduranceWorkout,
    anchors: Mapping[AnchorType, PinnedAnchor],
) -> tuple[ScoredStep, ...]:
    """Pair each kept alignment with the rows and the targets it was ridden at.

    ``intervals`` is the **same list** the alignment was made against — the one
    stored with the metric artefact — so an
    `app.domain.alignment.AlignedStep`'s ``interval_index`` addresses it
    directly. Re-detecting the efforts here could disagree with what was
    paired, and the pairing is what the score is about.
    """
    if alignment is None:
        return ()
    by_index = {step.index: step for step in steps}
    targets = _targets(body, anchors)
    scored: list[ScoredStep] = []
    for pair in alignment.aligned:
        step = by_index.get(pair.step_index)
        if step is None or not 0 <= pair.interval_index < len(intervals):
            continue  # pragma: no cover — `align` only names what it was given
        interval = intervals[pair.interval_index]
        scored.append(
            ScoredStep(
                step_index=pair.step_index,
                repetition=step.repetition,
                # The block the repetition counts within: two sibling blocks
                # both number their iterations from 1, so the pacing axis
                # needs both halves to tell one repetition from another.
                block=step.block,
                confidence=pair.confidence,
                start_index=interval.start_index,
                end_index=interval.end_index,
                targets=targets.get(pair.step_index, {}),
            )
        )
    return tuple(scored)


def _target_watts(
    body: EnduranceWorkout, anchors: Mapping[AnchorType, PinnedAnchor]
) -> dict[int, float]:
    """Prescribed watts per flat-step index, resolved against the pins."""
    return {
        index: channels[Channel.POWER]
        for index, channels in _targets(body, anchors).items()
        if Channel.POWER in channels
    }


def _targets(
    body: EnduranceWorkout, anchors: Mapping[AnchorType, PinnedAnchor]
) -> dict[int, dict[Channel, float]]:
    """The midpoint of each step's resolved target, per channel.

    A `Band`'s bounds are fractions of the step's own target, so the midpoint
    of the prescribed range is what they are fractions *of*. A target nothing
    resolves is simply absent — the criterion covering it then reports itself
    unevaluable rather than banding around zero.
    """
    return {step.index: _step_targets(step) for step in resolve_steps(body, anchors)}


def _step_targets(step: ResolvedStep) -> dict[Channel, float]:
    """One resolved step's per-channel midpoints."""
    midpoints: dict[Channel, float] = {}
    for target in step.start_targets:
        if target.resolved_low is None or target.resolved_high is None:
            continue
        midpoints[target.channel] = (target.resolved_low + target.resolved_high) / 2
    return midpoints


def _intervals(row: SessionMetricsRow | None) -> list[WorkInterval]:
    """The detected work intervals stored with the metric artefact.

    Read back rather than re-detected: the intervals table, the structure hint
    and the scoring axes have to be looking at the same efforts, and a second
    detection run against a different smoothing would give them three answers.
    """
    payload = row.payload if row is not None else None
    stored = (payload or {}).get("intervals") if isinstance(payload, Mapping) else None
    if not isinstance(stored, list):
        return []
    intervals: list[WorkInterval] = []
    for entry in stored:
        if not isinstance(entry, Mapping):  # pragma: no cover — written by us
            continue
        intervals.append(
            WorkInterval(
                start_index=int(entry["start_index"]),
                end_index=int(entry["end_index"]),
                duration_s=int(entry["duration_s"]),
                average_power=entry.get("average_power"),
                max_power=entry.get("max_power"),
                average_hr=entry.get("average_hr"),
            )
        )
    return intervals


def _offset_of(row: SessionAlignmentRow | None) -> int:
    """The offset in force, or zero before anything has been corrected."""
    return row.offset_s if row is not None else 0


def _tip(chain: Sequence[SessionReasonsRow]) -> SessionReasonsRow | None:
    """The unsuperseded version of a reasons chain, or ``None``."""
    open_rows = [row for row in chain if row.superseded_by is None]
    return max(open_rows, key=lambda row: row.version) if open_rows else None


def _bounded(reason: str) -> str:
    """Trim a stored reason to the column's width."""
    return reason[:MAX_REASON_LENGTH]


def _require_athlete(actor: Actor, what: str) -> None:
    """Refuse anything but the athlete's own hand (WP-7.2).

    Raises:
        ForbiddenError: When the actor is the agent or the system.
    """
    if actor.kind is not ActorKind.ATHLETE:
        raise ForbiddenError(
            f"Only the athlete may {what}. This is the athlete's own account "
            "of the session, and an agent that could write it would be "
            "inventing testimony to read back later."
        )


def _check_reasons(
    verdict: Verdict | None,
    reasons: Sequence[Reason],
    note: str | None,
    *,
    revising: bool = False,
) -> tuple[Reason, ...]:
    """Apply WP-7.3's rule to a set of reasons, in primacy order.

    One to three, no duplicates, and **required** whenever the thing being
    explained is not `as_intended`. An `as_intended` declaration may carry
    them — "felt good" is worth recording — but is not asked for them.

    Raises:
        ValidationError: When the list breaks any of those.
    """
    ordered = tuple(reasons)
    if len(set(ordered)) != len(ordered):
        raise ValidationError(
            "Each reason may be given once; the order is the primacy, so a "
            "repeated reason has no second place to hold."
        )
    if len(ordered) > MAX_REASONS:
        raise ValidationError(
            f"At most {MAX_REASONS} reasons, in order of primacy; "
            f"{len(ordered)} were given."
        )
    required = revising or (verdict is not None and verdict is not Verdict.AS_INTENDED)
    if required and len(ordered) < MIN_REASONS:
        raise ValidationError(
            f"At least {MIN_REASONS} reason is needed here. Pick "
            f"{Reason.NOT_PROVIDED.value} if you would rather not say."
        )
    if note is not None and len(note) > MAX_REASON_NOTE_CHARS:
        raise ValidationError(
            f"The note may be at most {MAX_REASON_NOTE_CHARS} characters."
        )
    return ordered


# --- the scheduled sweep ---------------------------------------------------------


async def run_prompt_expiry() -> None:
    """The scheduled sweep. Never raises — a failed run must not kill the job."""
    try:
        async with session_scope() as session:
            expired = await ScoringService.from_session(session).expire_prompts(
                actor=Actor.system()
            )
        logger.info("prompt_expiry_ran", expired=len(expired))
    except Exception:  # noqa: BLE001 — a scheduler job that raises stops running
        logger.exception("prompt_expiry_failed")


def register_prompt_expiry_job(scheduler: BaseScheduler) -> None:
    """Register the evening-prompt expiry sweep on the application scheduler.

    Registered by the work package that needs it, like the inbox and missed
    sweeps, rather than in `app.core.scheduler`, which owns no jobs of its own.
    ``coalesce`` and ``max_instances=1`` because the sweep is idempotent and two
    of them over one backlog would race for nothing.
    """
    interval = get_settings().scoring.prompt_expiry_interval_seconds
    scheduler.add_job(
        run_prompt_expiry,
        "interval",
        seconds=interval,
        id=PROMPT_EXPIRY_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

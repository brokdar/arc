"""Plan-change proposals: the use-cases behind invariant 6.

The coaching agent does not edit the plan. It **proposes**, and the athlete
answers — or does not, in which case the committed plan stands. Everything in
this module exists to make that sentence true under the ways it could quietly
stop being true:

* a proposal computed against a plan that has since moved on would apply a
  change nobody meant, so every targeted change carries the intent version it
  was written against and that token is checked **twice** — when the proposal
  is written and again when it is accepted (WP-8.3's optimistic concurrency);
* a proposal applied change-by-change could half-apply, so :meth:`accept`
  stages all of them through `PlannedSessionService`'s ``stage_*`` verbs and
  commits **once** (that split exists for this caller);
* two proposals about one session would let the athlete accept a plan neither
  of them describes, so a new proposal touching a session that already has an
  open one supersedes it, linked both ways and audited;
* a proposal nobody answers would sit there being maybe-true, so it expires,
  and the default on expiry is that nothing happens;
* and a proposal about a session the athlete has *already ridden* is not a
  question any more, so an activity arriving on the same date and discipline
  resolves it (`resolved_by_reality`).

**Nothing here scores, and nothing here declares.** Scoring runs against the
committed plan; a pending proposal is not the plan, and a lapsed one never
was.

**The dry run writes nothing at all** — no proposal, no audit row, not even
the lazily-bootstrapped athlete profile (see
`app.services.guardrails.current_profile`). "Check before you act" has to be
free, or an agent told to dry-run first is being told to spend its budget
twice.
"""

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Self

from apscheduler.schedulers.base import BaseScheduler
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    RedFlagError,
    ValidationError,
    domain_rules,
)
from app.core.logging import get_logger
from app.domain.activity import SessionDiscipline, as_planned_discipline
from app.domain.actor import Actor
from app.domain.athlete import Discipline
from app.domain.criteria import criteria_to_json
from app.domain.prediction import PredictedLoad, PredictedVolume
from app.domain.proposals import (
    MAX_CHANGES,
    MAX_RATIONALE_CHARS,
    MAX_RESOLUTION_NOTE_CHARS,
    ChangeKind,
    CreateChange,
    DeleteChange,
    MoveChange,
    PlanChange,
    ProposalStatus,
    UpdateChange,
    changes_from_json,
    changes_to_json,
    check_transition,
    intensifies,
    kind_of,
    target_of,
)
from app.domain.purpose import Purpose
from app.domain.sessions import SessionStatus
from app.domain.workout import workout_body_to_json
from app.persistence.audit import AuditRepository
from app.persistence.db import commit, refresh, session_scope
from app.persistence.planned_sessions import PlannedSessionRow
from app.persistence.proposals import PlanProposalRepository, PlanProposalRow
from app.persistence.scoring import SessionReasonsRepository
from app.services.guardrails import check_write_cap, current_profile, is_agent
from app.services.planned_sessions import (
    PlannedSessionService,
    PrescriptionPreview,
    SessionResolution,
)

logger = get_logger(__name__)

#: `entity_type` written on this use-case's audit rows.
ENTITY_TYPE = "plan_proposal"

#: Job id of the expiry sweep on the application scheduler.
EXPIRY_JOB_ID = "proposal-expiry"

# Aliases, not `list[...]` written out: inside the service's class body `list`
# resolves to its own `list` *method*, and the annotation becomes a subscript
# of a coroutine (caught by pyrefly, as it is on
# `PlannedSessionService.default_criteria`).
type DiffEntries = list[dict[str, Any]]
type ProposalRows = list[PlanProposalRow]


@dataclass(frozen=True, slots=True)
class ProposalOutcome:
    """What :meth:`ProposalService.propose` answers with.

    Args:
        diff: What the changes would do, per entity, before and after. Always
            present — it is the whole answer on a dry run.
        proposal: The stored proposal, or ``None`` on a dry run.
        superseded: The open proposals this one replaced — or, on a dry run,
            the ones it **would** replace, still pending and untouched. Usually
            empty. A dry run that reported nothing here would be answering the
            most consequential question about a proposal ("what does this
            displace?") with a confident and wrong "nothing".
    """

    diff: list[dict[str, Any]]
    proposal: PlanProposalRow | None
    superseded: tuple[PlanProposalRow, ...] = ()


class ProposalService:
    """Use-cases for plan-change proposals. Raises AppError subclasses."""

    def __init__(
        self,
        session: AsyncSession,
        repository: PlanProposalRepository,
        audit: AuditRepository,
        planned: PlannedSessionService,
        reasons: SessionReasonsRepository,
    ) -> None:
        self._session = session
        self._repository = repository
        self._audit = audit
        self._planned = planned
        self._reasons = reasons

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(
            session,
            PlanProposalRepository(session),
            AuditRepository(session),
            PlannedSessionService.from_session(session),
            SessionReasonsRepository(session),
        )

    # --- reads ---------------------------------------------------------------

    async def list(
        self,
        *,
        status: ProposalStatus | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[PlanProposalRow], int]:
        """Return a page of proposals, newest first, plus the total."""
        return await self._repository.list(status=status, offset=offset, limit=limit)

    async def get(self, proposal_id: uuid.UUID) -> PlanProposalRow:
        """Return one proposal.

        Raises:
            NotFoundError: When no proposal has that id.
        """
        row = await self._repository.get(proposal_id)
        if row is None:
            raise NotFoundError(f"Proposal {proposal_id} not found")
        return row

    # --- writing a proposal --------------------------------------------------

    async def propose(
        self,
        *,
        actor: Actor,
        changes: Sequence[PlanChange],
        rationale: str,
        expires_at: dt.datetime,
        dry_run: bool = False,
    ) -> ProposalOutcome:
        """Suggest a set of plan changes, or say what they would do.

        The order of the checks is the order of their costs to the athlete:
        the rate cap first (a capped agent is answered without touching the
        plan at all), then the shape of the request — including **one change
        per planned session**, see :func:`_check_distinct_targets` — then the
        changes against current state — every target must exist and every
        concurrency token must match — then the red-flag rule, which needs the
        computed diff to decide whether anything is being added or intensified.

        Args:
            actor: Who is proposing. Agent actors are rate-capped and
                red-flag-restrained; the athlete is neither.
            changes: One to `app.domain.proposals.MAX_CHANGES` changes.
            rationale: Why. Required and non-empty (invariant 6).
            expires_at: When the proposal stops standing. Aware, and in the
                future — a proposal that has already lapsed is not one.
            dry_run: Compute and return the diff, persisting nothing.

        Returns:
            The diff, plus the stored proposal unless this was a dry run.

        Raises:
            RateLimitedError: When the agent's trailing-hour cap is spent.
            ValidationError: When the request is malformed or a change is
                illegal against current state.
            NotFoundError: When a change targets a planned session that does
                not exist.
            ConflictError: When a change's ``expected_intent_version`` is not
                the version in force.
            RedFlagError: When the athlete's illness/injury flag is up and a
                change adds or intensifies work.
        """
        if not dry_run:
            await check_write_cap(self._session, actor)
        text = _clean_rationale(rationale)
        _check_expiry(expires_at)
        if not changes:
            raise ValidationError("A proposal must carry at least one change")
        if len(changes) > MAX_CHANGES:
            # A proposal is something a human reads and answers in one
            # sitting; one that rewrites forty sessions is not reviewable,
            # which is the same as not being a proposal.
            raise ValidationError(
                f"A proposal may carry at most {MAX_CHANGES} changes, got "
                f"{len(changes)}"
            )
        _check_distinct_targets(changes)

        diff = await self._diff(changes)
        await self._check_red_flag(actor, diff)
        if dry_run:
            # The overlap is *read* on a dry run and only closed on a real
            # one: what a proposal displaces is part of what it does, and a
            # preview that always said "nothing" would hide the case the
            # athlete most needs to see — a standing suggestion about the same
            # session being thrown away by this one.
            return ProposalOutcome(
                diff=diff,
                proposal=None,
                superseded=tuple(await self._open_overlapping(diff)),
            )

        superseded = await self._supersede(diff, actor=actor)
        row = await self._repository.add(
            PlanProposalRow(
                status=ProposalStatus.PENDING,
                rationale=text,
                changes=changes_to_json(changes),
                diff=diff,
                expires_at=expires_at,
                created_by=str(actor),
                # The forward link is one column and can name one predecessor;
                # a multi-entity proposal that displaces two open ones is rare
                # and both back-links (`superseded_by_id`) and the audit row
                # below carry the whole set, so nothing is lost that way round.
                supersedes_id=superseded[0].id if superseded else None,
            )
        )
        for old in superseded:
            old.superseded_by_id = row.id
            await self._repository.add(old)
        await self._audit.record(
            actor=actor,
            action="plan_proposal.created",
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            payload={
                "changes": [entry["kind"] for entry in diff],
                "targets": [entry["planned_session_id"] for entry in diff],
                "expires_at": expires_at.isoformat(),
                "supersedes": [str(old.id) for old in superseded],
            },
        )
        await commit(self._session)
        await refresh(self._session, row)
        return ProposalOutcome(diff=diff, proposal=row, superseded=tuple(superseded))

    # --- answering one -------------------------------------------------------

    async def accept(self, proposal_id: uuid.UUID, *, actor: Actor) -> PlanProposalRow:
        """Apply every change in a proposal, atomically, and close it.

        The concurrency tokens are re-checked here and not merely at write
        time: a proposal can stand for days, and the plan is the athlete's to
        edit meanwhile. A stale token leaves the proposal **pending** — the
        suggestion may still be a good one against the plan as it now stands,
        and it is the agent's job, not this method's, to decide that.

        Every change is staged through `PlannedSessionService`'s ``stage_*``
        verbs, so a plan change applied from a proposal is the same operation
        (the same versioning, pinning, freeze rule and audit row) as one the
        athlete makes by hand — and all of them share one transaction, so a
        failure anywhere leaves the plan exactly as it was.

        **Attribution.** The staged changes are recorded as the *accepting
        athlete's* writes, because they are: the agent suggested, the athlete
        decided, and an audit trail that credited the agent would say the plan
        changed itself. What the agent did is recorded too — one
        `plan_proposal.change_applied` row per change, naming the proposal and
        who wrote it — so the trail answers both "who changed this session"
        and "whose idea was it".

        Raises:
            NotFoundError: When no proposal has that id.
            ConflictError: When the proposal is not pending, has expired, or
                a change's expected intent version is no longer in force.
        """
        row = await self.get(proposal_id)
        self._require_transition(row, ProposalStatus.ACCEPTED)
        moment = dt.datetime.now(dt.UTC)
        if row.expires_at <= moment:
            raise ConflictError(
                f"This proposal expired at {row.expires_at.isoformat()} and "
                "cannot be accepted. The committed plan stands."
            )

        with domain_rules():
            changes = changes_from_json(row.changes)
        # Every token first, then every change: a proposal is accepted whole,
        # so the second change must not have landed when the third is refused.
        # Sound because no two changes share a target (`_check_distinct_targets`
        # refuses that when the proposal is written) — otherwise the second of
        # them would apply to a version it validated against but no longer has.
        for index, change in enumerate(changes):
            await self._check_applies(index, change)

        for index, change in enumerate(changes):
            applied = await self._apply(change, actor=actor)
            await self._audit.record(
                actor=actor,
                action="plan_proposal.change_applied",
                entity_type=ENTITY_TYPE,
                entity_id=row.id,
                payload={
                    "index": index,
                    "kind": kind_of(change).value,
                    "planned_session_id": None if applied is None else str(applied),
                    "proposed_by": row.created_by,
                },
            )

        row.status = ProposalStatus.ACCEPTED
        row.resolved_at = moment
        row = await self._repository.add(row)
        await self._audit.record(
            actor=actor,
            action="plan_proposal.accepted",
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            payload={"changes": len(changes), "proposed_by": row.created_by},
        )
        await commit(self._session)
        await refresh(self._session, row)
        return row

    async def reject(
        self, proposal_id: uuid.UUID, *, actor: Actor, reason: str | None = None
    ) -> PlanProposalRow:
        """Decline a proposal, optionally saying why.

        The reason is stored verbatim and never parsed: it is the athlete
        telling the coach what it got wrong, and it is the seed of the
        coach-quality loop rather than an input to any rule.

        Raises:
            NotFoundError: When no proposal has that id.
            ConflictError: When the proposal is not pending.
            ValidationError: When the reason is longer than the column.
        """
        row = await self.get(proposal_id)
        self._require_transition(row, ProposalStatus.REJECTED)
        note = _clean_note(reason)
        row.status = ProposalStatus.REJECTED
        row.resolved_at = dt.datetime.now(dt.UTC)
        row.resolution_note = note
        row = await self._repository.add(row)
        await self._audit.record(
            actor=actor,
            action="plan_proposal.rejected",
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            payload={"reason": note, "proposed_by": row.created_by},
        )
        await commit(self._session)
        await refresh(self._session, row)
        return row

    # --- the two automatic exits ---------------------------------------------

    async def expire(
        self, *, actor: Actor, now: dt.datetime | None = None
    ) -> Sequence[PlanProposalRow]:
        """Lapse every proposal whose deadline has passed. Changes no plan.

        Default-on-expiry is that the committed plan stands, so this touches
        nothing but the proposals themselves — which is the whole point of
        having a default: silence is not assent, and it is not refusal either,
        it is the plan continuing.

        Idempotent, and batched **over what has expired, oldest deadline
        first**: paging before filtering starves the overdue rows the
        sweep exists for whenever the pending backlog is larger than a batch.

        Returns:
            The proposals lapsed, oldest deadline first.
        """
        moment = now or dt.datetime.now(dt.UTC)
        due = await self._repository.expired(
            now=moment, limit=get_settings().proposals.expiry_batch
        )
        lapsed: ProposalRows = []
        for row in due:
            row.status = ProposalStatus.LAPSED
            row.resolved_at = moment
            await self._repository.add(row)
            await self._audit.record(
                actor=actor,
                action="plan_proposal.lapsed",
                entity_type=ENTITY_TYPE,
                entity_id=row.id,
                payload={
                    "expires_at": row.expires_at.isoformat(),
                    "proposed_by": row.created_by,
                    "default_on_expiry": "the committed plan stands",
                },
            )
            lapsed.append(row)
        if lapsed:
            await commit(self._session)
        return lapsed

    async def resolve_by_reality(
        self,
        *,
        actor: Actor,
        date: dt.date,
        discipline: Discipline,
        session_id: uuid.UUID | None = None,
    ) -> Sequence[PlanProposalRow]:
        """Close pending proposals that a session just made moot.

        The athlete rode. Whatever the coach was suggesting about *that day in
        that discipline* is no longer a question the athlete can usefully be
        asked — accepting it afterwards would rewrite the plan a recorded
        session is about to be scored against, and asking about it at all
        implies the day is still open.

        Two things close a proposal here, and the second exists because the
        first is not enough:

        * the recording lands on a **day and discipline** the proposal has
          something to say about — the pairing a plan entry is placed by, in
          either direction, since moving a session *off* Tuesday is as much
          about Tuesday as moving one onto it;
        * or one of its targets has since **acquired a match**. WP-6 links a
          recording to a planned session up to a day either side
          (``CANDIDATE_WINDOW_DAYS``), so a ride on the 12th can answer a
          session planned for the 11th while this method's date test sees two
          different days. A link is the truest form of "the athlete has
          already trained this", and it is the thing scoring runs on.

        Changes no plan — like expiry, this is the committed plan standing.

        Returns:
            The proposals resolved, oldest first.
        """
        moment = dt.datetime.now(dt.UTC)
        resolved: ProposalRows = []
        for row in await self._repository.pending():
            spent_day = (date, discipline) in _touched_days(row.diff)
            matched = None if spent_day else await self._matched_target(row.diff)
            if not spent_day and matched is None:
                continue
            row.status = ProposalStatus.RESOLVED_BY_REALITY
            row.resolved_at = moment
            await self._repository.add(row)
            await self._audit.record(
                actor=actor,
                action="plan_proposal.resolved_by_reality",
                entity_type=ENTITY_TYPE,
                entity_id=row.id,
                payload={
                    "date": date.isoformat(),
                    "discipline": discipline.value,
                    "session_id": None if session_id is None else str(session_id),
                    "proposed_by": row.created_by,
                    "matched_planned_session": (
                        None if matched is None else str(matched)
                    ),
                },
            )
            resolved.append(row)
        if resolved:
            await commit(self._session)
        return resolved

    async def _matched_target(
        self, diff: Sequence[Mapping[str, Any]]
    ) -> uuid.UUID | None:
        """The first planned session in a diff that now carries a match.

        One probe per target of each *pending* proposal, which is the set the
        inbox shows the athlete and not a table scan.
        """
        for target in sorted(_targets(diff)):
            if await self._planned.is_matched(target):
                return target
        return None

    # --- the diff ------------------------------------------------------------

    async def _diff(self, changes: Sequence[PlanChange]) -> DiffEntries:
        """Validate every change against current state and say what it would do.

        Read-only: it resolves, prices and compares, and writes nothing — so
        it serves the dry run and the stored proposal from one code path,
        which is what makes the dry run's answer the real answer.
        """
        entries: DiffEntries = []
        for index, change in enumerate(changes):
            try:
                entries.append(await self._diff_one(change))
            except ValidationError as exc:
                raise ValidationError(f"change {index}: {exc.detail}") from exc
        return entries

    async def _diff_one(self, change: PlanChange) -> dict[str, Any]:
        """One diff entry: the change, its target, and before/after."""
        if isinstance(change, CreateChange):
            preview = await self._planned.preview(
                purpose=change.purpose,
                workout_id=change.workout_id,
                structure=change.structure,
                success_criteria=change.success_criteria,
            )
            return {
                "kind": ChangeKind.CREATE.value,
                "planned_session_id": None,
                "date": change.date.isoformat(),
                "discipline": preview.discipline.value,
                "expected_intent_version": None,
                "before": None,
                "after": {
                    "date": change.date.isoformat(),
                    "purpose": preview.purpose.value,
                    "discipline": preview.discipline.value,
                    "status": "planned",
                    "intent_text": change.intent_text,
                    "coach_notes": change.coach_notes,
                    "workout_id": (
                        None if preview.workout_id is None else str(preview.workout_id)
                    ),
                    "success_criteria": criteria_to_json(preview.criteria),
                    "structure": workout_body_to_json(preview.body),
                    **_costs(preview.predicted_load, preview.predicted_volume),
                    **_sizes(preview.duration_s, preview.total_sets),
                },
            }

        row = await self._planned.get(change.planned_session_id)
        await self._check_token_against(row, change)
        if isinstance(change, DeleteChange):
            await self._check_deletable(row)
        resolution = (await self._planned.resolutions([row]))[row.id]
        before = _snapshot(row, resolution)
        entry: dict[str, Any] = {
            "kind": kind_of(change).value,
            "planned_session_id": str(row.id),
            "date": row.date.isoformat(),
            "discipline": row.discipline.value,
            "expected_intent_version": change.expected_intent_version,
            "before": before,
            "after": None,
        }

        if isinstance(change, DeleteChange):
            return entry
        if isinstance(change, MoveChange):
            entry["after"] = {**before, "date": change.date.isoformat()}
            return entry

        after, preview = await self._preview_update(row, change)
        entry["after"] = after
        entry["discipline"] = preview.discipline.value
        return entry

    async def _preview_update(
        self, row: PlannedSessionRow, change: UpdateChange
    ) -> tuple[dict[str, Any], PrescriptionPreview]:
        """Price a revision against the anchors **accepting it would pin**.

        Through `PlannedSessionService.preview_update`, which shares its
        resolver with `stage_update`: same purpose, same body, same criteria,
        same pins, so the stored diff is what the accept produces rather than
        an approximation of it.

        The pins are the subtle half, and getting them from the same place is
        the whole fix. Invariant 4 freezes a prescription at creation
        **or last pre-execution edit**, so revising a session nobody has ridden
        yet re-pins it to the anchors in force now: a note-only edit written
        after an FTP test really does re-price the session, and the diff has to
        say so or the athlete accepts one number and gets another. A session
        that *has* been matched keeps the pins it was executed against, and the
        preview keeps them too.

        ``before`` is priced separately, as the session stands (:func:`_snapshot`)
        — the two sides are a *this becomes that*, not two readings of one
        prescription.
        """
        # The patch's field names and value types were checked when the change
        # was built (`UpdateChange.__post_init__`), so everything read here is
        # already in the types the plan service works in.
        current = row.current_intent
        preview = await self._planned.preview_update(row, change.updates)
        after = {
            "date": _as_date(change.updates.get("date", row.date)),
            "purpose": preview.purpose.value,
            "discipline": preview.discipline.value,
            # Not proposable: a session's status is derived from what
            # the athlete actually did, so it passes through untouched.
            "status": _as_text(row.status),
            "intent_text": change.updates.get("intent_text", current.intent_text),
            "coach_notes": change.updates.get("coach_notes", current.coach_notes),
            # The link survives a body-less revision — `stage_update` leaves it
            # alone — and `preview_update` reports the same, so a note-only
            # edit no longer reads as "the prescription is being thrown away".
            "workout_id": (
                None if preview.workout_id is None else str(preview.workout_id)
            ),
            "success_criteria": criteria_to_json(preview.criteria),
            "structure": workout_body_to_json(preview.body),
            **_costs(preview.predicted_load, preview.predicted_volume),
            **_sizes(preview.duration_s, preview.total_sets),
        }
        return after, preview

    # --- the guardrails ------------------------------------------------------

    async def _check_red_flag(
        self, actor: Actor, diff: Sequence[Mapping[str, Any]]
    ) -> None:
        """Refuse an agent proposal that adds or intensifies while the flag is up.

        WP-8.4, deterministic and stated in one place. A ``create`` **adds**,
        with no further argument: a session that was not on the calendar is
        more work than no session. An ``update`` intensifies by the rule in
        `app.domain.proposals.intensifies` — a raised purpose rank, or a
        raised predicted load when both sides can honestly be priced. Moves,
        deletes and reductions pass, because the athlete must stay able to
        have the plan lightened, rearranged or cleared while unwell.

        The refusal names the offending change and quotes the rule; a
        guardrail the agent cannot read is one it can only trip over.
        """
        if not is_agent(actor):
            return
        profile = await current_profile(self._session)
        if not profile.red_flag_active:
            return
        severity = (
            profile.red_flag_severity.value
            if profile.red_flag_severity is not None
            else "unspecified"
        )
        for index, entry in enumerate(diff):
            reason = _intensifies(entry)
            if reason is None:
                continue
            raise RedFlagError(
                f"The athlete's illness/injury flag is set ({severity}), so a "
                f"proposal may not add or intensify training. Change {index} "
                f"({entry['kind']}) does: {reason}. Propose a reduction, a "
                "move or a deletion, or wait until the flag is cleared."
            )

    async def _check_applies(self, index: int, change: PlanChange) -> None:
        """Re-check at accept time that one change may still be applied.

        Three questions, all of them about the world having moved since the
        proposal was written: does the target still exist, is its concurrency
        token still in force, and — the one a token cannot answer — has the
        athlete meanwhile *trained* it. A match is the truest form of
        "this day is spent": the recording is already being scored against the
        prescription as it stands, and rewriting or moving that prescription
        now would rewrite what the ride is judged by. A 409, because the
        request is well formed and it is the state of the resource that
        refuses it.
        """
        if isinstance(change, CreateChange):
            return
        try:
            row = await self._planned.get(change.planned_session_id)
        except NotFoundError as exc:
            raise ConflictError(
                f"Change {index} targets planned session "
                f"{change.planned_session_id}, which no longer exists. The "
                "proposal stands; propose again against the plan as it is."
            ) from exc
        await self._check_token_against(row, change, index=index)
        if await self._planned.is_matched(row.id):
            raise ConflictError(
                f"Change {index}: planned session {row.id} has since been "
                "matched to a recorded session, so it is no longer a question "
                "the athlete can be asked. The committed plan stands; the ride "
                "is scored against the prescription it was executed under."
            )
        if isinstance(change, DeleteChange):
            try:
                await self._check_deletable(row)
            except ValidationError as exc:
                raise ConflictError(f"Change {index}: {exc.detail}") from exc

    async def _check_deletable(self, row: PlannedSessionRow) -> None:
        """Refuse a proposed deletion of a session the athlete has executed.

        Deleting a planned session cascades: the match link goes, the reasons
        the athlete wrote about it go, and the scores that named it are left
        pointing at nothing. None of that is a plan change, and none of
        it is the agent's to make — the agent may argue with the plan and never
        with the record of what happened. A plain unridden planned session
        stays deletable, which is what the verb is for.

        Checked when the proposal is written and again when it is accepted:
        the session may be ridden in between.

        Raises:
            ValidationError: When the session is completed, matched, or
                carries declared reasons. The message says which.
        """
        if row.status is SessionStatus.COMPLETED:
            reason = "it is marked completed"
        elif await self._planned.is_matched(row.id):
            reason = "a recorded session is matched to it"
        elif await self._reasons.for_planned_session(row.id):
            reason = "the athlete has declared reasons against it"
        else:
            return
        raise ValidationError(
            f"Planned session {row.id} cannot be deleted by proposal because "
            f"{reason}: deleting it would destroy the record of what the "
            "athlete did. Propose a revision, or move it."
        )

    async def _check_token_against(
        self,
        row: PlannedSessionRow,
        change: UpdateChange | MoveChange | DeleteChange,
        *,
        index: int | None = None,
    ) -> None:
        """Compare a change's token with the intent version in force.

        The token is the **intent chain's version** rather than a column of
        its own, because that chain is already the record of "has what this
        session is for changed since you looked" — invariant 4 made it
        append-only and monotonic, which is exactly what an optimistic
        concurrency token has to be. A second `version` column would be a
        second answer to one question.

        Raises:
            ConflictError: When they differ. The message names the entity,
                what was expected and what is in force, because the caller's
                next move is to re-read and re-propose.
        """
        current = row.current_intent.version
        if current == change.expected_intent_version:
            return
        where = "" if index is None else f"Change {index}: "
        raise ConflictError(
            f"{where}planned session {row.id} has moved on — this change was "
            f"computed against intent version "
            f"{change.expected_intent_version}, but version {current} is in "
            "force. Re-read the session and propose again."
        )

    async def _open_overlapping(
        self, diff: Sequence[Mapping[str, Any]]
    ) -> ProposalRows:
        """The open proposals a diff would displace, oldest first. Writes nothing.

        Split out of :meth:`_supersede` so the dry run and the real write
        answer "what does this displace" from one query — the same reason
        :meth:`_diff` is shared. Read-only, so it is safe on the path that
        must not touch the database at all.
        """
        targets = _targets(diff)
        if not targets:
            return []
        return [
            row
            for row in await self._repository.pending()
            if targets & _targets(row.diff)
        ]

    async def _supersede(
        self, diff: Sequence[Mapping[str, Any]], *, actor: Actor
    ) -> ProposalRows:
        """Close any open proposal about a session this one also touches.

        One open proposal per plan entity (invariant 6). Two standing at once
        would let the athlete accept both and end up with a plan neither
        describes — and would make "the proposal about Tuesday" ambiguous in
        an inbox built to answer exactly that.

        The old proposal is closed **whole**, even if only one of its changes
        overlaps: a proposal is one argument, and applying half of it applies
        an argument nobody made.
        """
        moment = dt.datetime.now(dt.UTC)
        targets = _targets(diff)
        superseded: ProposalRows = []
        for row in await self._open_overlapping(diff):
            overlap = targets & _targets(row.diff)
            row.status = ProposalStatus.SUPERSEDED
            row.resolved_at = moment
            await self._repository.add(row)
            await self._audit.record(
                actor=actor,
                action="plan_proposal.superseded",
                entity_type=ENTITY_TYPE,
                entity_id=row.id,
                payload={
                    "overlapping_planned_sessions": sorted(
                        str(target) for target in overlap
                    ),
                    "proposed_by": row.created_by,
                },
            )
            superseded.append(row)
        return superseded

    # --- applying --------------------------------------------------------------

    async def _apply(self, change: PlanChange, *, actor: Actor) -> uuid.UUID | None:
        """Stage one change in the caller's transaction. Never commits."""
        match change:
            case CreateChange():
                row = await self._planned.stage_create(
                    actor=actor,
                    date=change.date,
                    purpose=change.purpose,
                    workout_id=change.workout_id,
                    structure=change.structure,
                    intent_text=change.intent_text,
                    coach_notes=change.coach_notes,
                    success_criteria=change.success_criteria,
                )
                return row.id
            case UpdateChange():
                row = await self._planned.stage_update(
                    change.planned_session_id, change.updates, actor=actor
                )
                return row.id
            case MoveChange():
                row = await self._planned.stage_move(
                    change.planned_session_id, date=change.date, actor=actor
                )
                return row.id
            case DeleteChange():
                await self._planned.stage_delete(change.planned_session_id, actor=actor)
                return change.planned_session_id

    def _require_transition(self, row: PlanProposalRow, target: ProposalStatus) -> None:
        """Refuse a transition the state machine does not allow.

        A 409 rather than the 422 `domain_rules` would give: the request is
        perfectly well formed, and what refuses it is the state of the
        resource — which is what 409 means.

        Raises:
            ConflictError: When the transition is illegal.
        """
        try:
            check_transition(row.status, target)
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc


# --- what a change would do, as JSON --------------------------------------------


def _costs(
    load: PredictedLoad | None, volume: PredictedVolume | None
) -> dict[str, Any]:
    """The two prediction axes, as JSON. Exactly one is ever populated."""
    return {
        "predicted_load": None if load is None else round(load.load, 2),
        "predicted_volume_kg": (
            None
            if volume is None or volume.volume_load_kg is None
            else round(volume.volume_load_kg, 2)
        ),
    }


def _sizes(duration_s: int | None, total_sets: int | None) -> dict[str, Any]:
    """How much of it there is, as JSON. At most one is ever populated.

    Seconds for a ride and working sets for a lift: the sizes that survive the
    cases where neither cost axis can be computed, which is where a diff that
    carried costs alone stopped being able to say anything at all — and where
    the red-flag rule stopped being able to refuse.
    """
    return {"duration_s": duration_s, "total_sets": total_sets}


def _snapshot(row: PlannedSessionRow, resolution: SessionResolution) -> dict[str, Any]:
    """One planned session as the diff's ``before``.

    Carries its own ``discipline`` — the entry-level one is the discipline the
    change *leaves*, and a revision may change it, so a snapshot that borrowed
    it would report the wrong half of the story (see :func:`_touched_days`).

    Every field a change may touch is here, on both sides, including the two
    documents: a diff that omitted ``success_criteria`` and ``structure``
    showed an athlete "nothing differs" above an enabled Accept button for a
    change that rewrote how the session is judged.
    """
    intent = row.current_intent
    return {
        "date": row.date.isoformat(),
        "purpose": intent.purpose.value,
        "discipline": row.discipline.value,
        "status": row.status.value,
        "intent_text": intent.intent_text,
        "coach_notes": intent.coach_notes,
        "workout_id": None if intent.workout_id is None else str(intent.workout_id),
        "success_criteria": list(intent.success_criteria),
        "structure": dict(intent.structure),
        **_costs(resolution.predicted_load, resolution.predicted_volume),
        **_sizes(resolution.duration_s, resolution.total_sets),
    }


def _intensifies(entry: Mapping[str, Any]) -> str | None:
    """Whether one diff entry adds or intensifies work, and why.

    The kinds decide first: a ``create`` adds, and a ``move`` or ``delete``
    never intensifies. Only an ``update`` reaches the domain rule.
    """
    kind = entry["kind"]
    if kind == ChangeKind.CREATE.value:
        return "it plans a session that is not on the calendar"
    if kind != ChangeKind.UPDATE.value:
        return None
    before, after = entry["before"], entry["after"]
    return intensifies(
        before_purpose=Purpose(before["purpose"]),
        after_purpose=Purpose(after["purpose"]),
        before_load=_axis(before),
        after_load=_axis(after),
        before_sets=before.get("total_sets"),
        after_sets=after.get("total_sets"),
        before_duration_s=before.get("duration_s"),
        after_duration_s=after.get("duration_s"),
    )


def _axis(snapshot: Mapping[str, Any]) -> float | None:
    """The one predicted cost a snapshot carries, whichever axis it is on.

    Safe to compare only because both sides of a comparison are the same
    discipline — `intensifies` refuses a cross-discipline change before it
    ever looks at these numbers.
    """
    load = snapshot.get("predicted_load")
    return load if load is not None else snapshot.get("predicted_volume_kg")


def _targets(diff: Sequence[Mapping[str, Any]]) -> set[uuid.UUID]:
    """Every existing planned session a stored diff addresses."""
    return {
        uuid.UUID(entry["planned_session_id"])
        for entry in diff
        if entry.get("planned_session_id") is not None
    }


def _touched_days(diff: Sequence[Mapping[str, Any]]) -> set[tuple[dt.date, Discipline]]:
    """Every (date, discipline) a stored diff has anything to say about.

    Both ends of every change, in both senses. Both **days**, because a move
    is a statement about the day it leaves as much as the day it arrives on;
    and both **disciplines**, because a revision that turns Tuesday's ride
    into a lift is a statement about Tuesday's ride — an athlete who rides
    that Tuesday instead has answered the question, and a match on the after
    discipline alone would leave the proposal standing over a day that is
    already spent.
    """
    days: set[tuple[dt.date, Discipline]] = set()
    for entry in diff:
        for source in (entry, entry.get("before"), entry.get("after")):
            if not isinstance(source, Mapping):
                continue
            raw = source.get("date")
            if not isinstance(raw, str):
                continue
            try:
                # A snapshot carries its own discipline; the entry-level one
                # is the fallback for anything written without it.
                discipline = Discipline(source.get("discipline", entry["discipline"]))
            except ValueError:  # pragma: no cover — a stored value the enum lost
                continue
            days.add((dt.date.fromisoformat(raw), discipline))
    return days


# --- small coercions ------------------------------------------------------------


def _as_date(value: Any) -> str:
    """Render a date that may arrive as a date or an ISO string."""
    return value.isoformat() if isinstance(value, dt.date) else str(value)


def _as_text(value: Any) -> str:
    """Render an enum member or a plain string as its stored value."""
    return value.value if hasattr(value, "value") else str(value)


def _clean_rationale(rationale: str) -> str:
    """Validate the rationale a proposal must carry.

    Raises:
        ValidationError: When it is blank or too long. Blank rather than
            missing, because a rationale of whitespace is the shape a
            required field takes when nobody wanted to fill it in, and
            invariant 6 asks for a reason the athlete can weigh.
    """
    text = rationale.strip()
    if not text:
        raise ValidationError(
            "A proposal needs a rationale: the athlete has to be able to weigh "
            "it, and a change with no stated reason cannot be weighed."
        )
    if len(text) > MAX_RATIONALE_CHARS:
        raise ValidationError(
            f"The rationale must be at most {MAX_RATIONALE_CHARS} characters"
        )
    return text


def _check_distinct_targets(changes: Sequence[PlanChange]) -> None:
    """Refuse a proposal that says two things about one planned session.

    The concurrency token is what makes a proposal safe to hold for days, and
    it is checked **once per change against the plan as it stands** — every
    token first, then every change (see :meth:`ProposalService.accept`). Two
    changes aimed at the same session both validate against the version in
    force before either applies, so the second lands on a version it was never
    computed against: the first change moved the intent chain on, and the
    stored diff — computed the same way, one change at a time against the
    pre-apply state — describes neither of the two outcomes.

    Refusing the shape is the root fix. Making accept re-check between changes
    would only turn it into a conflict the agent cannot avoid, and merging the
    two changes here would be this service guessing at an intention nobody
    stated. One session, one change, one token, one before-and-after the
    athlete can read.

    Raises:
        ValidationError: When two changes target the same planned session. The
            message names the session and both positions, because the agent's
            next move is to combine them or drop one.
    """
    seen: dict[uuid.UUID, int] = {}
    for index, change in enumerate(changes):
        target = target_of(change)
        if target is None:
            continue
        first = seen.setdefault(target, index)
        if first != index:
            raise ValidationError(
                f"A proposal may carry at most one change per planned session, "
                f"but changes {first} and {index} both target planned session "
                f"{target}. Combine them into a single change, or propose them "
                "separately so each is answered against the plan it was "
                "computed on."
            )


def _clean_note(reason: str | None) -> str | None:
    """Validate an optional rejection reason."""
    if reason is None:
        return None
    text = reason.strip()
    if not text:
        return None
    if len(text) > MAX_RESOLUTION_NOTE_CHARS:
        raise ValidationError(
            f"The rejection reason must be at most {MAX_RESOLUTION_NOTE_CHARS} "
            "characters"
        )
    return text


def _check_expiry(expires_at: dt.datetime) -> None:
    """Reject a deadline that is naive, already past, or absurdly far off.

    The horizon is not decoration. Nothing pages
    :meth:`PlanProposalRepository.pending`, and it is scanned on every propose
    **and** on every recorded session, so a proposal dated year 9999 is a row
    the expiry sweep never reaches and every later write pays for. It is also
    not a suggestion any more: a question the athlete has ten thousand years to
    answer is one the plan can never be said to have settled.

    Raises:
        ValidationError: When ``expires_at`` carries no timezone (the column
            would refuse it as a 500), does not lie in the future (a proposal
            born lapsed asks a question nobody can answer), or lies beyond
            `ProposalSettings.max_horizon_days`.
    """
    if expires_at.tzinfo is None:
        raise ValidationError("expires_at must carry a timezone")
    now = dt.datetime.now(dt.UTC)
    if expires_at <= now:
        raise ValidationError("expires_at must be in the future")
    horizon_days = get_settings().proposals.max_horizon_days
    if expires_at > now + dt.timedelta(days=horizon_days):
        raise ValidationError(
            f"expires_at must be within {horizon_days} days: a suggestion that "
            "stands for longer than that is not one the athlete is being asked "
            "to answer."
        )


# --- wiring ---------------------------------------------------------------------


async def resolve_proposals_for_session(
    session: AsyncSession,
    *,
    actor: Actor,
    local_date: dt.date,
    discipline: SessionDiscipline,
) -> None:
    """Close pending proposals a newly-recorded session has made moot.

    The one entry point for both places an activity session comes into
    existence — the ingest pipeline and the manual-entry service — so the rule
    cannot apply to one and not the other. Plain composition rather than a
    module-level seam: `app.services.proposals` imports nothing that imports
    those callers, so there is no cycle to break and nothing a hook would buy.

    A recording of a discipline nothing is ever planned in (`other`) resolves
    nothing, which is the same answer WP-6's matcher gives it.
    """
    planned = as_planned_discipline(discipline)
    if planned is None:
        return
    await ProposalService.from_session(session).resolve_by_reality(
        actor=actor, date=local_date, discipline=planned
    )


async def run_proposal_expiry() -> None:
    """The scheduled sweep. Never raises — a failed run must not kill the job."""
    try:
        async with session_scope() as session:
            lapsed = await ProposalService.from_session(session).expire(
                actor=Actor.system()
            )
        logger.info("proposal_expiry_ran", lapsed=len(lapsed))
    except Exception:  # noqa: BLE001 — a scheduler job that raises stops running
        logger.exception("proposal_expiry_failed")


def register_proposal_expiry_job(scheduler: BaseScheduler) -> None:
    """Register the proposal expiry sweep on the application scheduler.

    Registered by the work package that needs it, like the inbox, missed and
    prompt sweeps, rather than in `app.core.scheduler`, which owns no jobs of
    its own. ``coalesce`` and ``max_instances=1`` because the sweep is
    idempotent and two of them over one backlog would race for nothing.
    """
    interval = get_settings().proposals.expiry_interval_seconds
    scheduler.add_job(
        run_proposal_expiry,
        "interval",
        seconds=interval,
        id=EXPIRY_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("proposal_expiry_registered", seconds=interval)

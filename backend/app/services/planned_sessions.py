"""Use-cases for planned sessions, and the freeze rule they exist to enforce.

Build-plan invariant 4, in one place:

* **Creating** a session writes intent version 1 and **pins**, for every
  anchor the prescription refers to, whichever version of that anchor is in
  force right now (`app.domain.anchors.anchor_as_of`, through
  `AnchorService.current`). A prescription that refers to an anchor nobody has
  entered yet is refused — an unresolvable prescription is not a plan.
* **Editing intent before the session has been matched** writes version n+1
  and **re-pins**: the plan calls this "frozen at creation or last
  pre-execution edit", so a session re-planned after a new FTP test uses the
  new FTP.
* **Editing intent after a match exists** writes version n+1 flagged
  ``edited_post_hoc``, **keeps the original pins**, and triggers a rescore.
  The pins are kept because the athlete executed against them; changing them
  would rewrite the prescription the ride was actually judged by. Kept, not
  frozen wholesale: a pin the new version no longer needs is dropped, and an
  anchor the new version introduces is pinned at today's version (D54).
* Editing a session's *date* or *status* is not an intent edit and writes no
  version. Those are facts about the calendar, not about what the session is
  for — which is also why **moving** a session (`move`) touches nothing but
  the date and its audit row.
* **Copying** a session (`copy`) creates a *new* planned session, so the rule
  above applies from the top: the copy gets intent version 1 and pins whatever
  anchors are in force now. It is a session planned today that happens to say
  what another one said, not a second view of the original.

Two things this work package cannot yet do are represented as **explicit
seams**, not as silence: whether a session has been matched (WP-6) and how a
rescore is requested (WP-7). Both are module-level hooks with an MVP default
and a setter, in the same spirit as
`app.persistence.db.set_session_factory` — so the freeze machinery is
complete and tested now, and the later work package supplies one function
instead of rewriting this module.
"""

import datetime as dt
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError, domain_rules
from app.domain.actor import Actor
from app.domain.anchors import AnchorType
from app.domain.criteria import (
    SuccessCriterion,
    criteria_from_json,
    criteria_to_json,
)
from app.domain.criteria import referenced_anchor_types as criteria_anchors
from app.domain.prediction import (
    PinnedAnchor,
    PredictedLoad,
    predict_endurance_load,
)
from app.domain.purpose import Purpose
from app.domain.purpose import discipline_of as purpose_discipline
from app.domain.resolution import ResolvedStep, resolve_steps
from app.domain.sessions import SessionIntent, SessionStatus, check_prescription
from app.domain.strength import StrengthWorkout
from app.domain.versioning import FIRST_VERSION, next_version
from app.domain.workout import (
    WorkoutBody,
    workout_body_from_json,
    workout_body_to_json,
)
from app.domain.workout import referenced_anchor_types as body_anchors
from app.persistence.audit import AuditRepository
from app.persistence.db import commit
from app.persistence.planned_sessions import (
    PlannedSessionIntentRow,
    PlannedSessionRepository,
    PlannedSessionRow,
)
from app.services.anchors import AnchorService, parse_pins, resolve_pins
from app.services.templates import purpose_templates
from app.services.workouts import WorkoutService

#: `entity_type` written on this use-case's audit rows.
ENTITY_TYPE = "planned_session"

#: Intent fields an edit may touch. Changing any of them writes a new intent
#: version; changing anything else does not.
INTENT_FIELDS = (
    "purpose",
    "intent_text",
    "coach_notes",
    "success_criteria",
    "workout_id",
    "structure",
)

#: Session fields an edit may touch without versioning anything.
SESSION_FIELDS = ("date", "status")

#: Everything `update` accepts.
UPDATABLE_FIELDS = (*INTENT_FIELDS, *SESSION_FIELDS)

#: Fields an explicit ``null`` may not clear. `intent_text` and `coach_notes`
#: are nullable by design and clearing them is an edit; these three name
#: something a planned session cannot be without, and a null would reach the
#: template lookup or a NOT NULL column as a 500 rather than a 422.
UNCLEARABLE_FIELDS = ("purpose", "date", "status")

#: `recompute_reason` written on an ordinary pre-execution edit.
REASON_EDITED = "intent edited"
#: `recompute_reason` written on an edit made after a match exists.
REASON_EDITED_POST_HOC = "intent edited after the session was matched"


# --- seams for later work packages --------------------------------------------

#: Signature of the "has this planned session been matched?" probe.
type MatchProbe = Callable[[AsyncSession, uuid.UUID], Awaitable[bool]]

#: Signature of the "this session's intent changed, rescore it" trigger.
type RescoreTrigger = Callable[[AsyncSession, uuid.UUID, int], Awaitable[None]]


async def _no_matches_yet(
    _session: AsyncSession, _planned_session_id: uuid.UUID
) -> bool:
    """MVP default: nothing is matched, because matching does not exist yet.

    **WP-6 replaces this** with a query against the match link table, via
    :func:`set_match_probe`. Until it does, every edit is a pre-execution edit
    — which is true, since no activity can be linked to a session yet.
    """
    return False


async def _no_scores_yet(
    _session: AsyncSession, _planned_session_id: uuid.UUID, _version: int
) -> None:
    """MVP default: there is nothing to rescore, because scoring does not exist.

    **WP-7 replaces this** with the rescore it needs, via
    :func:`set_rescore_trigger`. The call site is already correct: it fires
    exactly when a post-hoc intent edit lands, inside the same transaction.
    """


@dataclass(frozen=True, slots=True)
class SessionResolution:
    """One session's prescription said in numbers, against its own pins.

    Everything here is derived on read from the intent version in force. It
    is a separate value rather than fields on the row because it costs a
    query the write paths have no use for, and because it is *not* stored:
    invariant 4 says the pins freeze, so resolving against them is always
    correct and never needs invalidating.

    Args:
        anchors: The anchor versions this session pinned, by type.
        steps: Every flattened step with its targets resolved. Empty for a
            strength prescription, which has no anchor percentages.
        predicted_load: What the prescription is expected to cost, or ``None``
            when it cannot honestly be predicted.
    """

    anchors: Mapping[AnchorType, PinnedAnchor]
    steps: tuple[ResolvedStep, ...]
    predicted_load: PredictedLoad | None


_match_probe: MatchProbe = _no_matches_yet
_rescore_trigger: RescoreTrigger = _no_scores_yet


def set_match_probe(probe: MatchProbe | None) -> None:
    """Install the "has this session been matched?" probe; ``None`` restores the default."""
    global _match_probe  # noqa: PLW0603
    _match_probe = probe or _no_matches_yet


def set_rescore_trigger(trigger: RescoreTrigger | None) -> None:
    """Install the rescore trigger; ``None`` restores the default."""
    global _rescore_trigger  # noqa: PLW0603
    _rescore_trigger = trigger or _no_scores_yet


class PlannedSessionService:
    """Use-cases for planned sessions. Raises AppError subclasses."""

    def __init__(
        self,
        session: AsyncSession,
        repository: PlannedSessionRepository,
        audit: AuditRepository,
        anchors: AnchorService,
        workouts: WorkoutService,
    ) -> None:
        self._session = session
        self._repository = repository
        self._audit = audit
        self._anchors = anchors
        self._workouts = workouts

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(
            session,
            PlannedSessionRepository(session),
            AuditRepository(session),
            AnchorService.from_session(session),
            WorkoutService.from_session(session),
        )

    # --- reads ---------------------------------------------------------------

    async def list(
        self,
        *,
        start: dt.date | None = None,
        end: dt.date | None = None,
        status: SessionStatus | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[PlannedSessionRow], int]:
        """Return a page of planned sessions, oldest first, plus the total."""
        return await self._repository.list(
            start=start, end=end, status=status, offset=offset, limit=limit
        )

    async def get(self, planned_session_id: uuid.UUID) -> PlannedSessionRow:
        """Return one planned session with its intent chain.

        Raises:
            NotFoundError: When no session has that id.
        """
        row = await self._repository.get(planned_session_id)
        if row is None:
            raise NotFoundError(f"Planned session {planned_session_id} not found")
        return row

    async def intents(
        self, planned_session_id: uuid.UUID
    ) -> Sequence[PlannedSessionIntentRow]:
        """Return every intent version of one session, oldest first."""
        return (await self.get(planned_session_id)).intents

    async def intent(
        self, planned_session_id: uuid.UUID, version: int
    ) -> PlannedSessionIntentRow:
        """Return one intent version of one session.

        Raises:
            NotFoundError: When the session or the version does not exist.
        """
        await self.get(planned_session_id)
        row = await self._repository.intent(planned_session_id, version)
        if row is None:
            raise NotFoundError(
                f"Planned session {planned_session_id} has no intent version {version}"
            )
        return row

    async def resolutions(
        self, rows: Sequence[PlannedSessionRow]
    ) -> dict[uuid.UUID, SessionResolution]:
        """Resolve each session's prescription against the versions it pinned.

        Batched: every pin on the screen is loaded in one query, so a page of
        fifty sessions costs the same round-trips as one. Everything returned
        is derived on read — the resolved watts and the predicted load are
        pure functions of the frozen intent and its pins, so there is no
        column and nothing to invalidate.

        A pin the anchor table cannot answer is dropped rather than raising:
        the targets that needed it then report themselves unresolved, which is
        the honest answer on a read path.
        """
        pins = {
            row.id: parse_pins(row.current_intent.pinned_anchor_versions)
            for row in rows
        }
        versions = await self._anchors.by_ids(
            version_id
            for session_pins in pins.values()
            for version_id in session_pins.values()
        )
        resolved: dict[uuid.UUID, SessionResolution] = {}
        for row in rows:
            anchors = resolve_pins(pins[row.id], versions)
            body = _body_of(row.current_intent)
            resolved[row.id] = SessionResolution(
                anchors=anchors,
                steps=resolve_steps(body, anchors),
                predicted_load=(
                    None
                    if isinstance(body, StrengthWorkout)
                    else predict_endurance_load(body, anchors)
                ),
            )
        return resolved

    def default_criteria(self, purpose: Purpose) -> Sequence[dict[str, Any]]:
        """Return the success criteria a session of ``purpose`` starts with.

        Annotated `Sequence`, not `list`: `list` inside this class body
        resolves to the `list` *method* above, and the annotation would be a
        subscript of a coroutine (caught by pyrefly).
        """
        return criteria_to_json(purpose_templates()[purpose].default_criteria)

    # --- writes --------------------------------------------------------------

    async def create(
        self,
        *,
        actor: Actor,
        date: dt.date,
        purpose: Purpose,
        workout_id: uuid.UUID | None = None,
        structure: Mapping[str, Any] | None = None,
        intent_text: str | None = None,
        coach_notes: str | None = None,
        success_criteria: Sequence[Mapping[str, Any]] | None = None,
    ) -> PlannedSessionRow:
        """Plan a session, freezing its prescription.

        The prescription comes from the library (``workout_id``) or inline
        (``structure``) — exactly one. Success criteria default to the
        purpose's template when omitted.

        Raises:
            ValidationError: When neither or both prescription sources are
                given, when the prescription is illegal, or when it refers to
                an anchor with no version in force.
            NotFoundError: When ``workout_id`` names no library workout.
        """
        body, resolved_workout_id = await self._resolve_body(workout_id, structure)
        criteria = await self._resolve_criteria(purpose, success_criteria)
        # Shape first, pins second: resolving anchors is a round-trip per
        # anchor, and its error ("no FTP is in force") would otherwise mask
        # the more fundamental one ("that purpose is for the other
        # discipline").
        with domain_rules():
            check_prescription(purpose, body, criteria)
        pins = await self._pin_anchors(_anchor_sources(body, criteria))
        intent = self._build_intent(
            purpose=purpose,
            body=body,
            criteria=criteria,
            pins=pins,
            intent_text=intent_text,
            coach_notes=coach_notes,
        )

        row = await self._repository.add(
            PlannedSessionRow(
                date=date,
                discipline=intent.discipline,
                status=SessionStatus.PLANNED,
            )
        )
        await self._repository.append_intent(
            _intent_row(
                planned_session_id=row.id,
                version=FIRST_VERSION,
                intent=intent,
                workout_id=resolved_workout_id,
                edited_post_hoc=False,
                recompute_reason=None,
            )
        )
        await self._audit.record(
            actor=actor,
            action="planned_session.created",
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            payload=_payload(row, intent, version=FIRST_VERSION),
        )
        await commit(self._session)
        await self._session.refresh(row)
        return row

    async def update(
        self,
        planned_session_id: uuid.UUID,
        updates: Mapping[str, Any],
        *,
        actor: Actor,
    ) -> PlannedSessionRow:
        """Update a planned session, versioning its intent if intent changed.

        See the module docstring for the three cases. ``updates`` holds only
        the fields the caller supplied — and an edit that supplies nothing is
        refused rather than answered with an audit row saying nothing changed.

        A patch that moves the session **and** edits its intent leaves two
        audit rows, because those are two facts: the calendar move is not
        implied by the revision, and the revision is not implied by the move.

        Raises:
            NotFoundError: When the session (or a referenced workout) does not
                exist.
            ValidationError: When a field is unknown, empty, cleared or
                illegal.
        """
        unknown = set(updates) - set(UPDATABLE_FIELDS)
        if unknown:
            raise ValidationError(
                f"Unknown planned-session fields: {', '.join(sorted(unknown))}"
            )
        for name in UNCLEARABLE_FIELDS:
            if name in updates and updates[name] is None:
                raise ValidationError(f"{name} cannot be cleared")
        if "workout_id" in updates and "structure" in updates:
            raise ValidationError(
                "Give either workout_id or structure, not both: a session's "
                "prescription has one source."
            )

        row = await self.get(planned_session_id)
        # Checked after the lookup: patching a session that does not exist
        # should say so, not complain about the body.
        if not updates:
            raise ValidationError("Supply at least one field to update")
        touches_intent = bool(set(updates) & set(INTENT_FIELDS))
        touches_session = bool(set(updates) & set(SESSION_FIELDS))

        if touches_intent:
            await self._revise_intent(row, updates, actor=actor)

        if "date" in updates:
            row.date = updates["date"]
        if "status" in updates:
            row.status = updates["status"]
        row.discipline = purpose_discipline(row.current_intent.purpose)
        row = await self._repository.add(row)

        if touches_session:
            await self._audit.record(
                actor=actor,
                action="planned_session.updated",
                entity_type=ENTITY_TYPE,
                entity_id=row.id,
                payload={
                    "changed": sorted(set(updates) & set(SESSION_FIELDS)),
                    "date": row.date.isoformat(),
                    "status": row.status.value,
                    "intent_version": row.current_intent.version,
                },
            )
        await commit(self._session)
        await self._session.refresh(row)
        return row

    async def move(
        self, planned_session_id: uuid.UUID, *, date: dt.date, actor: Actor
    ) -> PlannedSessionRow:
        """Move a planned session to another date.

        The calendar's drag-and-drop, and nothing more: the prescription, the
        intent chain and the pins are untouched, because *when* a session is
        planned for is not part of what it is for. A move to the day the
        session already sits on is accepted and audited — dragging a card back
        where it came from is a legitimate thing to do, and refusing it would
        make the client track a fact the server already knows.

        Raises:
            NotFoundError: When no session has that id.
        """
        row = await self.get(planned_session_id)
        moved_from = row.date
        row.date = date
        row = await self._repository.add(row)
        await self._audit.record(
            actor=actor,
            action="planned_session.moved",
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            payload={
                "from": moved_from.isoformat(),
                "to": row.date.isoformat(),
                "intent_version": row.current_intent.version,
            },
        )
        await commit(self._session)
        await self._session.refresh(row)
        return row

    async def copy(
        self, planned_session_id: uuid.UUID, *, date: dt.date, actor: Actor
    ) -> PlannedSessionRow:
        """Duplicate a planned session onto another date.

        The copy is a new session planned *now*: status `planned`, intent
        version 1, its own version chain, and its anchors pinned at the
        versions in force today rather than inherited from the original
        (invariant 4 — a prescription freezes when it is planned, and this
        prescription is being planned now). Repeating last week's ride after
        an FTP test therefore prescribes against the new FTP, which is what
        "repeat this" means.

        What *is* inherited is everything the athlete wrote: the purpose, the
        frozen structure (the snapshot, not a fresh read of the library
        workout, so a library edit since cannot change what is being
        repeated), the intent text, the coach notes, the success criteria as
        they stand — edited or not — and the provenance link to the library
        workout.

        Raises:
            NotFoundError: When no session has that id.
            ValidationError: When the copy cannot be pinned, i.e. an anchor
                the prescription refers to has no version in force. Anchors
                are append-only, so this means the original was planned
                against an anchor type nobody has entered since.
        """
        source = await self.get(planned_session_id)
        current = source.current_intent
        with domain_rules():
            body = _body_of(current)
            criteria = criteria_from_json(current.success_criteria)
        pins = await self._pin_anchors(_anchor_sources(body, criteria))
        intent = self._build_intent(
            purpose=current.purpose,
            body=body,
            criteria=criteria,
            pins=pins,
            intent_text=current.intent_text,
            coach_notes=current.coach_notes,
        )

        row = await self._repository.add(
            PlannedSessionRow(
                date=date,
                discipline=intent.discipline,
                status=SessionStatus.PLANNED,
            )
        )
        await self._repository.append_intent(
            _intent_row(
                planned_session_id=row.id,
                version=FIRST_VERSION,
                intent=intent,
                workout_id=current.workout_id,
                edited_post_hoc=False,
                recompute_reason=None,
            )
        )
        # Where the copy came from is recorded on the audit row rather than on
        # the session: a copy is an independent plan entry from the moment it
        # exists, and a column pointing at the original would invite readers
        # to treat it as one artefact in two places.
        await self._audit.record(
            actor=actor,
            action="planned_session.copied",
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            payload=_payload(row, intent, version=FIRST_VERSION)
            | {"copied_from": str(source.id)},
        )
        await commit(self._session)
        await self._session.refresh(row)
        return row

    async def delete(self, planned_session_id: uuid.UUID, *, actor: Actor) -> None:
        """Remove a planned session and its whole intent chain.

        Raises:
            NotFoundError: When no session has that id.
        """
        row = await self.get(planned_session_id)
        payload = {
            "date": row.date.isoformat(),
            "discipline": row.discipline.value,
            "status": row.status.value,
            "intent_versions": len(row.intents),
        }
        await self._repository.delete(row)
        await self._audit.record(
            actor=actor,
            action="planned_session.deleted",
            entity_type=ENTITY_TYPE,
            entity_id=planned_session_id,
            payload=payload,
        )
        await commit(self._session)

    # --- the freeze rule -----------------------------------------------------

    async def _revise_intent(
        self, row: PlannedSessionRow, updates: Mapping[str, Any], *, actor: Actor
    ) -> PlannedSessionIntentRow:
        """Append the next intent version, applying the freeze rule."""
        current = row.current_intent
        post_hoc = await _match_probe(self._session, row.id)

        purpose = updates.get("purpose", current.purpose)
        if "workout_id" in updates or "structure" in updates:
            body, workout_id = await self._resolve_body(
                updates.get("workout_id"), updates.get("structure")
            )
        else:
            with domain_rules():
                body = _body_of(current)
            workout_id = current.workout_id

        if "success_criteria" in updates:
            criteria = await self._resolve_criteria(
                purpose, updates["success_criteria"]
            )
        elif purpose != current.purpose:
            # The purpose is what the criteria were derived from, so changing
            # it without supplying criteria re-derives them from the new
            # template rather than leaving the old purpose's rules behind.
            criteria = await self._resolve_criteria(purpose, None)
        else:
            with domain_rules():
                criteria = criteria_from_json(current.success_criteria)

        with domain_rules():
            check_prescription(purpose, body, criteria)
        sources = _anchor_sources(body, criteria)
        required = set(sources)
        if post_hoc:
            # Keep the pins the athlete executed against — but only those the
            # new version still refers to. A pin for an anchor the edit
            # removed is dropped (the intent rejects pins nothing needs, and
            # keeping one would claim a resolution this prescription has no
            # use for), and an anchor the edit *introduced* is pinned at
            # today's version, because there is no older answer to keep: it
            # was never part of what the athlete executed against (D54).
            pins = {
                anchor: version
                for anchor, version in parse_pins(
                    current.pinned_anchor_versions
                ).items()
                if anchor in required
            }
            missing = required - set(pins)
            pins |= await self._pin_anchors(
                {anchor: sources[anchor] for anchor in missing}
            )
        else:
            pins = await self._pin_anchors(sources)

        intent = self._build_intent(
            purpose=purpose,
            body=body,
            criteria=criteria,
            pins=pins,
            intent_text=updates.get("intent_text", current.intent_text),
            coach_notes=updates.get("coach_notes", current.coach_notes),
        )

        # pyrefly: ignore[bad-specialization]
        # See `PlannedSessionRow.current_intent` for why the protocol match is
        # suppressed: SQLAlchemy's `Mapped[X]` descriptors, not a real mismatch.
        version = next_version(row.intents)
        appended = await self._repository.append_intent(
            _intent_row(
                planned_session_id=row.id,
                version=version,
                intent=intent,
                workout_id=workout_id,
                edited_post_hoc=post_hoc,
                recompute_reason=(
                    REASON_EDITED_POST_HOC if post_hoc else REASON_EDITED
                ),
            )
        )
        # Close the old link *after* the new row exists, so a reader walking
        # the chain never sees a `superseded_by` pointing at nothing.
        current.superseded_by = appended.id
        row.intents.append(appended)

        await self._audit.record(
            actor=actor,
            action="planned_session.intent_revised",
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            payload={
                "from_version": current.version,
                "to_version": version,
                "edited_post_hoc": post_hoc,
                "recompute_reason": appended.recompute_reason,
                "changed": sorted(set(updates) & set(INTENT_FIELDS)),
                "pinned_anchor_versions": appended.pinned_anchor_versions,
            },
        )
        if post_hoc:
            await _rescore_trigger(self._session, row.id, version)
        return appended

    # --- helpers -------------------------------------------------------------

    def _build_intent(
        self,
        *,
        purpose: Purpose,
        body: WorkoutBody,
        criteria: Sequence[Any],
        pins: Mapping[AnchorType, uuid.UUID],
        intent_text: str | None,
        coach_notes: str | None,
    ) -> SessionIntent:
        """Assemble and validate one intent version."""
        with domain_rules():
            return SessionIntent(
                purpose=purpose,
                body=body,
                success_criteria=tuple(criteria),
                pinned_anchor_versions=dict(pins),
                intent_text=intent_text,
                coach_notes=coach_notes,
            )

    async def _resolve_body(
        self, workout_id: uuid.UUID | None, structure: Mapping[str, Any] | None
    ) -> tuple[WorkoutBody, uuid.UUID | None]:
        """Resolve the prescription from the library or from an inline document.

        Raises:
            ValidationError: When neither or both are given, or the inline
                document is illegal.
            NotFoundError: When ``workout_id`` names no library workout.
        """
        if (workout_id is None) == (structure is None):
            raise ValidationError(
                "A planned session needs exactly one of workout_id or structure"
            )
        if workout_id is not None:
            row = await self._workouts.get(workout_id)
            with domain_rules():
                return _parse_stored(row.structure), row.id
        return await self._workouts.parse_structure(structure or {}), None

    async def _resolve_criteria(
        self, purpose: Purpose, supplied: Sequence[Mapping[str, Any]] | None
    ) -> Sequence[Any]:
        """Return the criteria to store: the caller's, or the template's.

        Raises:
            ValidationError: When a supplied criterion is illegal.
        """
        if supplied is None:
            return purpose_templates()[purpose].default_criteria
        with domain_rules():
            return criteria_from_json(list(supplied))

    async def _pin_anchors(
        self, sources: Mapping[AnchorType, frozenset[str]]
    ) -> dict[AnchorType, uuid.UUID]:
        """Pin the version of each anchor that is in force right now.

        Args:
            sources: The anchors to pin, each mapped to which halves of the
                prescription refer to it (:func:`_anchor_sources`). Carried
                only so the refusal below can name the half the client would
                have to change.

        Raises:
            ValidationError: When an anchor the prescription needs has no
                version in force. A 422 rather than the 404 the anchor service
                raises: the fault is in the prescription being planned, not in
                a missing resource the client asked for.
        """
        pins: dict[AnchorType, uuid.UUID] = {}
        for anchor_type in sorted(sources, key=lambda anchor: anchor.value):
            try:
                pins[anchor_type] = (await self._anchors.current(anchor_type)).id
            except NotFoundError as exc:
                raise ValidationError(
                    _missing_anchor_message(anchor_type, sources[anchor_type])
                ) from exc
        return pins


#: What refers to an anchor, as :func:`_anchor_sources` labels it.
FROM_TARGETS = "targets"
FROM_CRITERIA = "criteria"


def _anchor_sources(
    body: WorkoutBody, criteria: Sequence[SuccessCriterion]
) -> dict[AnchorType, frozenset[str]]:
    """Return every anchor this prescription must pin, and what refers to it.

    The set is `app.domain.sessions.required_anchor_types`; what is added is
    *which* half asked for each anchor. Both halves are part of the frozen
    prescription, but they are edited in different places and by different
    people — the targets are what the planner wrote, the criteria usually come
    from the purpose template — so a refusal that cannot tell them apart sends
    the client to fix the wrong one.
    """
    from_body = body_anchors(body)
    from_criteria = criteria_anchors(criteria)
    return {
        anchor: frozenset(
            label
            for label, group in (
                (FROM_TARGETS, from_body),
                (FROM_CRITERIA, from_criteria),
            )
            if anchor in group
        )
        for anchor in from_body | from_criteria
    }


def _missing_anchor_message(anchor: AnchorType, sources: frozenset[str]) -> str:
    """Explain which half of the prescription needs an anchor nobody entered.

    D49 gave this refusal one wording, and it misleads whenever the anchor is
    required by the template's criteria alone: an athlete who prescribed
    nothing but absolute watts was told "this prescription is expressed as a
    percentage of ftp", and the remedy it offered — use absolute targets — was
    the thing they had already done.
    """
    name = anchor.value
    if sources == frozenset({FROM_TARGETS}):
        subject = f"This prescription is expressed as a percentage of {name}"
        remedy = "prescribe absolute targets"
    elif sources == frozenset({FROM_CRITERIA}):
        subject = (
            "The success criteria (from the purpose template, editable) "
            f"reference {name}"
        )
        remedy = "edit the criteria"
    else:
        subject = (
            "This prescription's targets and its success criteria (from the "
            f"purpose template, editable) both reference {name}"
        )
        remedy = "prescribe absolute targets and edit the criteria"
    return (
        f"{subject}, but no {name} anchor is in force. Append one before "
        f"planning the session, or {remedy}."
    )


def _intent_row(
    *,
    planned_session_id: uuid.UUID,
    version: int,
    intent: SessionIntent,
    workout_id: uuid.UUID | None,
    edited_post_hoc: bool,
    recompute_reason: str | None,
) -> PlannedSessionIntentRow:
    """Build the row for one intent version."""
    return PlannedSessionIntentRow(
        planned_session_id=planned_session_id,
        version=version,
        as_of=dt.datetime.now(dt.UTC),
        recompute_reason=recompute_reason,
        edited_post_hoc=edited_post_hoc,
        purpose=intent.purpose,
        intent_text=intent.intent_text,
        coach_notes=intent.coach_notes,
        success_criteria=criteria_to_json(intent.success_criteria),
        pinned_anchor_versions={
            anchor.value: str(version_id)
            for anchor, version_id in intent.pinned_anchor_versions.items()
        },
        workout_id=workout_id,
        structure=workout_body_to_json(intent.body),
    )


def _body_of(intent: PlannedSessionIntentRow) -> WorkoutBody:
    """Parse a stored intent's frozen prescription back into the domain."""
    return _parse_stored(intent.structure)


def _parse_stored(structure: Mapping[str, Any]) -> WorkoutBody:
    """Parse a stored structure document, raising the domain's own error."""
    return workout_body_from_json(structure)


def _payload(
    row: PlannedSessionRow, intent: SessionIntent, *, version: int
) -> dict[str, Any]:
    """The planned session, as JSON, for the audit trail."""
    return {
        "date": row.date.isoformat(),
        "discipline": row.discipline.value,
        "status": row.status.value,
        "purpose": intent.purpose.value,
        "intent_version": version,
        "criteria": [
            criterion["kind"] for criterion in criteria_to_json(intent.success_criteria)
        ],
        "pinned_anchor_versions": {
            anchor.value: str(version_id)
            for anchor, version_id in intent.pinned_anchor_versions.items()
        },
    }

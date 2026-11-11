"""The outbox use-case: apply the phone's mutations once, in order.

The phone writes every gym action to its own outbox first and uploads it in
batches (`frontend/lib/offline/`). This service takes one batch and

* **skips what it has seen.** Every mutation carries a phone-minted id; an id
  already in `sync_mutations` answers `already_applied` and writes nothing —
  no row, no audit entry, no recompute. That is what makes a retry after a
  dropped response safe.
* **merges what it has not,** in the order sent, under the conflict rule in
  `app.domain.sync` (latest client edit time per field, receive order as the
  tie-breaker, deletes as tombstones), then projects the merged state onto
  the ordinary rows — `sessions`, `logged_sets`, `exercise_notes` — so every
  reader sees a synced set as it sees a typed-in one. Sets are built by
  `SessionService.build_logged_set` and checked by `check_logged_set`, the same
  code `create_manual` uses, so a set cannot be valid by one door and refused
  by the other.
* **is all or nothing.** The first mutation the server cannot apply — a set
  for a session never started, an unknown exercise, a set with no reps — rolls
  the whole batch back and is named in a 422 (`MutationRejectedError`). The
  phone sets that one item aside in "Couldn't upload" and resends the rest.
  Displaced: per-item partial success, which would need a savepoint per item
  and leave the phone reconciling a half-applied batch.
* **commits once,** then refreshes every finished session the batch changed
  (`SessionService.refresh_derived`): a new metric version, and on Finish the
  matching and proposal resolution a typed-in session gets. `create_manual`
  was not reused whole because it commits per session and has no idempotency.
"""

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import athlete_timezone
from app.core.exceptions import (
    AppError,
    ConflictError,
    NotFoundError,
    ValidationError,
    domain_rules,
)
from app.domain.activity import (
    ClassificationSource,
    EffortSimple,
    HoldQuality,
    LoggedSetKind,
    PainBand,
    PainKind,
    RecordingKind,
    SessionDiscipline,
    SessionLifecycle,
    SetConfidence,
    SetSide,
    SetSource,
    parse_timezone,
    session_date,
)
from app.domain.actor import Actor, ActorKind
from app.domain.cards import CardPriority
from app.domain.safety import RedFlagSymptom
from app.domain.swaps import SWAP_REST_OF_BLOCK, SwapScope, rest_of_block_card
from app.domain.sync import (
    EXISTS,
    MAX_BATCH_MUTATIONS,
    MAX_SCHEMA_VERSION,
    MutationKind,
    MutationStatus,
    Stamp,
    decode_clocks,
    encode_clocks,
    is_alive,
    merge,
    schema_version_supported,
    stamp_for,
)
from app.persistence.activity import SessionRepository, SessionRow
from app.persistence.audit import AuditRepository
from app.persistence.db import commit, refresh
from app.persistence.exercises import MAX_NOTE_LENGTH, ExerciseNoteRepository
from app.persistence.sync import SyncEntityRow, SyncRepository
from app.services.activity import (
    EARLIEST_SESSION,
    LATEST_SESSION,
    MAX_MANUAL_DURATION_S,
    LoggedSetInput,
    SessionService,
    check_logged_set,
    check_session_context,
)
from app.services.cards import CardService
from app.services.exercise_preferences import ExercisePreferencesService
from app.services.exercises import ExerciseService
from app.services.guardrails import check_write_cap
from app.services.safety import SafetyService

#: The `reason` on a metric version a sync recomputed.
SYNC_REASON = "synced from the phone"

#: Entity names in `sync_entities`.
SESSION, SET, NOTE = "session", "set", "note"

#: `entity_type` on the audit row of each mutation kind.
AUDIT_ENTITY = {
    MutationKind.SESSION_START: "session",
    MutationKind.SESSION_FINISH: "session",
    MutationKind.SESSION_RATING: "session",
    MutationKind.DONT_LEARN: "session",
    MutationKind.SET_UPSERT: "logged_set",
    MutationKind.SET_DELETE: "logged_set",
    MutationKind.NOTE_UPSERT: "exercise_note",
    MutationKind.RED_FLAG: "session",
    MutationKind.SWAP: "exercise_preference",
}

#: LoggedSetRow columns a synced edit may change on an existing row. Not
#: `source`: who logged a set is fixed when it is created, so a coach
#: correcting a phone set does not turn it into the coach's.
_SET_COLUMNS = (
    "exercise_id",
    "exercise_name",
    "set_index",
    "set_kind",
    "side",
    "planned_line_ref",
    "replaced_exercise_id",
    "confidence",
    "reps",
    "duration_s",
    "per_side",
    "load_kg",
    "rir",
    "rir_capped",
    "rpe",
    "hold_quality",
    "effort_simple",
    "rest_s",
    "completed_at",
    "utc_offset_min",
    "pain_kind",
    "pain_band",
    "notes",
)


class SchemaVersionUnsupportedError(ConflictError):
    """The batch is a shape this server does not understand.

    A 409 with a stable ``code`` because the phone has to *act* on it — keep
    the queue and say "Update arc to upload" — rather than retry the same
    batch forever (`app.api.routes.sync`).
    """

    code = "schema_version_unsupported"

    def __init__(self, version: int) -> None:
        super().__init__(
            f"schema_version {version} is not one this server understands "
            f"(it writes {MAX_SCHEMA_VERSION}); update arc to upload"
        )

    def body(self) -> dict[str, Any]:
        """``detail``, ``code`` and the version the server does write."""
        return super().body() | {
            "code": self.code,
            "server_schema_version": MAX_SCHEMA_VERSION,
        }


class MutationRejectedError(ValidationError):
    """One mutation cannot be applied; the batch was rolled back.

    Names the mutation so the phone sets exactly that item aside.
    """

    code = "mutation_rejected"

    def __init__(self, mutation_id: uuid.UUID, detail: str) -> None:
        self.mutation_id = mutation_id
        super().__init__(detail)

    def body(self) -> dict[str, Any]:
        """``detail``, ``code`` and the rejected ``mutation_id``."""
        return super().body() | {
            "code": self.code,
            "mutation_id": str(self.mutation_id),
        }


@dataclass(frozen=True, slots=True)
class MutationInput:
    """One outbox item, as the adapter hands it over.

    Args:
        mutation_id: The phone's id for the item; a replay carries the same.
        kind: What it does.
        client_edit_time: When the edit was made on the device, aware.
        session_client_id: The session's phone id; every kind but
            ``note_upsert`` names one.
        set_client_id: The set's phone id, for the two set kinds.
        exercise_id: The exercise a ``note_upsert`` writes the note of.
        fields: The kind's own values, JSON-plain (enums by value, instants as
            ISO text): ``start_time``/``timezone``, ``end_time``,
            ``set`` (a partial set), ``session_rating``,
            ``dont_learn``/``reason``, ``text``.
    """

    mutation_id: uuid.UUID
    kind: MutationKind
    client_edit_time: dt.datetime
    session_client_id: uuid.UUID | None = None
    set_client_id: uuid.UUID | None = None
    exercise_id: str | None = None
    fields: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MutationResult:
    """What happened to one mutation."""

    mutation_id: uuid.UUID
    status: MutationStatus


@dataclass(slots=True)
class _Touched:
    """A session this batch changed, and whether it changed its finish."""

    row: SessionRow
    finished: bool = False


class SyncService:
    """Applies outbox batches. Raises AppError subclasses."""

    def __init__(
        self,
        session: AsyncSession,
        repository: SyncRepository,
        sessions: SessionRepository,
        audit: AuditRepository,
        session_service: SessionService,
        exercises: ExerciseService,
        notes: ExerciseNoteRepository,
        safety: SafetyService,
        preferences: ExercisePreferencesService,
        cards: CardService,
    ) -> None:
        self._safety = safety
        self._preferences = preferences
        self._cards = cards
        self._session = session
        self._repository = repository
        self._sessions = sessions
        self._audit = audit
        self._session_service = session_service
        self._exercises = exercises
        self._notes = notes

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(
            session,
            SyncRepository(session),
            SessionRepository(session),
            AuditRepository(session),
            SessionService.from_session(session),
            ExerciseService.from_session(session),
            ExerciseNoteRepository(session),
            SafetyService.from_session(session),
            ExercisePreferencesService.from_session(session),
            CardService.from_session(session),
        )

    async def apply(
        self,
        mutations: Sequence[MutationInput],
        *,
        schema_version: int,
        actor: Actor,
    ) -> list[MutationResult]:
        """Apply one batch in order and say what happened to each mutation.

        Raises:
            SchemaVersionUnsupportedError: When ``schema_version`` is outside
                the versions this server understands; nothing is applied.
            ValidationError: When the batch is longer than
                `MAX_BATCH_MUTATIONS`.
            MutationRejectedError: When one mutation cannot be applied; the
                whole batch is rolled back.
            RateLimitedError: When an agent's trailing-hour cap is spent.
        """
        if not schema_version_supported(schema_version):
            raise SchemaVersionUnsupportedError(schema_version)
        if len(mutations) > MAX_BATCH_MUTATIONS:
            raise ValidationError(
                f"A sync batch carries at most {MAX_BATCH_MUTATIONS} mutations"
            )
        known = await self._repository.known(m.mutation_id for m in mutations)
        if len(known) < len({m.mutation_id for m in mutations}):
            await check_write_cap(self._session, actor)
            # The catalogue seed commits mid-transaction (see
            # `ExerciseService.ensure_seeded`), so it runs before the first
            # write: a lazy seed on the first set's lookup would commit the
            # half-applied batch a later rejection must roll back.
            await self._exercises.ensure_seeded()

        results: list[MutationResult] = []
        touched: dict[uuid.UUID, _Touched] = {}
        for mutation in mutations:
            if mutation.mutation_id in known:
                results.append(
                    MutationResult(mutation.mutation_id, MutationStatus.ALREADY_APPLIED)
                )
                continue
            try:
                await self._apply_one(mutation, actor=actor, touched=touched)
            except (ValidationError, NotFoundError) as error:
                await self._session.rollback()
                raise MutationRejectedError(
                    mutation.mutation_id, error.detail
                ) from error
            known.add(mutation.mutation_id)
            results.append(MutationResult(mutation.mutation_id, MutationStatus.APPLIED))

        if all(r.status is MutationStatus.ALREADY_APPLIED for r in results):
            return results
        await commit(self._session)
        await self._refresh_derived(touched, actor=actor)
        return results

    async def _apply_one(
        self,
        mutation: MutationInput,
        *,
        actor: Actor,
        touched: dict[uuid.UUID, _Touched],
    ) -> None:
        """Receive, merge, project and audit one new mutation."""
        received = await self._repository.receive(
            mutation.mutation_id, mutation.kind, str(actor)
        )
        stamp = stamp_for(
            agent=actor.kind is ActorKind.AGENT,
            client_edit_time=mutation.client_edit_time,
            server_now=dt.datetime.now(dt.UTC),
            received=received,
        )
        payload: dict[str, Any] = {
            "mutation_id": str(mutation.mutation_id),
            "kind": mutation.kind.value,
        }
        entity_id: uuid.UUID | None = None

        if mutation.kind is MutationKind.NOTE_UPSERT:
            changed = await self._note(mutation, stamp=stamp)
            payload["exercise_id"] = mutation.exercise_id
        else:
            row, changed = await self._session_mutation(
                mutation, stamp=stamp, actor=actor
            )
            entity_id = row.id
            if mutation.set_client_id is not None:
                payload["set_client_id"] = str(mutation.set_client_id)
            if changed:
                entry = touched.setdefault(row.id, _Touched(row))
                entry.finished |= "end_time" in changed
        payload["changed"] = sorted(changed)
        await self._audit.record(
            actor=actor,
            action=f"sync.{mutation.kind.value}",
            entity_type=AUDIT_ENTITY[mutation.kind],
            entity_id=entity_id,
            payload=payload,
        )

    # --- sessions -----------------------------------------------------------

    async def _session_mutation(
        self, mutation: MutationInput, *, stamp: Stamp, actor: Actor
    ) -> tuple[SessionRow, tuple[str, ...]]:
        """Apply a session- or set-scoped mutation; return its session."""
        if mutation.session_client_id is None:  # pragma: no cover — schema
            raise ValidationError("this mutation needs a session_client_id")
        key = str(mutation.session_client_id)
        entity = await self._repository.entity(SESSION, key)
        if entity is None:
            if mutation.kind is not MutationKind.SESSION_START:
                raise ValidationError(
                    f"no session with client id {key}: a session begins with "
                    "session_start, and none was uploaded for this one"
                )
            row = await self._new_session(mutation.fields)
            entity = SyncEntityRow(
                entity=SESSION, key=key, session_id=row.id, values={}, clocks={}
            )
        else:
            if entity.session_id is None:  # pragma: no cover — cascade
                raise ValidationError(f"session {key} no longer exists")
            row = await self._session_service.get(entity.session_id)

        if mutation.kind in (MutationKind.SET_UPSERT, MutationKind.SET_DELETE):
            changed = await self._set(mutation, row, stamp=stamp, actor=actor)
            return row, changed

        if mutation.kind is MutationKind.RED_FLAG:
            # Not a session field: the flag is the athlete's, so there is
            # nothing to merge — each red flag is applied once, as received.
            with domain_rules():
                symptom = RedFlagSymptom(str(mutation.fields.get("symptom")))
            await self._safety.raise_red_flag(symptom, session_id=row.id, actor=actor)
            return row, ("red_flag",)

        if mutation.kind is MutationKind.SWAP:
            # Not a session field either: the pick is the athlete's preference,
            # applied once as received; the sets carry the swap themselves.
            await self._swap(mutation, session_id=row.id, actor=actor)
            return row, ("swap",)

        patch = _session_patch(mutation)
        merged = merge(entity.values, decode_clocks(entity.clocks), patch, stamp)
        _project_session(row, merged.values)
        entity.values = merged.values
        entity.clocks = encode_clocks(merged.clocks)
        await self._repository.save(entity)
        await self._sessions.add(row)
        return row, merged.changed

    async def _swap(
        self, mutation: MutationInput, *, session_id: uuid.UUID, actor: Actor
    ) -> None:
        """Remember the pick; for ``rest_of_block``, queue the pending card."""
        original = mutation.exercise_id or ""
        pick = str(mutation.fields.get("swap_exercise_id") or "")
        with domain_rules():
            scope = SwapScope(mutation.fields.get("scope", SwapScope.SESSION))
        await self._preferences.remember(original, pick, actor=actor)
        if scope is not SwapScope.REST_OF_BLOCK:
            return
        catalogue = await self._exercises.catalogue()
        headline, because = rest_of_block_card(
            catalogue[original].name, catalogue[pick].name
        )
        await self._cards.queue(
            actor=actor,
            kind=SWAP_REST_OF_BLOCK,
            priority=CardPriority.SUNDAY,
            headline=headline,
            because=because,
            payload={
                "session_id": str(session_id),
                "exercise_id": original,
                "swap_exercise_id": pick,
            },
        )

    async def _new_session(self, fields: Mapping[str, Any]) -> SessionRow:
        """The row a first `session_start` creates: active, not yet finished."""
        start = _instant(fields.get("start_time"), "start_time")
        timezone = fields.get("timezone") or athlete_timezone()
        with domain_rules():
            parse_timezone(timezone)
            local_date = session_date(start, timezone)
        return await self._sessions.add(
            SessionRow(
                start_time=start,
                end_time=start,
                timezone=timezone,
                local_date=local_date,
                discipline=SessionDiscipline.STRENGTH,
                classification_source=ClassificationSource.MANUAL,
                recording_kind=RecordingKind.MANUAL,
                lifecycle=SessionLifecycle.ACTIVE,
            )
        )

    # --- sets ---------------------------------------------------------------

    async def _set(
        self, mutation: MutationInput, row: SessionRow, *, stamp: Stamp, actor: Actor
    ) -> tuple[str, ...]:
        """Merge a set upsert or delete and project it onto `logged_sets`."""
        if mutation.set_client_id is None:  # pragma: no cover — schema
            raise ValidationError("a set mutation needs a set_client_id")
        key = str(mutation.set_client_id)
        session_key = str(mutation.session_client_id)
        entity = await self._repository.entity(SET, key)
        if entity is None:
            entity = SyncEntityRow(
                entity=SET,
                key=key,
                session_id=row.id,
                values={"session": session_key},
                clocks={},
            )
        elif entity.values.get("session") != session_key:
            raise ValidationError(
                f"set {key} belongs to another session; a set does not move"
            )

        existing = next((s for s in row.logged_sets if str(s.client_id) == key), None)
        pain_before = None if existing is None else existing.pain_kind
        if mutation.kind is MutationKind.SET_DELETE:
            patch: dict[str, Any] = {EXISTS: False}
        else:
            patch = {EXISTS: True, **dict(mutation.fields.get("set") or {})}
            if "set_index" not in patch and "set_index" not in entity.values:
                patch["set_index"] = len(row.logged_sets)
        merged = merge(entity.values, decode_clocks(entity.clocks), patch, stamp)

        if is_alive(merged.values):
            entry = _set_input(merged.values, client_id=mutation.set_client_id)
            index = int(merged.values["set_index"])
            check_logged_set(entry, index + 1)
            built = await self._session_service.build_logged_set(
                entry, index, actor=actor
            )
            if existing is None:
                row.logged_sets.append(built)
            else:
                for column in _SET_COLUMNS:
                    setattr(existing, column, getattr(built, column))
        elif existing is not None:
            row.logged_sets.remove(existing)

        alive = is_alive(merged.values)
        values = merged.values
        await self._safety.pain_changed(
            set_client_id=mutation.set_client_id,
            session_id=row.id,
            before=pain_before,
            after=_optional(values, "pain_kind", PainKind) if alive else None,
            exercise_id=values.get("exercise_id"),
            exercise_name=values.get("exercise_name"),
            band=_optional(values, "pain_band", PainBand) if alive else None,
            actor=actor,
        )

        entity.values = merged.values
        entity.clocks = encode_clocks(merged.clocks)
        await self._repository.save(entity)
        await self._sessions.add(row)
        return merged.changed

    # --- notes --------------------------------------------------------------

    async def _note(self, mutation: MutationInput, *, stamp: Stamp) -> tuple[str, ...]:
        """Merge an exercise note; empty text deletes it, as `set_note` does."""
        exercise_id = mutation.exercise_id or ""
        await self._exercises.get(exercise_id)
        text = str(mutation.fields.get("text") or "")
        if len(text) > MAX_NOTE_LENGTH:
            raise ValidationError(
                f"an exercise note is at most {MAX_NOTE_LENGTH} characters"
            )
        entity = await self._repository.entity(NOTE, exercise_id)
        if entity is None:
            entity = SyncEntityRow(entity=NOTE, key=exercise_id, values={}, clocks={})
        merged = merge(
            entity.values, decode_clocks(entity.clocks), {"text": text}, stamp
        )
        if "text" in merged.changed:
            existing = await self._notes.get(exercise_id)
            if text:
                await self._notes.put(exercise_id, text)
            elif existing is not None:
                await self._notes.delete(existing)
        entity.values = merged.values
        entity.clocks = encode_clocks(merged.clocks)
        await self._repository.save(entity)
        return merged.changed

    # --- after the commit ---------------------------------------------------

    async def _refresh_derived(
        self, touched: Mapping[uuid.UUID, _Touched], *, actor: Actor
    ) -> None:
        """Recompute every finished session the batch changed."""
        for session_id, entry in touched.items():
            try:
                row = await self._session_service.get(session_id)
            except AppError:  # pragma: no cover — committed a moment ago
                continue
            await refresh(self._session, row)
            if row.lifecycle is not SessionLifecycle.FINISHED:
                continue
            await self._session_service.refresh_derived(
                row, actor=actor, reason=SYNC_REASON, match=entry.finished
            )


def _session_patch(mutation: MutationInput) -> dict[str, Any]:
    """The session fields one mutation writes."""
    fields = mutation.fields
    match mutation.kind:
        case MutationKind.SESSION_START:
            return {
                "start_time": fields.get("start_time"),
                "timezone": fields.get("timezone") or athlete_timezone(),
            }
        case MutationKind.SESSION_FINISH:
            return {"end_time": fields.get("end_time")}
        case MutationKind.SESSION_RATING:
            return {"session_rating": fields.get("session_rating")}
        case MutationKind.DONT_LEARN:
            dont_learn = bool(fields.get("dont_learn"))
            # The reason travels with the flag at the same stamp: a reason
            # older than the toggle it explains would be a reason for a
            # different decision.
            return {
                "dont_learn": dont_learn,
                "dont_learn_reason": fields.get("reason") if dont_learn else None,
            }
        case _:  # pragma: no cover — set and note kinds are routed elsewhere
            raise ValidationError(f"{mutation.kind} is not a session mutation")


def _project_session(row: SessionRow, values: Mapping[str, Any]) -> None:
    """Write the merged session fields onto its row, checking them first.

    Raises:
        ValidationError: When the merged session breaks a rule — an end before
            its start, a session longer than a day, a rating off its scale, a
            reason without the flag.
    """
    start = _instant(values.get("start_time"), "start_time")
    timezone = str(values.get("timezone") or athlete_timezone())
    with domain_rules():
        parse_timezone(timezone)
        local_date = session_date(start, timezone)
    end_raw = values.get("end_time")
    end = start if end_raw is None else _instant(end_raw, "end_time")
    if (
        not dt.timedelta(0)
        <= end - start
        <= dt.timedelta(seconds=MAX_MANUAL_DURATION_S)
    ):
        raise ValidationError(
            "end_time must be after start_time and within a day of it"
        )
    dont_learn = bool(values.get("dont_learn", False))
    reason = values.get("dont_learn_reason")
    rating = values.get("session_rating")
    check_session_context(
        dont_learn=dont_learn, dont_learn_reason=reason, session_rating=rating
    )
    row.start_time = start
    row.end_time = end
    row.timezone = timezone
    row.local_date = local_date
    row.lifecycle = (
        SessionLifecycle.ACTIVE if end_raw is None else SessionLifecycle.FINISHED
    )
    row.session_rating = rating
    row.dont_learn = dont_learn
    row.dont_learn_reason = reason if dont_learn else None


def _instant(raw: Any, name: str) -> dt.datetime:
    """An aware instant from JSON text, inside the bounds a session may have.

    Raises:
        ValidationError: When it is missing, naive or out of bounds.
    """
    if raw is None:
        raise ValidationError(f"{name} is required")
    try:
        value = dt.datetime.fromisoformat(str(raw))
    except ValueError as error:  # pragma: no cover — the schema parsed it
        raise ValidationError(f"{name} is not an instant") from error
    if value.tzinfo is None:
        raise ValidationError(f"{name} must carry an offset")
    if not EARLIEST_SESSION <= value <= LATEST_SESSION:
        raise ValidationError(
            f"{name} must be between {EARLIEST_SESSION:%Y} and {LATEST_SESSION:%Y}"
        )
    return value


def _optional[T](values: Mapping[str, Any], name: str, cast: type[T]) -> T | None:
    raw = values.get(name)
    return None if raw is None else cast(raw)  # type: ignore[call-arg]


def _set_input(values: Mapping[str, Any], *, client_id: uuid.UUID) -> LoggedSetInput:
    """The merged set fields as the input `build_logged_set` takes.

    A field never sent takes the input's default, except ``source``: a set
    the phone logged is ``app``, not the ``recap`` a typed-in one defaults to.
    """
    completed = values.get("completed_at")
    return LoggedSetInput(
        client_id=client_id,
        exercise_id=values.get("exercise_id"),
        exercise_name=values.get("exercise_name"),
        reps=values.get("reps"),
        duration_s=values.get("duration_s"),
        per_side=bool(values.get("per_side", False)),
        load_kg=values.get("load_kg"),
        rir=values.get("rir"),
        notes=values.get("notes"),
        set_kind=LoggedSetKind(values.get("set_kind") or LoggedSetKind.WORKING),
        side=SetSide(values.get("side") or SetSide.BOTH),
        planned_line_ref=values.get("planned_line_ref"),
        replaced_exercise_id=values.get("replaced_exercise_id"),
        source=SetSource(values.get("source") or SetSource.APP),
        confidence=SetConfidence(values.get("confidence") or SetConfidence.HIGH),
        rir_capped=bool(values.get("rir_capped", False)),
        rpe=values.get("rpe"),
        hold_quality=_optional(values, "hold_quality", HoldQuality),
        effort_simple=_optional(values, "effort_simple", EffortSimple),
        rest_s=values.get("rest_s"),
        completed_at=None if completed is None else _instant(completed, "completed_at"),
        utc_offset_min=values.get("utc_offset_min"),
        pain_kind=_optional(values, "pain_kind", PainKind),
        pain_band=_optional(values, "pain_band", PainBand),
    )

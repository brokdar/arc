"""Use-cases for the workout library: create, read, search, edit, delete.

The library is a store of *reusable* prescriptions. Nothing here freezes
anything — that is the planned session's job (`app.services.planned_sessions`),
which snapshots the structure into an intent version precisely so that editing
a library workout later cannot rewrite what was prescribed on a past date.

Every structure that goes in or comes out passes through the domain
(`workout_body_from_json`). On write that is validation; on read it is a
guard, so a row written before a rule existed fails loudly here instead of
feeding a renderer or a scorer something the model no longer allows.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError, domain_rules
from app.domain.actor import Actor
from app.domain.athlete import Discipline
from app.domain.strength import StrengthWorkout, exercise_ids
from app.domain.workout import (
    WorkoutBody,
    discipline_of,
    flatten,
    total_duration_s,
    workout_body_from_json,
    workout_body_to_json,
)
from app.persistence.audit import AuditRepository
from app.persistence.db import commit
from app.persistence.workouts import (
    MAX_TAG_LENGTH,
    WorkoutRepository,
    WorkoutRow,
)
from app.services.exercises import ExerciseService
from app.services.guardrails import check_write_cap

#: `entity_type` written on this use-case's audit rows.
ENTITY_TYPE = "workout"

#: Fields `update` accepts.
UPDATABLE_FIELDS = ("name", "description", "structure", "folder", "tags")

#: Most tags one workout may carry. A bound, not a rule about taxonomy.
MAX_TAGS = 20


class WorkoutSummary:
    """Derived, never-stored facts about a prescription.

    Computed from the structure on every read for the same reason zones are
    (`app.domain.zones`): a stored copy of something derived from a document
    is the thing that goes stale when the document is edited.
    """

    __slots__ = ("step_count", "total_duration_s", "total_sets")

    def __init__(self, body: WorkoutBody) -> None:
        if isinstance(body, StrengthWorkout):
            self.step_count = len(body.prescriptions)
            self.total_duration_s: int | None = None
            self.total_sets: int | None = body.total_sets
        else:
            self.step_count = len(flatten(body))
            self.total_duration_s = total_duration_s(body)
            self.total_sets = None


@dataclass(frozen=True, slots=True)
class WorkoutDraft:
    """The workout :meth:`WorkoutService.create` would write, unsaved.

    Everything on it has been through the same checks the write applies, so
    ``tags`` are the *normalized* tags — stripped, lowercased, deduplicated and
    sorted — and not the ones the caller sent. That is the point: a dry run
    that echoed the request back would agree with the caller about a request
    the real call is going to refuse or rewrite.
    """

    name: str
    description: str | None
    folder: str | None
    tags: tuple[str, ...]
    body: WorkoutBody

    @property
    def discipline(self) -> Discipline:
        """Which discipline the parsed prescription belongs to."""
        return discipline_of(self.body)

    @property
    def step_count(self) -> int:
        """How many steps the prescription flattens to."""
        return WorkoutSummary(self.body).step_count


class WorkoutService:
    """Use-cases for the workout library. Raises AppError subclasses."""

    def __init__(
        self,
        session: AsyncSession,
        repository: WorkoutRepository,
        audit: AuditRepository,
        exercises: ExerciseService,
    ) -> None:
        self._session = session
        self._repository = repository
        self._audit = audit
        self._exercises = exercises

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(
            session,
            WorkoutRepository(session),
            AuditRepository(session),
            ExerciseService.from_session(session),
        )

    async def list(
        self,
        *,
        query: str | None = None,
        folder: str | None = None,
        tag: str | None = None,
        discipline: Discipline | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[WorkoutRow], int]:
        """Return a page of the library, newest first, plus the total."""
        return await self._repository.list(
            query=query,
            folder=folder,
            tag=tag,
            discipline=discipline,
            offset=offset,
            limit=limit,
        )

    async def get(self, workout_id: uuid.UUID) -> WorkoutRow:
        """Return one workout by id.

        Raises:
            NotFoundError: When no workout has that id.
        """
        row = await self._repository.get(workout_id)
        if row is None:
            raise NotFoundError(f"Workout {workout_id} not found")
        return row

    async def folders(self) -> Sequence[str]:
        """Return every folder label in use."""
        return await self._repository.folders()

    async def tags(self) -> Sequence[str]:
        """Return every tag in use."""
        return await self._repository.tags()

    async def preview(
        self,
        *,
        name: str,
        structure: Mapping[str, Any],
        description: str | None = None,
        folder: str | None = None,
        tags: Sequence[str] = (),
    ) -> WorkoutDraft:
        """Build and validate the workout :meth:`create` would write.

        The dry run of a create, and a **separate read-only method rather than
        a flag on the writer** — the shape `AnchorService.preview` established
        (D178). Every rule `create` applies is applied here, because `create`
        calls this to build its row: there is no second code path to disagree
        with, and a dry run that ran only *some* of the validation would tell
        an agent its call is fine and then refuse it.

        Returns:
            The draft, unsaved and unrecorded. Its tags are normalized.

        Raises:
            ValidationError: For exactly the reasons `create` would raise it —
                an illegal prescription, an unknown exercise, malformed tags.
        """
        return WorkoutDraft(
            name=name,
            description=description,
            folder=folder,
            tags=tuple(_clean_tags(tags)),
            body=await self._parse(structure),
        )

    async def create(
        self,
        *,
        actor: Actor,
        name: str,
        structure: Mapping[str, Any],
        description: str | None = None,
        folder: str | None = None,
        tags: Sequence[str] = (),
    ) -> WorkoutRow:
        """Add a workout to the library.

        Raises:
            ValidationError: When the structure is not a legal prescription,
                references an unknown exercise, or the tags are malformed.
            RateLimitedError: When an agent actor's trailing-hour write cap is
                spent (WP-8.3). Here rather than in the MCP tool, so it binds
                every path an agent can reach this write through.
        """
        await check_write_cap(self._session, actor)
        draft = await self.preview(
            name=name,
            structure=structure,
            description=description,
            folder=folder,
            tags=tags,
        )
        row = WorkoutRow(
            name=draft.name,
            description=draft.description,
            discipline=draft.discipline,
            structure=workout_body_to_json(draft.body),
            folder=draft.folder,
        )
        self._repository.set_tags(row, list(draft.tags))
        row = await self._repository.add(row)
        await self._audit.record(
            actor=actor,
            action="workout.created",
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            payload=_payload(row),
        )
        await commit(self._session)
        return row

    async def update(
        self, workout_id: uuid.UUID, updates: Mapping[str, Any], *, actor: Actor
    ) -> WorkoutRow:
        """Partially update a workout.

        ``updates`` holds only the fields the caller supplied. Changing the
        structure may change the workout's discipline; that is allowed, and
        the derived column follows.

        Raises:
            NotFoundError: When no workout has that id.
            ValidationError: When a field is unknown or a value is illegal.
        """
        unknown = set(updates) - set(UPDATABLE_FIELDS)
        if unknown:
            raise ValidationError(
                f"Unknown workout fields: {', '.join(sorted(unknown))}"
            )
        # `description` and `folder` are nullable and an explicit null clears
        # them; a workout without a name or a structure is not a workout.
        for required in ("name", "structure"):
            if required in updates and updates[required] is None:
                raise ValidationError(f"{required} cannot be cleared")
        row = await self.get(workout_id)
        before = _payload(row)

        if "structure" in updates:
            body = await self._parse(updates["structure"])
            row.structure = workout_body_to_json(body)
            row.discipline = discipline_of(body)
        for name in ("name", "description", "folder"):
            if name in updates:
                setattr(row, name, updates[name])
        if "tags" in updates:
            self._repository.set_tags(row, _clean_tags(updates["tags"] or ()))

        row = await self._repository.add(row)
        await self._audit.record(
            actor=actor,
            action="workout.updated",
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            payload={"before": before, "after": _payload(row)},
        )
        await commit(self._session)
        return row

    async def delete(self, workout_id: uuid.UUID, *, actor: Actor) -> None:
        """Remove a workout from the library.

        Planned sessions that were built from it are unaffected: each keeps
        its own frozen snapshot, and only the provenance link is nulled.

        Raises:
            NotFoundError: When no workout has that id.
        """
        row = await self.get(workout_id)
        payload = _payload(row)
        await self._repository.delete(row)
        await self._audit.record(
            actor=actor,
            action="workout.deleted",
            entity_type=ENTITY_TYPE,
            entity_id=workout_id,
            payload=payload,
        )
        await commit(self._session)

    async def parse_structure(self, structure: Mapping[str, Any]) -> WorkoutBody:
        """Validate a structure document and return the domain value.

        Public because the planned-session service validates inline
        prescriptions the same way, and neither adapter may re-implement it.

        Raises:
            ValidationError: When the structure is not a legal prescription
                or references an unknown exercise.
        """
        return await self._parse(structure)

    async def _parse(self, structure: Any) -> WorkoutBody:
        """Parse and validate a structure document."""
        with domain_rules():
            body = workout_body_from_json(structure)
        if isinstance(body, StrengthWorkout):
            await self._exercises.require_all(sorted(exercise_ids(body)))
        return body


def body_of(row: WorkoutRow) -> WorkoutBody:
    """Parse a stored workout back into the domain.

    Raises:
        ValueError: When the stored document no longer satisfies the domain's
            rules. Deliberately loud: silently tolerating it would let a
            renderer or a scorer act on a prescription the model rejects.
    """
    return workout_body_from_json(row.structure)


def summarize(row: WorkoutRow) -> WorkoutSummary:
    """Return the derived summary of a stored workout."""
    return WorkoutSummary(body_of(row))


#: How a *stored* structure can fail to parse. The domain's decoders raise
#: `ValueError` for everything they anticipate, but this reads a JSON column
#: that a caller wrote, a migration rewrote, or an older version of the model
#: accepted — so a document shaped nothing like a prescription can still reach
#: an attribute lookup or an index on the way to being rejected.
#:
#: Named and shared rather than repeated as a bare `except ValueError`, because
#: the callers are all *list* projections: one stale document must cost its own
#: row a step count, not cost the caller the whole page.
PARSE_FAILURES = (ValueError, TypeError, LookupError, AttributeError, RecursionError)


def step_count_of(row: WorkoutRow) -> int | None:
    """How many steps a stored structure flattens to, or None if it no longer parses.

    ``None`` rather than an exception: a library the athlete (or the agent)
    cannot list because one old document went stale is worse than a list with
    a null in it, and the null is visible where a swallowed error would not be.
    """
    try:
        return summarize(row).step_count
    except PARSE_FAILURES:
        return None


def _clean_tags(tags: Sequence[str]) -> list[str]:
    """Normalize and check a tag list.

    Raises:
        ValidationError: When a tag is blank, too long, or there are too many.
    """
    cleaned = []
    for tag in tags:
        stripped = tag.strip().lower()
        if not stripped:
            raise ValidationError("A tag must not be blank")
        if len(stripped) > MAX_TAG_LENGTH:
            raise ValidationError(
                f"A tag must be at most {MAX_TAG_LENGTH} characters, got {len(stripped)}"
            )
        cleaned.append(stripped)
    unique = sorted(set(cleaned))
    if len(unique) > MAX_TAGS:
        raise ValidationError(
            f"A workout may carry at most {MAX_TAGS} tags, got {len(unique)}"
        )
    return unique


def _payload(row: WorkoutRow) -> dict[str, Any]:
    """The workout's identifying fields, as JSON, for the audit trail.

    The structure itself is deliberately absent: an audit payload holding a
    full step tree per edit makes the trail unreadable, and the trail's job is
    to say *that* the prescription changed and by whom.
    """
    return {
        "name": row.name,
        "description": row.description,
        "discipline": row.discipline.value,
        "folder": row.folder,
        "tags": row.tag_names,
        "step_count": step_count_of(row),
    }

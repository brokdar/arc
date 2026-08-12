"""Use-cases for the exercise catalogue: seed it, read it.

**Seeding is lazy and idempotent**, on first access of the catalogue — the
same shape as the athlete bootstrap, and for the same reasons. A
migration cannot own it, because the integration suite truncates every table
between tests and a restore-from-dump would leave an application whose
strength prescriptions reference nothing. The application lifespan cannot own
it either without making a successful boot depend on a writable database,
which today it does not.

What the lifespan *does* own is validating the bundled file
(`app.services.templates.verify_bundled_resources`), so a malformed catalogue
is a failed deploy rather than a failed request.

The catalogue is reference data, so seeding writes **one** audit row, and only
when it actually changed something.
"""

from collections.abc import Sequence
from typing import Any, Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.domain.actor import Actor
from app.domain.strength import Exercise, ExerciseCategory
from app.persistence.audit import AuditRepository
from app.persistence.db import commit
from app.persistence.exercises import ExerciseRepository, ExerciseRow
from app.services.templates import load_exercise_catalogue

#: `entity_type` written on this use-case's audit rows.
ENTITY_TYPE = "exercise_catalogue"


class ExerciseService:
    """Use-cases for the exercise catalogue. Raises AppError subclasses."""

    def __init__(
        self,
        session: AsyncSession,
        repository: ExerciseRepository,
        audit: AuditRepository,
    ) -> None:
        self._session = session
        self._repository = repository
        self._audit = audit

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(session, ExerciseRepository(session), AuditRepository(session))

    async def list(
        self,
        *,
        category: ExerciseCategory | None = None,
        query: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[ExerciseRow], int]:
        """Return a page of the catalogue, seeding it first if it is empty."""
        await self.ensure_seeded()
        return await self._repository.list(
            category=category, query=query, offset=offset, limit=limit
        )

    async def get(self, exercise_id: str) -> ExerciseRow:
        """Return one catalogue entry by slug.

        Raises:
            NotFoundError: When no entry has that slug.
        """
        await self.ensure_seeded()
        row = await self._repository.get(exercise_id)
        if row is None:
            raise NotFoundError(f"Exercise {exercise_id!r} is not in the catalogue")
        return row

    async def require_all(self, exercise_ids: Sequence[str]) -> None:
        """Check that every slug is in the catalogue.

        Called before a strength prescription is stored. A typo'd slug would
        otherwise sit in a workout until someone tried to render or score it,
        by which time the mistake is months old.

        Raises:
            ValidationError: Naming every slug that is not in the catalogue.
                Worded without naming any route or tool: this service is
                behind more than one adapter, and each surface has its own
                way to list the catalogue.
        """
        await self.ensure_seeded()
        known = {row.id for row in await self._repository.all()}
        unknown = sorted(set(exercise_ids) - known)
        if unknown:
            raise ValidationError(
                f"unknown exercise(s): {', '.join(unknown)}; not in the "
                "exercise catalogue — list the catalogue for the valid slugs"
            )

    async def ensure_seeded(self, *, actor: Actor | None = None) -> int:
        """Bring the catalogue table in line with the bundled file.

        Idempotent: entries are matched by slug, missing ones inserted and
        changed ones updated. Nothing is ever deleted — a slug that leaves the
        file may still be referenced by a stored prescription, and losing the
        name would make that prescription unreadable.

        **Call this before loading any row the same use-case goes on to
        mutate.** Unlike every other write in this layer, the seed commits in
        the *middle* of whatever transaction it is called from, and its
        lost-race branch rolls that transaction back. The commit is harmless
        on its own (`expire_on_commit=False`, `app.persistence.db`), but a
        rollback expires every instance in the session — so a caller that
        loaded a row first, seeded second and wrote third would be flushing an
        object whose in-memory state had been discarded, and would lose the
        edits it had already made. The safe order is: seed, then load, then
        write. A caller that genuinely cannot follow it should run the seed in
        its own transaction (`session_scope()`) rather than reorder the
        commit here — the commit is what makes a lazy first-access seed
        visible to the request that triggered it.

        Args:
            actor: Credited with the seed. Defaults to the system, which is
                what a lazy first-access seed genuinely is.

        Returns:
            How many rows were written. Zero on every call after the first,
            which is what makes this cheap enough to call from a read.
        """
        catalogue = load_exercise_catalogue()
        existing = {row.id: row for row in await self._repository.all()}
        pending = [
            exercise
            for exercise in catalogue
            if _differs(existing.get(exercise.id), exercise)
        ]
        if not pending:
            return 0

        try:
            for exercise in pending:
                await self._repository.add(
                    ExerciseRow(
                        id=exercise.id,
                        name=exercise.name,
                        category=exercise.category,
                        unilateral=exercise.unilateral,
                    )
                )
        except ConflictError:
            # Two first-ever accesses raced; the winner has written the rows
            # this one was about to. The failed flush rolled the session back,
            # so there is nothing of ours left and the catalogue is correct.
            return 0

        await self._audit.record(
            actor=actor or Actor.system(),
            action="exercise_catalogue.seeded",
            entity_type=ENTITY_TYPE,
            entity_id=None,
            payload=_seed_payload(pending, existing),
        )
        await commit(self._session)
        return len(pending)


def _differs(row: ExerciseRow | None, exercise: Exercise) -> bool:
    """Whether the stored row is missing or out of step with the file."""
    if row is None:
        return True
    return (
        row.name != exercise.name
        or row.category is not exercise.category
        or row.unilateral != exercise.unilateral
    )


def _seed_payload(
    pending: Sequence[Exercise], existing: dict[str, ExerciseRow]
) -> dict[str, Any]:
    """What the seed changed, as JSON, for the audit trail."""
    added = sorted(entry.id for entry in pending if entry.id not in existing)
    updated = sorted(entry.id for entry in pending if entry.id in existing)
    return {"added": added, "updated": updated, "total": len(pending)}

"""Use-cases for swaps and the athlete's preferences per exercise.

The ranking is `app.domain.swaps`'s; this module builds the
:class:`~app.domain.swaps.SwapContext` it reads from stored rows — the avoid
and less-often lists, remembered picks, the exercises ever logged, and the
restrictions recent sharp pain implies — so the packet, the pain card, the
HTTP endpoint and the coach's tool all rank with the same inputs.

**Ease back in advances only on the athlete's tap** (:meth:`advance_ease_back`).
Nothing else moves the step: not logging a session of the current step, not
a good effort rating, not time passing. The athlete is coming back from
something only they can feel, and a ladder that climbed itself after one
easy session would put them back on the movement that hurt before they
said they were ready. Pinned by
`test_exercise_preferences_api.py::test_ease_back_in_advances_only_on_the_athletes_tap`.

:meth:`update` and :meth:`advance_ease_back` commit; :meth:`remember` does
not — it runs inside the outbox batch (`app.services.sync`) or a card answer
(`app.services.cards`), which commits once.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.domain.actor import Actor
from app.domain.safety import blocks_increase
from app.domain.strength import Exercise
from app.domain.swaps import (
    SwapContext,
    ease_back_ladder,
    restrictions_from_pain,
    substitutes,
)
from app.persistence.activity import SessionRepository
from app.persistence.audit import AuditRepository
from app.persistence.db import commit
from app.persistence.exercise_preferences import (
    ExercisePreferenceRepository,
    ExercisePreferenceRow,
)
from app.persistence.exercises import ExerciseRow
from app.services.exercises import ExerciseService

#: `entity_type` on this module's audit rows.
ENTITY_TYPE = "exercise_preference"

#: The fields :meth:`ExercisePreferencesService.update` takes.
UPDATABLE = frozenset({"avoid", "less_often", "remembered_swap_id", "ease_back"})


@dataclass(frozen=True, slots=True)
class EaseBack:
    """Where the athlete stands on *Ease back in* for one exercise.

    ``steps`` is the ladder, easiest first; ``step`` the 0-based index of the
    variation to train now. Past the last step is the exercise itself, which
    is where :meth:`ExercisePreferencesService.advance_ease_back` ends it.
    """

    step: int
    steps: tuple[ExerciseRow, ...]

    @property
    def current(self) -> ExerciseRow:
        """The variation to train now."""
        return self.steps[self.step]


@dataclass(frozen=True, slots=True)
class ExercisePreference:
    """What the athlete prefers about one exercise; defaults when nothing was said."""

    exercise_id: str
    avoid: bool
    less_often: bool
    remembered_swap_id: str | None
    ease_back: EaseBack | None


@dataclass(frozen=True, slots=True)
class Library:
    """The catalogue with its domain values and the context to rank it in."""

    rows: Mapping[str, ExerciseRow]
    domain: Mapping[str, Exercise]
    context: SwapContext

    def substitutes(self, exercise_id: str) -> list[ExerciseRow]:
        """The ranked swaps for one slug; ``[]`` for one outside the catalogue."""
        original = self.domain.get(exercise_id)
        if original is None:
            return []
        found = substitutes(original, self.domain.values(), self.context)
        return [self.rows[exercise.id] for exercise in found]

    def ladder(self, exercise_id: str) -> tuple[ExerciseRow, ...]:
        """The *Ease back in* ladder for one slug, easiest first."""
        original = self.domain.get(exercise_id)
        if original is None:
            return ()
        found = ease_back_ladder(original, self.domain.values(), self.context)
        return tuple(self.rows[exercise.id] for exercise in found)

    def ease_back(self, row: ExercisePreferenceRow | None) -> EaseBack | None:
        """The stored step against today's ladder, clamped if the ladder shrank."""
        if row is None or row.ease_back_step is None:
            return None
        steps = self.ladder(row.exercise_id)
        if not steps:
            return None
        return EaseBack(step=min(row.ease_back_step, len(steps) - 1), steps=steps)


class ExercisePreferencesService:
    """Swaps and preferences. Raises AppError subclasses."""

    def __init__(
        self,
        session: AsyncSession,
        repository: ExercisePreferenceRepository,
        exercises: ExerciseService,
        history: SessionRepository,
        audit: AuditRepository,
    ) -> None:
        self._session = session
        self._repository = repository
        self._exercises = exercises
        self._history = history
        self._audit = audit

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its collaborators to one session."""
        return cls(
            session,
            ExercisePreferenceRepository(session),
            ExerciseService.from_session(session),
            SessionRepository(session),
            AuditRepository(session),
        )

    # --- reads ---------------------------------------------------------------

    async def library(self) -> Library:
        """The catalogue and the athlete's swap context, read once.

        Seeds the catalogue first (it may commit — see
        `ExerciseService.ensure_seeded`), so a caller loading rows of its own
        reads this before them.
        """
        catalogue = await self._exercises.catalogue()
        domain = {slug: row.to_domain() for slug, row in catalogue.items()}
        rows = await self._repository.all()
        known = await self._history.last_logged_sets()
        pain = await self._history.latest_session_pain()
        painful = [
            domain[slug]
            for slug, kinds in pain.items()
            if slug in domain and blocks_increase(kinds)
        ]
        context = SwapContext(
            restrictions=restrictions_from_pain(painful),
            avoided=frozenset(r.exercise_id for r in rows if r.avoid),
            less_often=frozenset(r.exercise_id for r in rows if r.less_often),
            known=frozenset(slug for slug in known if slug in domain),
            remembered={
                r.exercise_id: r.remembered_swap_id
                for r in rows
                if r.remembered_swap_id is not None
            },
        )
        return Library(rows=catalogue, domain=domain, context=context)

    async def rows(self) -> dict[str, ExercisePreferenceRow]:
        """Every stored preference row by slug, for a caller that reads many."""
        return {row.exercise_id: row for row in await self._repository.all()}

    async def substitutes(self, exercise_id: str) -> list[ExerciseRow]:
        """The ranked swaps for one exercise — the packet's three for its lines.

        Raises:
            NotFoundError: When no exercise has that slug.
        """
        library = await self.library()
        _require(library, exercise_id)
        return library.substitutes(exercise_id)

    async def get(self, exercise_id: str) -> ExercisePreference:
        """The preferences on one exercise; all defaults when none were stated.

        Raises:
            NotFoundError: When no exercise has that slug.
        """
        library = await self.library()
        _require(library, exercise_id)
        return _view(library, exercise_id, await self._repository.get(exercise_id))

    async def list(self) -> list[ExercisePreference]:
        """Every exercise the athlete has stated a preference on, by slug."""
        library = await self.library()
        return [
            _view(library, row.exercise_id, row)
            for row in await self._repository.all()
            if _stated(row)
        ]

    # --- writes --------------------------------------------------------------

    async def update(
        self, exercise_id: str, changes: Mapping[str, Any], *, actor: Actor
    ) -> ExercisePreference:
        """Change the given fields of one exercise's preferences, and commit.

        ``remembered_swap_id: None`` forgets the pick, which restores the
        computed order. ``ease_back: True`` starts the ladder at its easiest
        step (and leaves a ladder already under way where it is);
        ``ease_back: False`` stops it.

        Raises:
            NotFoundError: When either slug is not in the catalogue.
            ValidationError: When an exercise is named as its own swap.
            ConflictError: When *Ease back in* is asked for an exercise
                nothing in the library is easier than.
        """
        unknown = set(changes) - UPDATABLE
        if unknown:  # pragma: no cover — the API schema forbids extra keys
            raise ValidationError(f"Unknown preference fields: {sorted(unknown)}")
        library = await self.library()
        _require(library, exercise_id)
        row = await self._repository.get_or_new(exercise_id)
        if "avoid" in changes:
            row.avoid = bool(changes["avoid"])
        if "less_often" in changes:
            row.less_often = bool(changes["less_often"])
        if "remembered_swap_id" in changes:
            pick = changes["remembered_swap_id"]
            if pick is not None:
                _require(library, pick)
                if pick == exercise_id:
                    raise ValidationError(
                        f"{exercise_id!r} cannot be remembered as its own swap"
                    )
            row.remembered_swap_id = pick
        if "ease_back" in changes:
            if not changes["ease_back"]:
                row.ease_back_step = None
            elif row.ease_back_step is None:
                if not library.ladder(exercise_id):
                    raise ConflictError(
                        f"Nothing in the library is an easier variation of "
                        f"{library.rows[exercise_id].name}, so there is no "
                        "ladder to ease back in on."
                    )
                row.ease_back_step = 0
        row = await self._repository.save(row)
        await self._audit.record(
            actor=actor,
            action="exercise_preference.updated",
            entity_type=ENTITY_TYPE,
            entity_id=None,
            payload={"exercise_id": exercise_id, **dict(changes)},
        )
        await commit(self._session)
        return _view(await self.library(), exercise_id, row)

    async def advance_ease_back(
        self, exercise_id: str, *, actor: Actor
    ) -> ExercisePreference:
        """The athlete's tap: one step up the ladder, and commit.

        From the last step the next is the exercise itself, so the ladder
        ends (``ease_back`` reads null again).

        Raises:
            NotFoundError: When no exercise has that slug.
            ConflictError: When the athlete is not easing back in on it.
        """
        library = await self.library()
        _require(library, exercise_id)
        row = await self._repository.get(exercise_id)
        current = library.ease_back(row)
        if row is None or current is None:
            raise ConflictError(
                f"You are not easing back in on {library.rows[exercise_id].name}; "
                "start Ease back in first."
            )
        following = current.step + 1
        row.ease_back_step = following if following < len(current.steps) else None
        row = await self._repository.save(row)
        await self._audit.record(
            actor=actor,
            action="exercise_preference.ease_back_advanced",
            entity_type=ENTITY_TYPE,
            entity_id=None,
            payload={"exercise_id": exercise_id, "step": row.ease_back_step},
        )
        await commit(self._session)
        return _view(library, exercise_id, row)

    async def remember(self, exercise_id: str, swap_id: str, *, actor: Actor) -> None:
        """Remember ``swap_id`` as the athlete's pick for ``exercise_id``. No commit.

        The caller must have seeded the catalogue (the outbox does, before its
        first write); an unknown slug on either side is refused.

        Raises:
            NotFoundError: When either slug is not in the catalogue.
            ValidationError: When the two are the same exercise.
        """
        catalogue = await self._exercises.catalogue()
        for slug in (exercise_id, swap_id):
            if slug not in catalogue:
                raise NotFoundError(f"Exercise {slug!r} is not in the catalogue")
        if swap_id == exercise_id:
            raise ValidationError(f"{exercise_id!r} cannot be its own swap")
        row = await self._repository.get_or_new(exercise_id)
        if row.remembered_swap_id == swap_id:
            return
        row.remembered_swap_id = swap_id
        await self._repository.save(row)
        await self._audit.record(
            actor=actor,
            action="exercise_preference.swap_remembered",
            entity_type=ENTITY_TYPE,
            entity_id=None,
            payload={"exercise_id": exercise_id, "remembered_swap_id": swap_id},
        )


def _require(library: Library, exercise_id: str) -> None:
    if exercise_id not in library.rows:
        raise NotFoundError(f"Exercise {exercise_id!r} is not in the catalogue")


def _stated(row: ExercisePreferenceRow) -> bool:
    return (
        row.avoid
        or row.less_often
        or row.remembered_swap_id is not None
        or row.ease_back_step is not None
    )


def _view(
    library: Library, exercise_id: str, row: ExercisePreferenceRow | None
) -> ExercisePreference:
    if row is None:
        return ExercisePreference(exercise_id, False, False, None, None)
    return ExercisePreference(
        exercise_id=exercise_id,
        avoid=row.avoid,
        less_often=row.less_often,
        remembered_swap_id=row.remembered_swap_id,
        ease_back=library.ease_back(row),
    )


def swap_names(rows: Sequence[ExerciseRow]) -> list[tuple[str, str]]:
    """``(slug, name)`` per row: the projection every swap list uses."""
    return [(row.id, row.name) for row in rows]

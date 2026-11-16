"""An exercise's personal bests and estimated max, and a session's new bests.

Read-only use-cases over `logged_sets`, folded by
`app.domain.strength_records`. The HTTP routes and the coach's
`get_exercise_history` tool both call :meth:`StrengthHistoryService.exercise_history`,
so the athlete and the coach read one computation.

Nothing is stored: the records are derived on every read (the domain module
says why), which `test_strength_history_api.py` bounds at 20,000 sets.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.domain.metrics import PerformedSet
from app.domain.strength_records import (
    ExerciseRecords,
    Exposure,
    exercise_records,
    new_bests,
)
from app.persistence.exercises import ExerciseRow
from app.persistence.strength_history import (
    StrengthHistoryRepository,
    exercise_match,
)
from app.persistence.sync import SyncRepository
from app.services.exercises import ExerciseService
from app.services.sync import SESSION


@dataclass(frozen=True, slots=True)
class ExerciseHistory:
    """One catalogue exercise and its records."""

    exercise: ExerciseRow
    records: ExerciseRecords


@dataclass(frozen=True, slots=True)
class SessionBest:
    """A new personal best set in one session, named as it was logged."""

    exercise_id: str | None
    exercise_name: str
    load_kg: float
    reps: int


class StrengthHistoryService:
    """Use-cases for strength records. Raises AppError subclasses."""

    def __init__(
        self,
        repository: StrengthHistoryRepository,
        exercises: ExerciseService,
        sync: SyncRepository,
    ) -> None:
        self._repository = repository
        self._exercises = exercises
        self._sync = sync

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(
            StrengthHistoryRepository(session),
            ExerciseService.from_session(session),
            SyncRepository(session),
        )

    async def exercise_history(self, exercise_id: str) -> ExerciseHistory:
        """Bests, rep maxes and the estimate line of one catalogue exercise.

        An exercise never logged has empty records, not an error — it is in
        the catalogue, it simply has no history yet.

        Raises:
            NotFoundError: When no catalogue entry has that slug.
        """
        exercise = await self._exercises.get(exercise_id)
        history = await self._repository.exposures(
            exercise_match(exercise_id, exercise.name)
        )
        return ExerciseHistory(exercise=exercise, records=exercise_records(history))

    async def session_bests(
        self, session_client_id: uuid.UUID
    ) -> Sequence[SessionBest]:
        """The new personal bests of the session the phone knows by this id.

        Keyed by the phone's own session id because that is all the phone has
        when its Finish screen asks: the server's id is never sent back to it.
        Each exercise of the session is judged against every **finished**
        session of it that started earlier.

        Raises:
            NotFoundError: When no session with that phone id was uploaded.
        """
        entity = await self._sync.entity(SESSION, str(session_client_id))
        if entity is None or entity.session_id is None:
            raise NotFoundError(f"No session with client id {session_client_id}")
        facts, rows = await self._repository.session_sets(entity.session_id)
        if facts is None:  # pragma: no cover — the entity cascades with it
            raise NotFoundError(f"No session with client id {session_client_id}")
        by_exercise: dict[tuple[str | None, str], list[PerformedSet]] = {}
        names: dict[tuple[str | None, str], str] = {}
        for exercise_id, name, entry in rows:
            key = (exercise_id, "" if exercise_id is not None else name.lower())
            by_exercise.setdefault(key, []).append(entry)
            names.setdefault(key, name)

        out: list[SessionBest] = []
        for key, sets in by_exercise.items():
            exercise_id, name = key[0], names[key]
            prior = await self._repository.exposures(
                exercise_match(exercise_id, name),
                before=(facts.start_time, facts.id),
            )
            session = Exposure(
                session_id=facts.id,
                date=facts.local_date,
                sets=sets,
                dont_learn=facts.dont_learn,
            )
            out.extend(
                SessionBest(
                    exercise_id=exercise_id,
                    exercise_name=name,
                    load_kg=best.load_kg,
                    reps=best.reps,
                )
                for best in new_bests(prior, session)
            )
        return out

"""Fitness, fatigue and form over the recorded sessions.

The read behind `GET /fitness` and the MCP tool `get_fitness_trend`.

The arithmetic is `app.domain.pmc`'s; this service only gathers its input. Each
session contributes the load already selected and stored on its current metric
artefact (`app.domain.metrics.select_training_load`, read through
`app.services.metrics.summarise` like every other aggregate) on its
`local_date`, the day the athlete would say they trained on.

**The whole history, every read.** The averages start on the first session
ever recorded, so a window asks the domain to compute from there and slice —
reading only the window's sessions would restart the EWMA at its left edge and
describe a different athlete.

Display-only (`app.domain.authority`): no deterministic module may import this
one, and the walk in `tests/unit/test_display_only_authority.py` holds them to
it.
"""

import datetime as dt
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import athlete_today
from app.core.exceptions import InvalidRangeError
from app.domain.pmc import FitnessTrend, SessionLoad, fitness_trend
from app.persistence.activity import SessionRepository
from app.services.metrics import SessionMetricsService, summarise


class FitnessService:
    """Reads the fitness series. Raises AppError subclasses."""

    def __init__(
        self, sessions: SessionRepository, metrics: SessionMetricsService
    ) -> None:
        self._sessions = sessions
        self._metrics = metrics

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service to one session."""
        return cls(
            SessionRepository(session), SessionMetricsService.from_session(session)
        )

    async def trend(
        self,
        *,
        start: dt.date | None = None,
        end: dt.date | None = None,
        today: dt.date | None = None,
    ) -> FitnessTrend:
        """The series over the half-open window ``[start, end)``.

        Args:
            start: First day to return, inclusive; omitted, from the first
                session.
            end: First day *not* to return; omitted, through today. Clipped at
                today either way.
            today: The athlete's local today; read from their clock when
                omitted.

        Raises:
            InvalidRangeError: When ``start`` is after ``end``, carrying both;
                each adapter words it with its own argument names. Equal
                bounds are a legal, empty window, like every half-open range
                here.
        """
        if start is not None and end is not None and start > end:
            raise InvalidRangeError(start, end)
        rows = await self._sessions.all_oldest_first()
        current = await self._metrics.current_for_sessions(row.id for row in rows)
        loads = [
            SessionLoad(
                local_date=row.local_date,
                discipline=row.discipline,
                load=summarise(current[row.id]).training_load
                if row.id in current
                else None,
            )
            for row in rows
        ]
        return fitness_trend(
            loads, start=start, end=end, today=today or athlete_today()
        )

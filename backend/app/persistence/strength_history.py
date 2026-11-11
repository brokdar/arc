"""The logged-set reads behind personal bests and the estimated max.

Column tuples, not ORM rows: a history is read whole on every request (the
records are derived on read, `app.domain.strength_records`), and building
20,000 mapped objects with identity-map bookkeeping costs more than the rest
of the request together. Nothing here is ever written back, so nothing needs
to be an entity.
"""

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import (
    ColumnElement,
    ColumnExpressionArgument,
    Row,
    String,
    func,
    select,
    type_coerce,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.activity import EffortSimple, LoggedSetKind, SessionLifecycle, SetSide
from app.domain.metrics import PerformedSet
from app.domain.strength_records import Exposure
from app.persistence.activity import LoggedSetRow, SessionRow


@dataclass(frozen=True, slots=True)
class SessionFacts:
    """What a record needs to know about the session a set belongs to."""

    id: uuid.UUID
    start_time: dt.datetime
    local_date: dt.date
    dont_learn: bool


_SET_COLUMNS = (
    LoggedSetRow.set_kind,
    LoggedSetRow.side,
    LoggedSetRow.per_side,
    LoggedSetRow.reps,
    LoggedSetRow.duration_s,
    LoggedSetRow.load_kg,
    LoggedSetRow.rir,
    LoggedSetRow.rir_capped,
    LoggedSetRow.rpe,
    LoggedSetRow.effort_simple,
)


#: `_SET_COLUMNS` with the two enums read as the stored strings. A history
#: read converts them only once per distinct set (`exposures` shares identical
#: rows), not 40,000 times through the column type for 20,000 rows.
_RAW_SET_COLUMNS = (
    type_coerce(LoggedSetRow.set_kind, String).label("set_kind"),
    type_coerce(LoggedSetRow.side, String).label("side"),
    *_SET_COLUMNS[2:-1],
    type_coerce(LoggedSetRow.effort_simple, String).label("effort_simple"),
)


def _raw(column: ColumnExpressionArgument[uuid.UUID]) -> ColumnElement[Any]:
    """A session id as the driver returns it, labelled ``key``.

    A hex string on SQLite, the driver's own UUID value on Postgres: equal
    for equal ids within one dialect, which is all a grouping key needs, and
    without building a `uuid.UUID` per row (`StrengthHistoryRepository.exposures`).
    """
    return type_coerce(column, String).label("key")


def _performed(row: Row[*tuple[Any, ...]]) -> PerformedSet:
    """The domain's view of one result row's `_SET_COLUMNS`."""
    return PerformedSet(
        reps=row.reps,
        load_kg=row.load_kg,
        duration_s=row.duration_s,
        per_side=row.per_side,
        side=row.side,
        kind=row.set_kind,
        rir=row.rir,
        rir_capped=row.rir_capped,
        rpe=row.rpe,
        effort_simple=row.effort_simple,
    )


def _performed_raw(row: Row[*tuple[Any, ...]]) -> PerformedSet:
    """`_performed` over `_RAW_SET_COLUMNS`: the enums built from their values."""
    return PerformedSet(
        reps=row.reps,
        load_kg=row.load_kg,
        duration_s=row.duration_s,
        per_side=row.per_side,
        side=SetSide(row.side),
        kind=LoggedSetKind(row.set_kind),
        rir=row.rir,
        rir_capped=row.rir_capped,
        rpe=row.rpe,
        effort_simple=(
            None if row.effort_simple is None else EffortSimple(row.effort_simple)
        ),
    )


def exercise_match(exercise_id: str | None, name: str) -> ColumnElement[bool]:
    """Sets of one exercise: by slug, or a free-text entry by its name.

    A free-text movement has no identity but its name, so "Sled push" and
    "sled push" are one exercise and a catalogue entry's sets never match a
    free-text one that happens to share its name.
    """
    if exercise_id is not None:
        return LoggedSetRow.exercise_id == exercise_id
    return LoggedSetRow.exercise_id.is_(None) & (
        func.lower(LoggedSetRow.exercise_name) == name.lower()
    )


class StrengthHistoryRepository:
    """Read-only access to an exercise's logged sets, oldest first."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def exposures(
        self,
        match: ColumnElement[bool],
        *,
        before: tuple[dt.datetime, uuid.UUID] | None = None,
    ) -> list[Exposure]:
        """One exposure per finished session holding a set ``match`` selects.

        Sessions oldest first (start, then id), each with its matching sets in
        logged order (``set_index``, then id) — the order the one-side pairing
        reads. An active session is left out: half a workout is not yet
        history. ``before`` keeps only sessions that started strictly earlier
        than the given ``(start_time, id)``.

        Two queries rather than one join, so the session facts are read once
        per session instead of once per set. Per set, nothing is built that
        a 20,000-set history (`test_history_over_20000_sets_answers_in_under_200ms`)
        would pay for 20,000 times:

        * the sets are grouped by the session id **as the driver returns it**
          (`_raw`), never turned into a `uuid.UUID` — both queries read the
          key the same way, so it matches whatever the dialect returns, and a
          set whose session the first query did not see (one finished between
          the two statements) is left for the next read;
        * identical rows share one `PerformedSet`. A lifter repeats the same
          weight, reps and effort constantly, so a long history holds a few
          hundred distinct sets, and the value is frozen, so sharing it is
          safe. Building one object per row was a quarter of the read and
          most of the objects the garbage collector then had to walk.
        """
        finished: list[ColumnElement[bool]] = [
            SessionRow.lifecycle == SessionLifecycle.FINISHED
        ]
        if before is not None:
            start, session_id = before
            finished.append(
                (SessionRow.start_time < start)
                | ((SessionRow.start_time == start) & (SessionRow.id < session_id))
            )
        sessions = (
            await self._session.execute(
                select(
                    _raw(SessionRow.id),
                    SessionRow.id,
                    SessionRow.local_date,
                    SessionRow.dont_learn,
                )
                .where(
                    *finished,
                    SessionRow.id.in_(select(LoggedSetRow.session_id).where(match)),
                )
                .order_by(SessionRow.start_time, SessionRow.id)
            )
        ).all()
        sets: dict[Any, list[PerformedSet]] = {row.key: [] for row in sessions}
        seen: dict[tuple[Any, ...], PerformedSet] = {}
        result = await self._session.execute(
            select(_raw(LoggedSetRow.session_id), *_RAW_SET_COLUMNS)
            .where(
                match,
                LoggedSetRow.session_id.in_(select(SessionRow.id).where(*finished)),
            )
            .order_by(LoggedSetRow.set_index, LoggedSetRow.id)
        )
        for row in result:
            bucket = sets.get(row.key)
            if bucket is None:
                continue
            values = row[1:]
            performed = seen.get(values)
            if performed is None:
                performed = seen[values] = _performed_raw(row)
            bucket.append(performed)
        return [
            Exposure(
                session_id=row.id,
                date=row.local_date,
                sets=sets[row.key],
                dont_learn=row.dont_learn,
            )
            for row in sessions
        ]

    async def session_sets(
        self, session_id: uuid.UUID
    ) -> tuple[SessionFacts | None, list[tuple[str | None, str, PerformedSet]]]:
        """One session's facts and its sets in logged order, any lifecycle.

        Each set comes with the exercise it was logged as — its slug (or
        ``None``) and the name written on the row.
        """
        facts = (
            await self._session.execute(
                select(
                    SessionRow.id,
                    SessionRow.start_time,
                    SessionRow.local_date,
                    SessionRow.dont_learn,
                ).where(SessionRow.id == session_id)
            )
        ).one_or_none()
        result = await self._session.execute(
            select(LoggedSetRow.exercise_id, LoggedSetRow.exercise_name, *_SET_COLUMNS)
            .where(LoggedSetRow.session_id == session_id)
            .order_by(LoggedSetRow.set_index, LoggedSetRow.id)
        )
        rows: list[tuple[str | None, str, PerformedSet]] = [
            (row.exercise_id, row.exercise_name, _performed(row)) for row in result
        ]
        return (None if facts is None else SessionFacts(*facts)), rows

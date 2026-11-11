"""Response schemas for strength records: an exercise's history, a session's bests.

Every number here is derived on read by `app.domain.strength_records`;
:func:`to_history_read` and :func:`to_session_bests_read` are the one
projection the routes use and the generated frontend fixture is emitted
through (`backend/scripts/emit_strength_records_fixture.py`).
"""

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel

from app.services.strength_history import ExerciseHistory, SessionBest


class PersonalBestRead(BaseModel):
    """A set no other set of the exercise beat on both weight and reps."""

    load_kg: float
    reps: int
    #: The day it was first lifted.
    date: dt.date
    session_id: uuid.UUID


class RepMaxRead(BaseModel):
    """The heaviest weight lifted for at least ``target_reps`` reps."""

    target_reps: int
    load_kg: float
    #: The reps that set actually did, at least ``target_reps``.
    reps: int
    date: dt.date
    session_id: uuid.UUID


class EstimatePointRead(BaseModel):
    """One session's best estimated max, and the line's value after it."""

    session_id: uuid.UUID
    date: dt.date
    estimate_kg: float
    #: Median of the last three sessions' estimates; null before the third.
    median_kg: float | None


class EstimateLineRead(BaseModel):
    """The estimated-max line — a rough guide, never a personal best."""

    status: Literal["ok", "not_enough_data"]
    #: The line's latest value; null while there is not enough data.
    current_kg: float | None
    points: list[EstimatePointRead]


class ExerciseHistoryRead(BaseModel):
    """One exercise's personal bests, rep maxes and estimated max."""

    exercise_id: str
    exercise_name: str
    #: Sessions in which the exercise was logged at all.
    exposures: int
    #: Undominated sets, heaviest first.
    bests: list[PersonalBestRead]
    #: Heaviest for 1, 3, 5, 8 and 10 reps — only the counts reached.
    rep_maxes: list[RepMaxRead]
    estimate: EstimateLineRead


class SessionBestRead(BaseModel):
    """A new personal best in one session, named as it was logged."""

    #: Null for a free-text exercise, which is compared by its name.
    exercise_id: str | None
    exercise_name: str
    load_kg: float
    reps: int


class SessionBestsRead(BaseModel):
    """The finish summary's new bests; empty when the session set none."""

    bests: list[SessionBestRead]


def to_history_read(history: ExerciseHistory) -> ExerciseHistoryRead:
    """Project an exercise's records into the response body."""
    records = history.records
    return ExerciseHistoryRead(
        exercise_id=history.exercise.id,
        exercise_name=history.exercise.name,
        exposures=records.exposures,
        bests=[
            PersonalBestRead(
                load_kg=b.load_kg, reps=b.reps, date=b.date, session_id=b.session_id
            )
            for b in records.bests
        ],
        rep_maxes=[
            RepMaxRead(
                target_reps=m.target_reps,
                load_kg=m.load_kg,
                reps=m.reps,
                date=m.date,
                session_id=m.session_id,
            )
            for m in records.rep_maxes
        ],
        estimate=EstimateLineRead(
            status=records.estimate.status,
            current_kg=records.estimate.current_kg,
            points=[
                EstimatePointRead(
                    session_id=p.session_id,
                    date=p.date,
                    estimate_kg=p.estimate_kg,
                    median_kg=p.median_kg,
                )
                for p in records.estimate.points
            ],
        ),
    )


def to_session_bests_read(bests: Sequence[SessionBest]) -> SessionBestsRead:
    """Project a session's new bests into the response body."""
    return SessionBestsRead(
        bests=[
            SessionBestRead(
                exercise_id=b.exercise_id,
                exercise_name=b.exercise_name,
                load_kg=b.load_kg,
                reps=b.reps,
            )
            for b in bests
        ]
    )

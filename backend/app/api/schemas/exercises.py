"""Request/response schemas for the exercise catalogue and its notes.

The catalogue itself is read-only: it is bundled reference data seeded from a
file in the repository, so there is no create, update or delete payload for an
exercise — adding one is a change to `app/resources/exercise_catalogue.json`,
reviewed like any other change. The one writable thing is the athlete's note
on an exercise.
"""

import datetime as dt
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.api.pagination import Page
from app.api.validation import PostgresText
from app.domain.muscles import MuscleRegion
from app.domain.strength import (
    ExerciseCategory,
    ExerciseEquipment,
    LoadType,
    Mechanic,
    MovementPattern,
    Restriction,
)
from app.persistence.exercises import MAX_NOTE_LENGTH


class ExerciseRead(BaseModel):
    """One catalogue movement as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    #: The stable slug a prescription references.
    id: str
    name: str
    category: ExerciseCategory
    #: Whether the movement is performed one side at a time.
    unilateral: bool
    #: ``{region: weight}``: 1.0 for a main muscle, 0.5 for a helper.
    muscles: dict[MuscleRegion, float]
    mechanic: Mechanic
    pattern: MovementPattern
    #: Everything the movement needs, sorted; ``bodyweight`` means nothing.
    equipment: list[ExerciseEquipment]
    load_type: LoadType
    #: Demands a safety filter may rule out, sorted; often empty.
    restrictions: list[Restriction]
    created_at: dt.datetime
    updated_at: dt.datetime


ExercisesPage = Page[ExerciseRead]


class ExerciseNoteRead(BaseModel):
    """The athlete's note on one exercise.

    ``text`` is empty and ``updated_at`` null when there is no note — every
    exercise has a note slot, so "nothing written" is a value, not a 404.
    """

    model_config = ConfigDict(from_attributes=True)

    exercise_id: str
    text: str
    updated_at: dt.datetime | None


class ExerciseNoteWrite(BaseModel):
    """Replace the note on an exercise; an empty ``text`` deletes it."""

    model_config = ConfigDict(extra="forbid")

    text: Annotated[PostgresText, Field(max_length=MAX_NOTE_LENGTH)]

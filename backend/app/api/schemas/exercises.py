"""Request/response schemas for the exercise catalogue.

Read-only: the catalogue is bundled reference data seeded from a file in the
repository, so there is no create, update or delete payload here — adding an
exercise is a change to `app/resources/exercise_catalogue.json`, reviewed like
any other change.
"""

import datetime as dt

from pydantic import BaseModel, ConfigDict

from app.api.pagination import Page
from app.domain.strength import ExerciseCategory


class ExerciseRead(BaseModel):
    """One catalogue movement as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    #: The stable slug a prescription references.
    id: str
    name: str
    category: ExerciseCategory
    #: Whether the movement is performed one side at a time.
    unilateral: bool
    created_at: dt.datetime
    updated_at: dt.datetime


ExercisesPage = Page[ExerciseRead]

"""Request/response schemas for swaps and the athlete's preferences per exercise.

A swap is named by slug and display name only: everything else about the
movement is in the catalogue (and, for the phone, in the packet's library),
so repeating it here would be a second copy to keep in step.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from app.persistence.exercises import MAX_SLUG_LENGTH, ExerciseRow
from app.services.exercise_preferences import EaseBack, ExercisePreference

Slug = Annotated[str, Field(min_length=1, max_length=MAX_SLUG_LENGTH)]


class SwapRead(BaseModel):
    """One movement offered in place of another."""

    exercise_id: str
    exercise_name: str


class SubstitutesRead(BaseModel):
    """The ranked swaps for one exercise, best first; at most three, maybe none."""

    exercise_id: str
    substitutes: list[SwapRead]


class EaseBackRead(BaseModel):
    """Where the athlete stands on *Ease back in* for one exercise.

    ``steps`` runs easiest first; ``step`` is the 0-based index of
    ``current``, the variation to train now. It moves only when the athlete
    taps *advance*.
    """

    step: int
    steps: list[SwapRead]
    current: SwapRead


class ExercisePreferenceRead(BaseModel):
    """What the athlete prefers about one exercise; defaults when nothing was said."""

    exercise_id: str
    #: Never offered as a swap or suggested in the packet.
    avoid: bool
    #: Offered only after every other valid swap.
    less_often: bool
    #: The swap picked last time; it is offered first while it stays valid.
    remembered_swap_id: str | None
    #: Null when the athlete is not easing back in on this exercise.
    ease_back: EaseBackRead | None


class ExercisePreferenceUpdate(BaseModel):
    """Change some of one exercise's preferences; an omitted field is left alone.

    ``remembered_swap_id: null`` forgets the pick (the computed order comes
    back); ``ease_back: true`` starts *Ease back in* at its easiest step and
    ``false`` stops it.
    """

    model_config = ConfigDict(extra="forbid")

    avoid: bool | SkipJsonSchema[None] = None
    less_often: bool | SkipJsonSchema[None] = None
    remembered_swap_id: Slug | None = None
    ease_back: bool | SkipJsonSchema[None] = None


def swap_read(row: ExerciseRow) -> SwapRead:
    """One catalogue row as an offered swap."""
    return SwapRead(exercise_id=row.id, exercise_name=row.name)


def ease_back_read(ease_back: EaseBack | None) -> EaseBackRead | None:
    """The ladder as the API sends it, or null."""
    if ease_back is None:
        return None
    return EaseBackRead(
        step=ease_back.step,
        steps=[swap_read(row) for row in ease_back.steps],
        current=swap_read(ease_back.current),
    )


def preference_read(preference: ExercisePreference) -> ExercisePreferenceRead:
    """One exercise's preferences as the API sends them."""
    return ExercisePreferenceRead(
        exercise_id=preference.exercise_id,
        avoid=preference.avoid,
        less_often=preference.less_often,
        remembered_swap_id=preference.remembered_swap_id,
        ease_back=ease_back_read(preference.ease_back),
    )

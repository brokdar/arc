"""Request/response schemas for the athlete's training settings."""

from pydantic import BaseModel, ConfigDict
from pydantic.json_schema import SkipJsonSchema

from app.domain.training_mode import (
    AskFrequency,
    LiftedBefore,
    Locale,
    TrainingMode,
    Units,
)


class TrainingSettingsRead(BaseModel):
    """The training settings every gym surface reads.

    On a database where nothing has been stored these are the defaults:
    Simple, kilograms, English, nothing seen and nothing answered.
    """

    model_config = ConfigDict(from_attributes=True)

    mode: TrainingMode
    #: Log only: no suggestions and no progression.
    plain_logbook: bool
    #: How often a set asks how hard it felt.
    ask_frequency: AskFrequency
    units: Units
    locale: Locale
    high_contrast: bool
    #: The once-only safety screen has been dismissed.
    before_you_start_seen: bool
    #: The first-run question was answered or skipped; Today stops asking.
    first_run_seen: bool
    #: Null until answered — skipping the question leaves it null.
    lifted_before: LiftedBefore | None


class TrainingSettingsUpdate(BaseModel):
    """A partial change. Omitted fields are left alone.

    No setting can be cleared, so none takes ``null`` in the contract
    (`.claude/rules/api-nullability.md`); an explicit ``null`` that reaches
    the service anyway is a 422 naming the field. Sending ``lifted_before``
    also marks the question seen and, for a new answer, sets the mode
    (`app.domain.training_mode.with_changes`), unless ``mode`` is in the same
    request or Full control was chosen.
    """

    # `extra="forbid"`: a typo'd setting is a 422, not a silently lost change.
    model_config = ConfigDict(extra="forbid")

    mode: TrainingMode | SkipJsonSchema[None] = None
    plain_logbook: bool | SkipJsonSchema[None] = None
    ask_frequency: AskFrequency | SkipJsonSchema[None] = None
    units: Units | SkipJsonSchema[None] = None
    locale: Locale | SkipJsonSchema[None] = None
    high_contrast: bool | SkipJsonSchema[None] = None
    before_you_start_seen: bool | SkipJsonSchema[None] = None
    first_run_seen: bool | SkipJsonSchema[None] = None
    lifted_before: LiftedBefore | SkipJsonSchema[None] = None

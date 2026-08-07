"""Request/response schemas for completed sessions.

Two shapes, and the split is the same one the planned-session list makes
(D79): a **list row** is what a log line renders and costs one query for the
page, while the **detail** carries the recording metadata behind it — the
sources that produced each channel, the stops that were subtracted, and how
many repairs the cleaner made.

**Streams are not here.** ``GET /sessions/{id}`` answers with metadata; the
samples live in ``data/streams/`` and WP-5 owns the endpoints that read them.
A detail response that carried 14 400 rows per channel would be the wrong
resource for every page that exists today.
"""

import datetime as dt
import uuid
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from app.api.pagination import Page
from app.api.validation import PostgresText
from app.domain.activity import (
    ClassificationSource,
    RecordingKind,
    SessionDiscipline,
    SessionMatchStatus,
)
from app.persistence.activity import MAX_NOTES_LENGTH, MAX_TIMEZONE_LENGTH
from app.persistence.exercises import MAX_SLUG_LENGTH
from app.services.activity import (
    EARLIEST_SESSION,
    LATEST_SESSION,
    MAX_EXERCISE_NAME,
    MAX_LOAD_KG,
    MAX_MANUAL_DURATION_S,
    MAX_REPS,
    MAX_RIR,
    MAX_RPE,
    MAX_SETS,
    MIN_MANUAL_DURATION_S,
    MIN_RIR,
    MIN_RPE,
)

#: A timezone as it is written: bounded to the width of the column holding it.
TimezoneName = Annotated[PostgresText, Field(max_length=MAX_TIMEZONE_LENGTH)]


class RecordingStopRead(BaseModel):
    """One recording pause, as a half-open row range on the 1 Hz grid."""

    #: First row of the pause.
    start_index: int
    #: One past the last — ``[start, end)``, like every other range here.
    end_index: int


class RecordingRead(BaseModel):
    """One device file's account of a session (A4.3, A4.4).

    Everything a session detail page needs to explain its own numbers: which
    meter produced the power, how the choice was made, how irregular the file
    was, and what was subtracted from elapsed time to get the duration that
    training load is computed over.
    """

    id: uuid.UUID
    #: sha256 of the original file, hex — the dedup key's first half.
    file_hash: str
    #: Which sport within the file this recording is (A4.5). 0 for the
    #: single-sport files that are the normal case.
    file_sport_index: int
    #: The file's extension, lowercase. The original's *path* is deliberately
    #: not exposed: it is a server filesystem location.
    original_ext: str
    #: The raw sport string the file carried, if any.
    sport: str | None
    #: Last sample minus first.
    elapsed_time_s: float
    #: Elapsed minus every pause over 30 s. **The duration training load is
    #: computed over** (A4.4, A5.1) — not elapsed, not moving time.
    recording_time_s: float
    #: The pauses that were subtracted.
    recording_stops: list[RecordingStopRead]
    #: Median spacing of the original samples: the one-number answer to "how
    #: irregular was this file".
    median_time_delta_s: float
    #: Time at or above 1 km/h. **Display only** — never a load input.
    moving_time_s: float
    #: Every plausible power source the file named (A4.3); more than one means
    #: the file did not say which produced the numbers.
    power_source_candidates: list[str]
    power_source: str | None
    #: Why that source — ``"only candidate"`` when there was no choice.
    power_source_rule: str | None
    hr_source_candidates: list[str]
    hr_source: str | None
    hr_source_rule: str | None
    #: `StreamChannel` values present in the stored frame.
    channels: list[str]
    #: Regions the cleaner repaired (A4.2). Channels that needed nothing are
    #: **not** counted: a clean ride reports 0, not one per channel.
    anomaly_count: int
    created_at: dt.datetime


class LoggedSetRead(BaseModel):
    """One set the athlete logged by hand."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    #: Catalogue slug, or null for a free-text movement.
    exercise_id: str | None
    #: What the movement was called — the catalogue's name at the time of
    #: logging, or the athlete's own words.
    exercise_name: str
    set_index: int
    reps: int
    load_kg: float | None
    #: Reps in reserve, as reported after the set.
    rir: int | None
    notes: str | None


class SessionListItem(BaseModel):
    """One completed session, as a row in the log."""

    id: uuid.UUID
    #: The athlete-local day it belongs to. A midnight-crosser belongs to the
    #: day it began.
    local_date: dt.date
    start_time: dt.datetime
    #: IANA name, ``UTC±HH:MM``, or ``UTC``. Athlete-overridable.
    timezone: str
    discipline: SessionDiscipline
    classification_source: ClassificationSource
    #: Whether the athlete corrected the discipline.
    discipline_overridden: bool
    recording_kind: RecordingKind
    #: Reserved for WP-6: every session is ``unmatched`` until matching
    #: exists. The badge takes this as a prop rather than assuming.
    status: SessionMatchStatus
    #: What to render as the session's length: the recording time for a device
    #: session (pauses removed) and the wall-clock duration for a manual one.
    duration_s: float
    #: The load-bearing duration (A4.4), null for a manual session — which has
    #: no recording and therefore no pauses to subtract.
    recording_time_s: float | None
    #: Session RPE, when there is one.
    rpe: float | None


class SessionRead(SessionListItem):
    """One completed session with the recordings behind it."""

    notes: str | None
    end_time: dt.datetime
    #: One per device file behind this session — exactly one today, and the
    #: schema permits N because WP-6 owns the merge case.
    recordings: list[RecordingRead]
    #: Sets logged by hand, in order. Empty for a device session.
    logged_sets: list[LoggedSetRead]
    created_at: dt.datetime
    updated_at: dt.datetime


SessionsPage = Page[SessionListItem]


class SessionUpdate(BaseModel):
    """Corrections to a session's guessed facts.

    Both fields are overrides of something inferred from a file: setting the
    discipline records that the athlete decided it, and setting the timezone
    re-derives ``local_date``, which is what puts a late-evening ride back on
    the right day.

    **Optional by omission, never nullable.** Neither field can be *cleared*:
    a session always has a discipline and always has a timezone, so the
    service refuses an explicit ``null`` with a 422. ``SkipJsonSchema[None]``
    keeps the Python-side ``= None`` "unset" default while dropping the
    ``null`` branch from the contract, so the schema promises exactly what the
    parser accepts — the same rule the optional *query* parameters follow
    (`.claude/rules/api-optional-query-params.md`), applied to a request body.
    Other update payloads here (``AthleteUpdate``, ``WorkoutUpdate``) stay
    ``X | None`` on purpose: for those, ``null`` means "clear this field".
    """

    model_config = ConfigDict(extra="forbid")

    discipline: SessionDiscipline | SkipJsonSchema[None] = None
    # The bound rides on the *string* branch, not on the union. `Field(
    # max_length=...)` beside a `X | SkipJsonSchema[None]` default is applied
    # to the whole union — and `len(None)` is a `TypeError` inside pydantic's
    # validator, which reaches the client as a 500 where the service's
    # "timezone cannot be cleared" 422 belongs. (`X | None` happens to hoist
    # the constraint onto the non-null member; this union does not.)
    timezone: TimezoneName | SkipJsonSchema[None] = Field(
        default=None,
        description=(
            "An IANA name (Europe/Zurich), a fixed offset (UTC+02:00), or UTC. "
            "Anything else is refused: a timezone that cannot be resolved makes "
            "the session's date unrecoverable."
        ),
    )


class LoggedSetCreate(BaseModel):
    """One set of a manually entered session."""

    model_config = ConfigDict(extra="forbid")

    #: A catalogue slug, **or** ``exercise_name`` — exactly one.
    exercise_id: str | None = Field(default=None, max_length=MAX_SLUG_LENGTH)
    exercise_name: PostgresText | None = Field(
        default=None, min_length=1, max_length=MAX_EXERCISE_NAME
    )
    reps: int = Field(ge=1, le=MAX_REPS)
    load_kg: float | None = Field(default=None, ge=0, le=MAX_LOAD_KG)
    rir: int | None = Field(default=None, ge=MIN_RIR, le=MAX_RIR)
    notes: PostgresText | None = Field(default=None, max_length=MAX_NOTES_LENGTH)


class ManualSessionCreate(BaseModel):
    """A session the athlete performed and is typing in (B-6).

    No file, no recording row, no streams — a session row with
    ``recording_kind=manual`` and its sets. Strength by default, because that
    is what has no device file worth ingesting.
    """

    model_config = ConfigDict(extra="forbid")

    #: When it started. Must carry an offset: a naive instant would be read
    #: as local time on whichever machine happened to receive it.
    start_time: AwareDatetime = Field(ge=EARLIEST_SESSION, le=LATEST_SESSION)
    #: The athlete-local timezone at the time, which fixes the session's date.
    timezone: TimezoneName = Field(
        default="UTC",
        description="IANA name, fixed offset (UTC+02:00), or UTC.",
    )
    duration_s: int = Field(ge=MIN_MANUAL_DURATION_S, le=MAX_MANUAL_DURATION_S)
    discipline: SessionDiscipline = SessionDiscipline.STRENGTH
    #: Session RPE on the 0-10 scale.
    rpe: float | None = Field(default=None, ge=MIN_RPE, le=MAX_RPE)
    notes: PostgresText | None = Field(default=None, max_length=MAX_NOTES_LENGTH)
    sets: list[LoggedSetCreate] = Field(default_factory=list, max_length=MAX_SETS)

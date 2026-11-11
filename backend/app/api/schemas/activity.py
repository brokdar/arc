"""Request/response schemas for completed sessions.

Two shapes, and the split is the same one the planned-session list makes:
a **list row** is what a log line renders and costs one query for the
page, while the **detail** carries the recording metadata behind it — the
sources that produced each channel, the stops that were subtracted, and how
many repairs the cleaner made.

**Streams are not here.** ``GET /sessions/{id}`` answers with metadata and the
current metric artefact; the samples live in ``data/streams/`` and
``GET /sessions/{id}/streams`` serves them. A detail response that carried
14 400 rows per channel would be the wrong resource for every page that exists
today — and the metrics, which are a few dozen numbers, would then be
unreachable without paying for them.
"""

import datetime as dt
import uuid
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from app.api.pagination import Page
from app.api.schemas.matching import MatchSummary
from app.api.schemas.metrics import SessionMetricsRead
from app.api.schemas.wellness import WeightInForceRead, WellnessDayRead
from app.api.validation import PostgresText
from app.domain.activity import (
    MAX_TEMPERATURE_C,
    MIN_TEMPERATURE_C,
    ClassificationSource,
    HoldQuality,
    LoggedSetKind,
    PainBand,
    PainKind,
    RecordingKind,
    SessionDiscipline,
    SessionMatchStatus,
    SetConfidence,
    SetSide,
    SetSource,
)
from app.domain.metrics import LoadBasis
from app.persistence.activity import (
    MAX_DONT_LEARN_REASON_LENGTH,
    MAX_NOTES_LENGTH,
    MAX_PLANNED_LINE_REF_LENGTH,
    MAX_TIMEZONE_LENGTH,
)
from app.persistence.exercises import MAX_SLUG_LENGTH
from app.services.activity import (
    EARLIEST_SESSION,
    LATEST_SESSION,
    MAX_EXERCISE_NAME,
    MAX_HOLD_S,
    MAX_LOAD_KG,
    MAX_MANUAL_DURATION_S,
    MAX_REPS,
    MAX_REST_S,
    MAX_RIR,
    MAX_RPE,
    MAX_SESSION_RATING,
    MAX_SET_RPE,
    MAX_SETS,
    MAX_UTC_OFFSET_MIN,
    MIN_MANUAL_DURATION_S,
    MIN_RIR,
    MIN_RPE,
    MIN_SESSION_RATING,
    MIN_SET_RPE,
    MIN_UTC_OFFSET_MIN,
    SET_RPE_STEP,
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
    #: Repetitions performed, or null for a timed hold. Exactly one of ``reps``
    #: and ``duration_s`` is set.
    reps: int | None
    #: Seconds held, for a timed set.
    duration_s: int | None
    #: Whether the row holds both sides of a one-side movement in one line
    #: ("8 each side") — one set, its reps counted twice in volume.
    per_side: bool
    #: External load. For a per-side row, the load on **that side**.
    load_kg: float | None
    #: Reps in reserve, as reported after the set — never the target. Null
    #: is "not rated".
    rir: int | None
    notes: str | None
    #: The set's own identity (uuid7), minted on the phone or by the server.
    #: Unique within its session.
    client_id: uuid.UUID
    set_kind: LoggedSetKind
    side: SetSide
    #: The prescription line this set answered, when it answered one.
    planned_line_ref: str | None
    #: The catalogue movement this set was swapped in for.
    replaced_exercise_id: str | None
    source: SetSource
    confidence: SetConfidence
    #: True for "4+ reps left": ``rir`` is then 4 and a floor, not a count.
    rir_capped: bool
    #: RPE as reported, 6-10 in half steps.
    rpe: float | None
    #: The rating of a timed hold.
    hold_quality: HoldQuality | None
    #: Rest before this set, seconds.
    rest_s: int | None
    #: When the set was logged, UTC, and the device's UTC offset then.
    completed_at: dt.datetime | None
    utc_offset_min: int | None
    pain_kind: PainKind | None
    pain_band: PainBand | None


class SessionListItem(BaseModel):
    """One completed session, as a row in the log."""

    id: uuid.UUID
    #: The athlete-local day it belongs to. A midnight-crosser belongs to the
    #: day it began.
    local_date: dt.date
    start_time: dt.datetime
    #: False for a manual session that was dated but never timed: its
    #: ``start_time`` is then the local midnight of ``local_date``, an anchor
    #: to order by and not a time to show.
    start_time_known: bool
    #: IANA name, ``UTC±HH:MM``, or ``UTC``. Athlete-overridable.
    timezone: str
    discipline: SessionDiscipline
    classification_source: ClassificationSource
    #: Whether the athlete corrected the discipline.
    discipline_overridden: bool
    recording_kind: RecordingKind
    #: Where this session stands relative to the plan (WP-6). ``unmatched``
    #: means undecided — including while a proposal is waiting on the athlete
    #: — and ``unplanned`` means decided: there was nothing this could be.
    status: SessionMatchStatus
    #: The link to a planned session, when one stands. A `pending` link is a
    #: proposal the athlete has not answered, which is why it can be present
    #: while ``status`` is still ``unmatched``.
    match: MatchSummary | None = None
    #: What to render as the session's length: the recording time for a device
    #: session (pauses removed) and the wall-clock duration for a manual one.
    duration_s: float
    #: The load-bearing duration (A4.4), null for a manual session — which has
    #: no recording and therefore no pauses to subtract.
    recording_time_s: float | None
    #: Session RPE, when there is one.
    rpe: float | None
    #: Ambient temperature during the session, °C, athlete-reported (#23).
    #: The conditions the measurement was taken under, not a device channel —
    #: null means nobody said, never zero degrees.
    temperature_c: float | None
    #: The session's training load, from its current metric artefact. Null
    #: when nothing has been computed yet **and** when neither load model
    #: could be — the row keeps the slot either way and the detail endpoint
    #: carries the reason (A5.2).
    load: float | None
    #: Which model produced ``load``. Null exactly when ``load`` is.
    load_basis: LoadBasis | None
    #: How far the session went, from its current metric artefact. Null when
    #: nothing has been computed, when the recording carried no speed channel,
    #: and for a session with no recording at all — the row holds the slot in
    #: every one of those cases rather than printing a zero.
    distance_km: float | None = None


class SessionRead(SessionListItem):
    """One completed session with the recordings behind it."""

    notes: str | None
    end_time: dt.datetime
    #: One per device file behind this session — exactly one today, and the
    #: schema permits N because WP-6 owns the merge case.
    recordings: list[RecordingRead]
    #: Sets logged by hand, in order. Empty for a device session.
    logged_sets: list[LoggedSetRead]
    #: "Don't learn from today": kept in history, out of progression.
    dont_learn: bool
    #: The athlete's private reason for ``dont_learn``.
    dont_learn_reason: str | None
    #: "How hard was the whole session?" 1-10, or null when skipped.
    session_rating: int | None
    #: The metric version in force, or null when nothing has been computed —
    #: which is a state the page has an action for, not an error.
    metrics: SessionMetricsRead | None
    #: The body weight governing this session's `local_date`, so watts per
    #: kilogram is derivable without a second call. Null before the first
    #: weight was recorded — and w/kg is then **absent**, not computed against
    #: a default nobody has.
    weight_kg_in_force: WeightInForceRead | None
    #: What the athlete reported on this session's own day. The question worth
    #: answering is not "was HRV low on the 14th" but "does poor sleep predict
    #: poor execution *for this athlete*", and after this the two halves are
    #: joined rather than left to a manual date match per session, forever.
    #: Null when nothing was recorded that day — the session still reads.
    wellness: WellnessDayRead | None
    created_at: dt.datetime
    updated_at: dt.datetime


SessionsPage = Page[SessionListItem]


class SessionUpdate(BaseModel):
    """Corrections to a session's guessed facts, and its measurement context.

    Two kinds of field, with different null semantics — and in both cases an
    **omitted** field is left untouched (the route dumps with
    ``exclude_unset``):

    * **Corrections** (``discipline``, ``timezone``) override something
      inferred from a file: setting the discipline records that the athlete
      decided it, and setting the timezone re-derives ``local_date``, which
      is what puts a late-evening ride back on the right day. Neither can be
      *cleared* — a session always has both — so the service refuses an
      explicit ``null`` with a 422, and ``SkipJsonSchema[None]`` keeps the
      Python-side ``= None`` "unset" default while dropping the ``null``
      branch from the contract, so the schema promises exactly what the
      parser accepts. In a body, omitting a field means "leave it alone" and
      ``null`` would mean "clear it" — which is not an operation these two have.
    * **Measurement context** (``rpe``, ``temperature_c``, #23) is what the
      athlete reports about the conditions of a session — including an
      ingested one, which is the point: a device file never carries an RPE.
      These are ``X | None`` on purpose, like ``AthleteUpdate``'s fields:
      an explicit ``null`` *clears* the value, because "nobody said" is
      their honest empty state.
    """

    model_config = ConfigDict(extra="forbid")

    #: Session RPE on the athlete's 0-10 scale. Their own report — never
    #: derived from power or heart rate, which are measured channels.
    rpe: float | None = Field(default=None, ge=MIN_RPE, le=MAX_RPE)
    #: Ambient temperature during the session, °C, athlete-reported.
    temperature_c: float | None = Field(
        default=None, ge=MIN_TEMPERATURE_C, le=MAX_TEMPERATURE_C
    )
    #: Free text about the session — athlete's words or the coach's line on
    #: their behalf. Context, not measurement, so it is correctable (#51); the
    #: text replaced stays in the audit row. ``null`` or blank clears it.
    notes: PostgresText | None = Field(default=None, max_length=MAX_NOTES_LENGTH)
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


class LoggedSetFields(BaseModel):
    """What one logged set says about itself — everything but its identity.

    Shared by the two doors a set comes in through: `LoggedSetCreate`, which
    adds the optional ``client_id``, and the outbox's `SyncSet`, whose set is
    named by its mutation's ``set_client_id`` instead.

    ``reps`` and ``duration_s`` are both optional *here* and exactly one of
    them is required by the service: the exactly-one-of rule cannot be stated
    in a JSON schema without an ``oneOf`` the generated client would render as
    two unrelated shapes, and the #17 lesson says a bound that lives only in
    the API schema is a bound a dry run passes and the write then fails. One
    rule, one message, one place.
    """

    model_config = ConfigDict(extra="forbid")

    #: A catalogue slug, **or** ``exercise_name`` — exactly one.
    exercise_id: str | None = Field(default=None, max_length=MAX_SLUG_LENGTH)
    exercise_name: PostgresText | None = Field(
        default=None, min_length=1, max_length=MAX_EXERCISE_NAME
    )
    #: Repetitions performed. Exactly one of ``reps`` and ``duration_s``.
    reps: int | None = Field(default=None, ge=1, le=MAX_REPS)
    #: Seconds held, for a timed set.
    duration_s: int | None = Field(default=None, ge=1, le=MAX_HOLD_S)
    #: Whether the row holds both sides of a one-side movement in one line
    #: ("8 each side"): one set, its reps counted twice in volume. Refused on
    #: a movement the catalogue marks bilateral, and together with a single
    #: ``side`` — a row is one side or a pair, not both.
    per_side: bool = False
    load_kg: float | None = Field(default=None, ge=0, le=MAX_LOAD_KG)
    #: Reps in reserve as reported — never the planned target. Omit when the
    #: set was not rated. "4+" is ``rir: 4, rir_capped: true``.
    rir: int | None = Field(default=None, ge=MIN_RIR, le=MAX_RIR)
    notes: PostgresText | None = Field(default=None, max_length=MAX_NOTES_LENGTH)
    set_kind: LoggedSetKind = LoggedSetKind.WORKING
    #: A single side is refused on a movement the catalogue marks bilateral.
    side: SetSide = SetSide.BOTH
    planned_line_ref: PostgresText | None = Field(
        default=None, min_length=1, max_length=MAX_PLANNED_LINE_REF_LENGTH
    )
    replaced_exercise_id: str | None = Field(default=None, max_length=MAX_SLUG_LENGTH)
    #: Omitted, a typed-in set is a ``recap``. ``coach`` is the agent's alone.
    source: SetSource | None = None
    confidence: SetConfidence = SetConfidence.HIGH
    #: "4+ reps left" — needs ``rir: 4`` (a service rule, for the reason the
    #: class docstring gives).
    rir_capped: bool = False
    #: With ``rir`` it must agree: ``rir == floor(10 - rpe)``.
    rpe: float | None = Field(
        default=None, ge=MIN_SET_RPE, le=MAX_SET_RPE, multiple_of=SET_RPE_STEP
    )
    #: Only on a timed hold.
    hold_quality: HoldQuality | None = None
    rest_s: int | None = Field(default=None, ge=0, le=MAX_REST_S)
    completed_at: (
        Annotated[AwareDatetime, Field(ge=EARLIEST_SESSION, le=LATEST_SESSION)] | None
    ) = None
    utc_offset_min: int | None = Field(
        default=None, ge=MIN_UTC_OFFSET_MIN, le=MAX_UTC_OFFSET_MIN
    )
    pain_kind: PainKind | None = None
    #: Needs ``pain_kind``.
    pain_band: PainBand | None = None


class LoggedSetCreate(LoggedSetFields):
    """One set of a manually entered session."""

    #: The phone-minted uuid7. A set uploaded twice with the same id is stored
    #: once; omitted, the server mints one.
    client_id: uuid.UUID | None = None


class ManualSessionCreate(BaseModel):
    """A session the athlete performed and is typing in (B-6).

    No file, no recording row, no streams — a session row with
    ``recording_kind=manual`` and its sets. Strength by default, because that
    is what has no device file worth ingesting.
    """

    model_config = ConfigDict(extra="forbid")

    #: When it started. Must carry an offset: a naive instant would be read
    #: as local time on whichever machine happened to receive it. Give this
    #: **or** ``local_date``, exactly one — the service enforces it, for the
    #: reason `LoggedSetCreate` gives. The bound rides on the datetime branch
    #: for the reason `SessionUpdate.timezone` spells out.
    start_time: (
        Annotated[AwareDatetime, Field(ge=EARLIEST_SESSION, le=LATEST_SESSION)]
        | SkipJsonSchema[None]
    ) = None
    #: The day, when the athlete reported no time ("Monday evening"). Stored
    #: at that day's local midnight and flagged `start_time_known=false`.
    local_date: dt.date | SkipJsonSchema[None] = None
    #: The athlete-local timezone at the time, which fixes the session's date.
    #: Omitted, the service fills in the athlete's configured zone
    #: (`MATCHING__TIMEZONE`) — it used to default to ``"UTC"`` here, which
    #: silently mis-derived `local_date` by a day for every athlete who does
    #: not live in Greenwich (issue #62). `None` is not in the published
    #: schema (`SkipJsonSchema`): "omit it" is the contract, not "send null".
    #: The bound rides on the string branch for the reason `SessionUpdate`
    #: spells out above.
    timezone: TimezoneName | SkipJsonSchema[None] = Field(
        default=None,
        description=(
            "IANA name, fixed offset (UTC+02:00), or UTC. Omit for the "
            "athlete's own configured timezone."
        ),
    )
    duration_s: int = Field(ge=MIN_MANUAL_DURATION_S, le=MAX_MANUAL_DURATION_S)
    discipline: SessionDiscipline = SessionDiscipline.STRENGTH
    #: Session RPE on the 0-10 scale.
    rpe: float | None = Field(default=None, ge=MIN_RPE, le=MAX_RPE)
    #: Ambient temperature during the session, °C, athlete-reported (#23).
    temperature_c: float | None = Field(
        default=None, ge=MIN_TEMPERATURE_C, le=MAX_TEMPERATURE_C
    )
    notes: PostgresText | None = Field(default=None, max_length=MAX_NOTES_LENGTH)
    sets: list[LoggedSetCreate] = Field(default_factory=list, max_length=MAX_SETS)
    #: "Don't learn from today": keep it in history, out of progression.
    dont_learn: bool = False
    #: The private reason for ``dont_learn``; refused without it.
    dont_learn_reason: PostgresText | None = Field(
        default=None, max_length=MAX_DONT_LEARN_REASON_LENGTH
    )
    #: "How hard was the whole session?" 1-10. Omit when skipped.
    session_rating: int | None = Field(
        default=None, ge=MIN_SESSION_RATING, le=MAX_SESSION_RATING
    )

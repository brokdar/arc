"""Request/response schemas for the daily wellness series.

Two things in here are worth reading before adding a field.

**Nullability says what a client may say.** In a PATCH body the three states
are *omitted* ("leave it alone"), *present* ("set it to this") and *null*
("clear this") — and on this resource clearing is a real operation, because
over eighteen fields a typo'd HRV the athlete cannot retract is a permanent lie
in a baseline. So every value field here is genuinely `X | None` — unlike the
optional *query* parameters in `app.api.routes.wellness`, which are optional by
omission and use ``SkipJsonSchema[None]``
(`.claude/rules/api-nullability.md`).

**Bounds are the domain's.** Every numeric field references
`app.domain.wellness.BOUNDS`, so the schema's 422 and the domain's refusal
cannot disagree about what is plausible — and the MCP tool, which does not pass
through this schema at all, meets the same limit (the #17 lesson).
"""

import datetime as dt
import uuid
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import ValidationError as PydanticValidationError

from app.api.pagination import Page
from app.api.validation import PostgresText
from app.domain.wellness import (
    BOUNDS,
    MAX_BACKFILL_DAYS,
    MAX_NOTE_CHARS,
    SUBJECTIVE_SCALES,
    BodyRegion,
    Confounder,
    HrvContext,
    HrvMetric,
    InputTier,
    Polarity,
    WellnessProvenance,
    WellnessSource,
)

# Every bound below is read from the domain rather than typed in again: the
# schema's 422 and the domain's refusal have to name the same number, and a
# duplicated literal is how they stop doing so.
SleepDurationS = Annotated[
    int,
    Field(ge=int(BOUNDS["sleep_duration_s"][0]), le=int(BOUNDS["sleep_duration_s"][1])),
]
RestingHrBpm = Annotated[
    int, Field(ge=int(BOUNDS["resting_hr_bpm"][0]), le=int(BOUNDS["resting_hr_bpm"][1]))
]
HrvMs = Annotated[float, Field(ge=BOUNDS["hrv_ms"][0], le=BOUNDS["hrv_ms"][1])]
RespiratoryRate = Annotated[
    float,
    Field(ge=BOUNDS["respiratory_rate_brpm"][0], le=BOUNDS["respiratory_rate_brpm"][1]),
]
Spo2 = Annotated[float, Field(ge=BOUNDS["spo2"][0], le=BOUNDS["spo2"][1])]
WristTemperatureDeltaC = Annotated[
    float,
    Field(
        ge=BOUNDS["wrist_temperature_delta_c"][0],
        le=BOUNDS["wrist_temperature_delta_c"][1],
    ),
]
WeightKg = Annotated[float, Field(ge=BOUNDS["weight_kg"][0], le=BOUNDS["weight_kg"][1])]
Note = Annotated[PostgresText, Field(max_length=MAX_NOTE_CHARS)]

#: A wall-clock time with **no date and no offset** — `23:15` or `06:45:00`.
#:
#: A string with a pattern rather than a `dt.time`, and that is not fussiness:
#: OpenAPI's `format: time` is RFC 3339 `full-time`, which *requires* an
#: offset, so a naive clock reading serialized into it is a value the published
#: contract says is invalid (found by Schemathesis). These readings are naive
#: by design — the date is the day's and the zone is the athlete's, and storing
#: either again would create a second answer to which day the night belongs to
#: — so the honest contract is the one that says "clock time" and refuses an
#: offset rather than silently dropping it.
ClockTime = Annotated[
    str,
    Field(
        pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9](:[0-5][0-9])?$",
        examples=["23:15", "06:45:00"],
    ),
]

#: The four wellness ratings and sleep quality all sit on the same declared
#: 1-5 scale, so one alias carries all of them — and it is bounded by the scale
#: table rather than by a literal, for the reason the numeric bounds are.
_SORENESS = SUBJECTIVE_SCALES["soreness"]
Rating = Annotated[int, Field(ge=_SORENESS.low, le=_SORENESS.high)]


class WellnessDayWrite(BaseModel):
    """The fields a wellness write may set, on any past or present date.

    Every field is omittable and every field is nullable, and the two mean
    different things: **omitted leaves the stored value alone, `null` clears
    it.** A day is corrigible, so retracting a wrong reading has to be
    expressible; a day is also written a field at a time over a morning, so a
    partial payload must not wipe what came before it.

    Clearing ``hrv_ms`` clears ``hrv_metric`` and ``hrv_context`` with it: they
    describe a reading, and describing one that is no longer there is a claim
    about nothing.
    """

    # `extra="forbid"` so a typo'd field name is a 422 rather than a silent
    # no-op: with one athlete and no undo, a lost morning is expensive.
    model_config = ConfigDict(extra="forbid")

    #: Seconds. Hours are an edge rendering — the surface stores seconds.
    sleep_duration_s: SleepDurationS | None = None
    #: Clock time, no date and no offset: the date is the day's and the zone is
    #: the athlete's (see the module docstring of `app.domain.wellness`).
    sleep_start_local: ClockTime | None = None
    sleep_end_local: ClockTime | None = None
    sleep_quality: Rating | None = None
    resting_hr_bpm: RestingHrBpm | None = None
    #: Milliseconds. Requires ``hrv_metric`` and ``hrv_context``.
    hrv_ms: HrvMs | None = None
    #: Which statistic: RMSSD and SDNN are not on one scale.
    hrv_metric: HrvMetric | None = None
    #: How it was measured: a sleeping mean and a daytime spot sample are not
    #: one distribution, and baselines are computed within one context.
    hrv_context: HrvContext | None = None
    respiratory_rate_brpm: RespiratoryRate | None = None
    #: A **fraction**: 0.97, never 97.
    spo2: Spo2 | None = None
    #: Deviation from the device's own baseline, in °C.
    wrist_temperature_delta_c: WristTemperatureDeltaC | None = None
    weight_kg: WeightKg | None = None
    fatigue: Rating | None = None
    soreness: Rating | None = None
    stress: Rating | None = None
    motivation: Rating | None = None
    #: Per-region soreness on the same 1-5 scale, keyed by body region.
    soreness_by_region: dict[BodyRegion, Rating] | None = None
    #: What was true about the night. Some of these void the morning's
    #: objective markers as evidence — see ``markers`` on the read.
    confounders: list[Confounder] | None = None
    #: Free text. Never parsed, and never a substitute for a confounder tag.
    note: Note | None = None


class MarkerStandingRead(BaseModel):
    """Whether this day's objective markers may be acted on, and why not.

    On the **same object as the readings**, deliberately: a coach that has to
    remember to look somewhere else for last night's beer will one day not
    remember, and the cost is a training week. The numbers themselves are
    always returned — they are real, and they matter to the history. What is
    withheld is their standing as evidence today.
    """

    actionable: bool
    #: The declared confounders that voided the morning.
    invalidated_by: list[Confounder]
    #: One line, ready to read: `recorded` or
    #: `recorded, not actionable: alcohol`.
    statement: str


class WellnessDayRead(BaseModel):
    """One recorded day as the API returns it.

    A null value means **not provided**, never zero. A day that was never
    recorded at all is a 404 on the dated route and simply absent from a range
    — it is never synthesized as a day of nulls, because "reported nothing" and
    "was not asked" must not render as the same thing.
    """

    id: uuid.UUID
    #: The day the readings describe. For an overnight reading that is the
    #: **wake** day — deliberately unlike a session, which belongs to the day
    #: it started.
    local_date: dt.date

    sleep_duration_s: int | None
    #: `HH:MM:SS`, no offset — see :data:`ClockTime`.
    sleep_start_local: str | None
    sleep_end_local: str | None
    sleep_quality: int | None
    resting_hr_bpm: int | None
    hrv_ms: float | None
    hrv_metric: HrvMetric | None
    hrv_context: HrvContext | None
    respiratory_rate_brpm: float | None
    spo2: float | None
    wrist_temperature_delta_c: float | None
    weight_kg: float | None
    fatigue: int | None
    soreness: int | None
    stress: int | None
    motivation: int | None
    soreness_by_region: dict[BodyRegion, int]
    confounders: list[Confounder]
    note: str | None

    #: The confounder pre-check, applied rather than merely stored.
    markers: MarkerStandingRead
    #: True when the day was entered late enough for recall to be a problem.
    #: It marks the **subjective** ratings only: the watch measured the
    #: objective ones on the day, and the date they were typed in says nothing
    #: about them.
    subjective_recalled: bool

    #: Where the numbers came from. Distinct from ``source``, which is who
    #: wrote them down — the agent records what it was told and never signs as
    #: the athlete.
    provenance: WellnessProvenance
    source: WellnessSource
    created_at: dt.datetime
    updated_at: dt.datetime


class WellnessDaysPage(Page[WellnessDayRead]):
    """A page of the series, with the gaps in it named.

    ``missing`` is why this is not a bare `Page`: a range read returns the days
    that exist, and a consumer that only sees those cannot tell a Tuesday
    nobody answered from a Tuesday outside the page. Reporting the absences is
    cheaper than synthesizing null-filled days that read as answers.
    """

    #: Dates in the requested range with no recorded day, oldest first.
    missing: list[dt.date]


class WeightInForceRead(BaseModel):
    """The body weight governing a date, and the day it was recorded on.

    ``effective_date`` rides along because watts per kilogram computed against
    a three-week-old weight should say so.
    """

    weight_kg: float
    effective_date: dt.date
    #: The date the question was asked about.
    on: dt.date


class ScaleAnchorRead(BaseModel):
    """One point on a declared subjective scale, and what it means."""

    value: int
    label: str


class SubjectiveScaleRead(BaseModel):
    """A declared subjective scale: range, direction and anchor words.

    Served so that neither the UI nor the agent carries a private copy of what
    a 3 means. ``polarity`` is the load-bearing field: 5 motivation is good and
    5 fatigue is not, and a reader that assumes one direction is a bug nothing
    catches, because both are plausible numbers.
    """

    field: str
    low: int
    high: int
    polarity: Polarity
    prompt: str
    anchors: list[ScaleAnchorRead]


class InputTierRead(BaseModel):
    """One writable field and how much a consumer should want it."""

    field: str
    tier: InputTier


class ConfounderRead(BaseModel):
    """One confounder tag, and whether it voids the morning's markers."""

    value: Confounder
    #: True for the five the athlete's own pre-check treats as making a
    #: morning's objective numbers unusable. The rest are context.
    invalidates_markers: bool


class WellnessInputsRead(BaseModel):
    """The self-describing vocabulary of the wellness surface.

    One read that answers every "what may I send" question: the tiers, the
    scales with their descriptors and polarity, the confounder vocabulary with
    its invalidating half marked, and the body regions. It exists so that no
    consumer ever discovers the vocabulary by submitting guesses and reading
    the refusals.
    """

    tiers: list[InputTierRead]
    scales: list[SubjectiveScaleRead]
    confounders: list[ConfounderRead]
    body_regions: list[BodyRegion]
    #: Most days one call to `POST /wellness/backfill` may carry.
    max_backfill_days: int = MAX_BACKFILL_DAYS


class BackfillDay(WellnessDayWrite):
    """One dated day inside a batch write."""

    #: The day these readings describe. Any past or present date; a future one
    #: is refused by name.
    local_date: dt.date

    @model_validator(mode="before")
    @classmethod
    def _refusals_name_the_date(cls, data: Any) -> Any:
        """Re-raise a per-field refusal with the **date** in front of it.

        Pydantic reports a bad field in a list as ``["body", "days", 6,
        "spo2"]``, and an ordinal position in a three-hundred-day migration is
        barely more actionable than "validation failed" — the #19 lesson is
        that an error the caller cannot act on costs a round trip. So the day's
        own fields are validated here first, and anything they refuse is
        re-raised with the date it belongs to.

        The double validation costs one extra parse per day and buys an error
        message someone can fix.
        """
        if not isinstance(data, dict) or "local_date" not in data:
            return data
        fields = {name: value for name, value in data.items() if name != "local_date"}
        try:
            WellnessDayWrite.model_validate(fields)
        except PydanticValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                for error in exc.errors()
            )
            raise ValueError(f"{data['local_date']}: {problems}") from exc
        return data


class WellnessBackfill(BaseModel):
    """Many days in one transaction — the migration shape.

    A batch either lands whole or not at all: a partial migration leaves the
    athlete unable to tell which days made it, and the retry then has to reason
    about overlap.
    """

    model_config = ConfigDict(extra="forbid")

    days: Annotated[
        list[BackfillDay], Field(min_length=1, max_length=MAX_BACKFILL_DAYS)
    ]
    #: Report exactly the per-day outcomes a real call would produce, writing
    #: nothing. A migration is exactly when you want to see the answer before
    #: committing to it.
    dry_run: bool = False


class BackfillDayResult(BaseModel):
    """What one day of a batch was, or would have been."""

    local_date: dt.date
    #: `created` or `updated` — a batch mixing new and existing days says which
    #: each was, so a re-run is legible.
    outcome: str
    #: The fields this write changed, before and after. Empty when the day was
    #: already exactly this.
    changed: dict[str, Any]


class WellnessBackfillResult(BaseModel):
    """The outcome of a batch write, per day."""

    dry_run: bool
    #: One count per outcome — `created`, `updated`, `retracted` — so a re-run
    #: of an import is legible without reading every day object.
    outcomes: dict[str, int]
    days: list[BackfillDayResult]

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
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator
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
    WellnessPromptStatus,
    WellnessProvenance,
    WellnessSource,
)
from app.domain.wellness_baseline import (
    Direction,
    JointStateKey,
    Space,
    WellnessMetric,
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


# --- the trend read -----------------------------------------------------------
#
# Three baseline shapes rather than one nullable model, and that is the whole
# design: `AC-9` requires an immature baseline to carry **no** `mean`, `band`
# or `deviation_sd` key at all, and a single model with optional fields serves
# them as `null`. A null mean is a number somebody eventually reads as zero,
# and a caveat beside a number is advice a model under pressure to be helpful
# drops. So the discriminator `kind` picks between an abstention, a
# trend-only baseline (body weight, the subjective ratings) and a banded one.


class CountRead(BaseModel):
    """A count against the bar it has to clear."""

    have: int
    need: int
    #: The same pair as one string — `11 of 14` — so a reader that renders the
    #: object without arithmetic still says the actionable thing.
    statement: str


class BandRead(BaseModel):
    """The athlete's own normal range: the smallest worthwhile change.

    `low`/`high` are in the metric's analysis space (`ln` for HRV);
    `low_native`/`high_native` are the same edges in the metric's own unit,
    which is what a chart draws behind the series.
    """

    low: float
    high: float
    half_width: float
    low_native: float
    high_native: float


class SlopeRead(BaseModel):
    """A least-squares trend through the baseline window."""

    per_day: float
    per_week: float
    n: int


class RollingMeanRead(BaseModel):
    """The trailing seven-day mean, with the `n` it was computed over.

    `mean` is null exactly when `n` is zero — that is a genuine absence, not a
    withheld number, and the two are told apart by the `n` beside it.
    """

    mean: float | None
    mean_native: float | None
    n: int


class BaselineAbstentionRead(BaseModel):
    """No baseline yet, and exactly what it would take to have one.

    Carries no `mean`, no `band` and no `deviation_sd` **key**. What it does
    carry is both counts and its own unlock condition, so "not enough data" can
    be acted on rather than merely regretted.
    """

    kind: Literal["abstention"] = "abstention"
    mature: Literal[False] = False
    metric: WellnessMetric
    #: Named when this abstention is about one HRV context.
    hrv_context: HrvContext | None
    readings: CountRead
    span_days: CountRead
    reason: str


class TrendBaselineRead(BaseModel):
    """A mature baseline with no band: body weight and the subjective ratings.

    Weight moves on a scale of weeks, so a daily SD deviation from it is a
    statement nobody should make; a 1-5 rating has five ordinal points, where
    an SD is arithmetic dressed as precision. Both get a mean and a trend, and
    neither gets a `band` or a `deviation_sd` key.
    """

    kind: Literal["trend"] = "trend"
    mature: Literal[True] = True
    metric: WellnessMetric
    hrv_context: HrvContext | None
    #: `linear` everywhere but HRV, whose statistics live in `ln`.
    space: Space
    unit: str
    #: Readings **after** exclusions — a confounder-voided day and a recalled
    #: rating are not in it, which is what makes a thin `n` a visible reason.
    n: int
    span_days: int
    mean: float
    mean_native: float
    sd: float
    #: `sd / mean`. Null when the mean is zero, where it is undefined.
    cv: float | None
    trend: SlopeRead


class BandedBaselineRead(TrendBaselineRead):
    """A mature baseline with a normal band and today's distance from it."""

    kind: Literal["banded"] = "banded"  # pyright: ignore[reportIncompatibleVariableOverride]
    band: BandRead
    #: The **seven-day mean's** distance from the baseline in SDs — never today
    #: against yesterday. Null when nothing was recorded in the seven-day
    #: window, and in the degenerate case of a zero SD, where a distance in SD
    #: units is undefined rather than infinite.
    deviation_sd: float | None
    direction: Direction | None


#: The three shapes a baseline may take, discriminated by `kind`.
BaselineRead = Annotated[
    BandedBaselineRead | TrendBaselineRead | BaselineAbstentionRead,
    Field(discriminator="kind"),
]


class TrendPointRead(BaseModel):
    """One date of the requested range: a reading, or an explicit gap.

    `value` is null on a date with no reading — never zero and never
    interpolated from its neighbours. `markers` rides on the same object,
    because a confounder standing the caller has to fetch separately is a
    confounder standing the caller will one day not fetch.
    """

    local_date: dt.date
    value: float | None
    #: Null on a date with no day recorded; there is then no standing to state.
    markers: MarkerStandingRead | None


class MetricTrendRead(BaseModel):
    """One metric over the requested range: readings, mean and baseline."""

    metric: WellnessMetric
    unit: str
    space: Space
    #: One entry per date in the range, oldest first, gaps included.
    series: list[TrendPointRead]
    #: The reading on `as_of`, in the metric's **native** unit, or null.
    #: Distinct from `rolling_mean_7d` on purpose: conflating them is how one
    #: bad night becomes a trend.
    today: float | None
    rolling_mean_7d: RollingMeanRead
    baseline: BaselineRead
    #: HRV only: one baseline per context that has readings. A context with no
    #: readings is simply absent, and two contexts are never pooled — a mean
    #: over an overnight average and a daytime spot sample belongs to neither.
    by_context: dict[HrvContext, BaselineRead] = Field(default_factory=dict)


class OutsideMarkerRead(BaseModel):
    """One marker sitting outside its own band, named and directed."""

    metric: WellnessMetric
    direction: Direction
    deviation_sd: float


class MarkersOutsideBandRead(BaseModel):
    """How many markers are outside their band, of how many that could say.

    The denominator excludes markers whose baseline is immature and **says
    so** — `2 of 4`, not `2 of 5` — because a denominator that silently counts
    markers with no baseline makes two of five look calmer than it is.
    """

    count: int
    of: int
    statement: str
    markers: list[OutsideMarkerRead]


class JointStateRead(BaseModel):
    """The HRV x resting-HR quadrant, as a plain label with no verdict."""

    key: JointStateKey
    label: str
    hrv_deviation_sd: float
    resting_hr_deviation_sd: float


class ReadinessDict(TypedDict):
    """The serialized shape of :class:`ReadinessRead`, and its contract.

    A `TypedDict` with a `NotRequired` member is the one way to say "this key
    is **absent**, not null" in both pydantic and the published schema: it is
    handed to `model_serializer` as its `return_type`, so the OpenAPI document
    describes `joint_state` as an optional, non-nullable property rather than
    the untyped object a bare `dict[str, Any]` return would collapse to.
    """

    as_of: dt.date
    markers_outside_band: MarkersOutsideBandRead
    joint_state: NotRequired[JointStateRead]


class ReadinessRead(BaseModel):
    """What the markers say about today. No score, no recommendation.

    `test_readiness_field_inventory` pins this key set closed and fails if a
    key named `readiness_score`, `recommendation`, `verdict` or `score` ever
    appears at any depth.
    """

    as_of: dt.date
    markers_outside_band: MarkersOutsideBandRead
    #: **Absent**, not null, when either half of the pair is missing or
    #: immature. See :meth:`_omit_an_undrawable_quadrant`.
    joint_state: JointStateRead | None = None

    @model_serializer(mode="plain", return_type=ReadinessDict)
    def _omit_an_undrawable_quadrant(self) -> ReadinessDict:
        """Drop `joint_state` entirely when there is no quadrant to name.

        A null here would be a guess wearing a key: a reader that sees the
        field at all learns that a quadrant is a thing this object reports, and
        the next reader fills it in from one of the two markers. The quadrant
        exists precisely because neither marker means anything alone, so when
        one of them cannot speak the honest answer is silence.

        A serializer rather than `response_model_exclude_none`, which would
        also drop the genuine nulls this response depends on — a gap in a
        series is a null that has to survive.
        """
        data: ReadinessDict = {
            "as_of": self.as_of,
            "markers_outside_band": self.markers_outside_band,
        }
        if self.joint_state is not None:
            data["joint_state"] = self.joint_state
        return data


class WellnessTrendRead(BaseModel):
    """The dated readings, rolling means and baselines over a range."""

    start: dt.date
    end: dt.date
    #: The day the baselines and rolling means are anchored to: the last day of
    #: the range, so a historical range answers as it stood then.
    as_of: dt.date
    metrics: dict[WellnessMetric, MetricTrendRead]
    #: Computed over **every** marker, not only the requested ones — a
    #: denominator that depended on the query string would mean something
    #: different on every call.
    readiness: ReadinessRead


class WellnessPromptRead(BaseModel):
    """The standing of one day's question, as the API returns it.

    Read as **`null` for the whole object** when no prompt was ever raised for
    the date — "nobody has been asked yet" is an answer, and a 404 would make
    the Today view treat the ordinary state of a fresh morning as a failure.

    `expires_at` is the deadline stamped when the prompt was raised, not a
    constant re-derived on read: it is a fact about *this* prompt, so the
    sweep, this surface and the Today view cannot disagree about when the day
    closes.
    """

    local_date: dt.date
    status: WellnessPromptStatus
    #: When the day stops being answerable. The window is half-open
    #: `[raised, expires_at)`, so this instant is already outside it.
    expires_at: dt.datetime
    #: When it was answered, or closed unanswered. Null while pending.
    resolved_at: dt.datetime | None
    #: When the question was put to the athlete.
    raised_at: dt.datetime


class WellnessPromptAnswer(BaseModel):
    """What answering the prompt produced: the closed question and the day.

    Both, because they move together — the day is written and the prompt is
    resolved in one transaction — and a client that had to re-read one of them
    could render a moment where only half of it had happened.
    """

    prompt: WellnessPromptRead
    #: The day as stored. **Null** when the answer cleared the day's last
    #: value, which retracts it — the same thing `PATCH /wellness/days/{date}`
    #: returns null for.
    day: WellnessDayRead | None

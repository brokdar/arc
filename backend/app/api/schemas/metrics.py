"""Response schemas for a session's computed metrics and its stored streams.

Two resources, split by size. The **metrics** ride along on
``GET /sessions/{id}`` — a few dozen numbers with their explanations, which is
what the page header renders. The **streams** are their own endpoint because a
four-hour ride is 14 400 rows per channel and 1-2 MB on the wire, and no page
that merely lists sessions should pay for that.

**Every metric is one shape.** `MetricRead` carries a value with its
explanation *or* a `not_assessed` reason, never both and never neither. The UI
branches on that once — `NotAssessed` renders the reason in the slot the value
would have occupied — instead of inventing an empty state per number
(`.claude/rules/frontend-ui-conventions.md` rule 4).

The field names mirror `app.domain.session_analysis.analysis_to_json` exactly,
so the stored payload validates straight into these models. Extra keys are
ignored by default, which is what lets a later work package add a metric
without invalidating every artefact already written.
"""

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.api.validation import PostgresText
from app.domain.anchors import AnchorType, Provenance
from app.domain.metrics import LoadBasis
from app.domain.streams import AnomalyKind, StreamChannel
from app.domain.zones import ZoneModel
from app.persistence.metrics import MAX_REASON_LENGTH


class ExplanationRead(BaseModel):
    """Why a number is the number — data attached to the number, not page copy."""

    #: The arithmetic, written the way a human reads it.
    formula: str
    #: Named quantities that went in, already rendered. An anchor input names
    #: the **version's** value, provenance and effective date.
    inputs: dict[str, str]
    #: What the computation had to assume, in the order it assumed them.
    assumptions: list[str]
    #: Where the method comes from, when it comes from somewhere.
    citation: str | None


class MetricRead(BaseModel):
    """One metric slot: answered, or refused with a reason.

    Exactly one of ``value`` and ``not_assessed`` is non-null.
    ``explanation`` is present exactly when ``value`` is.
    """

    value: float | None = None
    explanation: ExplanationRead | None = None
    #: Why this metric has no value, in the athlete's terms and **naming the
    #: missing input** ("no heart rate was recorded"). Render it in the slot.
    not_assessed: str | None = None


def predates(metric: str) -> MetricRead:
    """The slot an artefact written before ``metric`` existed carries.

    A payload stored by an earlier version of the metric set simply has no key
    for a number added later, and the honest answer is neither a zero nor a
    silently missing field: it is "these numbers predate this one, recompute to
    get it" — which is a `not_assessed` reason like any other, rendered in the
    slot by the same component (`.claude/rules/frontend-ui-conventions.md` rule
    4) and fixed by the button already on the page.
    """
    return MetricRead(
        not_assessed=(
            f"these metrics were computed before {metric} was — recompute this "
            "session to add it"
        )
    )


class PowerMetricsRead(BaseModel):
    """Everything derived from the power channel."""

    normalized_power: MetricRead
    #: Work done **while moving**, over moving time (D194, D196) — the basis a
    #: head unit averages over, summed across the same seconds it divides by,
    #: and *not* the duration the load was computed over. Its explanation
    #: carries all of that; render it.
    average_power: MetricRead
    max_power: MetricRead
    intensity_factor: MetricRead
    #: NP over the mean of the **same recorded rows** (D196), never over the
    #: moving-time average power above: two divisors would let the ratio fall
    #: below 1, which no ride can do.
    variability_index: MetricRead
    work_kj: MetricRead
    work_above_ftp_kj: MetricRead
    #: Display only. Never subtracted from the duration load is computed over.
    coasting_time_s: MetricRead


class HeartRateMetricsRead(BaseModel):
    """Everything derived from the heart-rate channel."""

    average_hr: MetricRead
    max_hr: MetricRead
    hrss: MetricRead
    efficiency_factor: MetricRead


class CadenceMetricsRead(BaseModel):
    """Everything derived from the cadence channel."""

    average_cadence: MetricRead
    max_cadence: MetricRead


class SpeedMetricsRead(BaseModel):
    """Distance and speed, in km and km/h.

    Every field defaults to the "recompute me" reason so that an artefact
    written before these numbers existed still validates and still renders in
    its slot; see :func:`predates`.
    """

    #: Integrated from the speed channel, not from the GPS track.
    distance_km: MetricRead = Field(default_factory=lambda: predates("distance"))
    #: Over moving time, the same basis as average power (D194).
    average_speed_kmh: MetricRead = Field(
        default_factory=lambda: predates("average speed")
    )
    max_speed_kmh: MetricRead = Field(default_factory=lambda: predates("max speed"))


class TemperatureMetricsRead(BaseModel):
    """What the device's own sensor read, in degrees Celsius."""

    average_temp_c: MetricRead = Field(default_factory=lambda: predates("temperature"))
    min_temp_c: MetricRead = Field(default_factory=lambda: predates("temperature"))
    max_temp_c: MetricRead = Field(default_factory=lambda: predates("temperature"))


class ZoneTimeRead(BaseModel):
    """Seconds spent in one band of one zone model."""

    index: int
    name: str
    seconds: float


class TimeInZoneRead(BaseModel):
    """One channel's zone distribution, or the reason it has none."""

    #: Non-null when this channel produced no distribution; every field below
    #: is then null or empty.
    not_assessed: str | None = None
    #: The model the bands came from — pinned, because ``(anchor, model) ->
    #: zones`` is only deterministic while the model is recorded (A5.5).
    zone_model: ZoneModel | None = None
    #: One entry per band, ascending, **including the empty ones**: a zone
    #: with no time in it is a fact about the ride, and dropping it would make
    #: the bar's shape depend on the data.
    zones: list[ZoneTimeRead] = []
    #: Seconds that fell in any band — this channel's coverage, not the ride's
    #: elapsed time.
    total_s: float | None = None
    easy_s: float | None = None
    moderate_s: float | None = None
    hard_s: float | None = None
    #: Treff's PI over the three-zone split, or the reason it is degenerate.
    polarization_index: MetricRead | None = None
    explanation: ExplanationRead | None = None


class TimeInZoneBlockRead(BaseModel):
    """The zone distributions, one per channel that has one."""

    power: TimeInZoneRead
    hr: TimeInZoneRead


class LoadRead(BaseModel):
    """Both load models, the selected one, and the rule that chose it (A5.2).

    Both values are here whichever was selected — that is the whole point.
    The counterfactual sentence ("had power been unavailable, the HR model
    would have given 75") is composed by the client from these two fields.
    """

    not_assessed: str | None = None
    training_load: float | None = None
    load_basis: LoadBasis | None = None
    load_basis_rule: str | None = None
    power_load: float | None = None
    hr_load: float | None = None
    explanation: ExplanationRead | None = None


class StrengthRead(BaseModel):
    """What a strength session moved. Kilograms, **never** a training load."""

    not_assessed: str | None = None
    #: Σ ``reps × kg`` over the sets logged in kilograms. Never add this to
    #: ``training_load`` and never render the two in one column (v2 §5.4).
    volume_load_kg: float | None = None
    sets_completed: int | None = None
    #: Fraction of the logged sets that carried kilograms.
    coverage: float | None = None
    explanation: ExplanationRead | None = None


class IntervalRead(BaseModel):
    """One detected work interval, addressed by row on the 1 Hz grid."""

    start_index: int
    #: One past the last — ``[start, end)``.
    end_index: int
    duration_s: int
    average_power: float | None
    max_power: float | None
    average_hr: float | None


class AnchorPinRead(BaseModel):
    """An anchor version a metric artefact was computed against.

    Resolved rather than left as an id: the header renders `FTP 262 ± 15 ·
    estimated`, and a client that had to fetch four anchor versions to draw
    one line would fetch them on every session it showed.
    """

    anchor_type: AnchorType
    version_id: uuid.UUID
    value: float
    unit: str
    provenance: Provenance
    effective_date: dt.date
    ci_low: float | None
    ci_high: float | None


class SessionMetricsRead(BaseModel):
    """One version of one session's metrics, with what it was computed from."""

    #: 1-based position in the chain. A recompute writes n+1 and supersedes n;
    #: the old version stays readable.
    version: int
    computed_at: dt.datetime
    #: Why this version exists. Null on version 1.
    recompute_reason: str | None
    #: The anchor versions in force when this was computed (D115).
    pins: list[AnchorPinRead]
    power_zone_model: ZoneModel | None
    hr_zone_model: ZoneModel | None

    #: Elapsed minus every stop over 30 s — **the duration training load was
    #: computed over** (A4.4, A5.1).
    recording_time_s: float
    elapsed_time_s: float
    #: Rows of the cleaned speed column at or above 1 km/h, one second each
    #: (D196). The basis every *average* here is taken over (D194) — and the
    #: rows they are summed over — whenever the speed channel covered enough of
    #: the ride to be one; where it did not, this still reports what the column
    #: showed and each average's explanation names the recording time it fell
    #: back to. Never the load's duration term.
    moving_time_s: float
    #: Elapsed minus moving minus the seconds the speed channel had no reading
    #: for, derived server-side so a client never has to pick which durations
    #: to subtract — or mistake a sensor dropout for standing still.
    stopped_time_s: MetricRead = Field(default_factory=lambda: predates("stopped time"))

    power: PowerMetricsRead
    heart_rate: HeartRateMetricsRead
    cadence: CadenceMetricsRead
    speed: SpeedMetricsRead = Field(default_factory=SpeedMetricsRead)
    temperature: TemperatureMetricsRead = Field(default_factory=TemperatureMetricsRead)
    elevation_gain_m: MetricRead
    load: LoadRead
    time_in_zone: TimeInZoneBlockRead
    #: Deterministic from the stream alone, so it is versioned with the
    #: metrics rather than recomputed per consumer (D118).
    intervals: list[IntervalRead]
    strength: StrengthRead


# --- the stream payload -------------------------------------------------------


class StreamChannelRead(BaseModel):
    """One channel's cleaned column, on the 1 Hz grid."""

    channel: StreamChannel
    #: Which sensor produced it (A4.3), when the file said.
    source: str | None
    #: One entry per row, **nulls preserved**: a recording stop is a break in
    #: the trace, not a run of zeros, and a chart that filled them would draw
    #: a ride the athlete did not do.
    values: list[float | None]


class StreamAnomalyRead(BaseModel):
    """One region of one channel the cleaner repaired (A4.2)."""

    channel: StreamChannel
    start_index: int
    end_index: int
    kind: AnomalyKind
    substituted_value: float | None


class SessionStreamsRead(BaseModel):
    """The chart payload: every channel on one index-aligned grid.

    Separate from `SessionRead` because it is 1-2 MB for a long ride. Every
    column has exactly ``length`` entries by construction (A4.1), which is
    what lets a client index them together without checking.

    For a **merged** session (WP-6.5) this is the joined grid: the recordings
    laid end to end from the earliest one's origin, with the gap between them
    left as unrecorded rows and reported in ``recording_stops``. Every index
    here addresses that grid.
    """

    #: The first recording the samples came from, in time order — the only one
    #: for every session the MVP ingests.
    recording_id: uuid.UUID
    #: Every recording joined into this view, in time order. One entry unless
    #: the session was merged.
    recording_ids: list[uuid.UUID]
    #: The grid origin, aware UTC. Row ``i`` covers ``[t0 + i, t0 + i + 1)``.
    t0: dt.datetime
    #: Rows in the grid — the length of every channel's ``values``.
    length: int
    channels: list[StreamChannelRead]
    #: The pauses that were subtracted from recording time, as row ranges.
    recording_stops: list[StreamStopRead]
    #: The cleaner's repairs, so the chart can mark them. `resampled_only`
    #: certificates are **excluded**: nothing was repaired there.
    anomalies: list[StreamAnomalyRead]


class StreamStopRead(BaseModel):
    """One recording pause, as a half-open row range."""

    start_index: int
    end_index: int


class MetricsRecompute(BaseModel):
    """Why a recomputation was asked for. The body is optional.

    The reason lands on the **new** version (`recompute_reason`) and is what a
    later reader sees when two versions of one session's numbers disagree —
    "anchor changed" and "stream re-ingested" are different stories, and
    neither is reconstructible from the numbers themselves.
    """

    model_config = ConfigDict(extra="forbid")

    reason: PostgresText | None = Field(
        default=None,
        max_length=MAX_REASON_LENGTH,
        description="Why the metrics are being recomputed.",
    )

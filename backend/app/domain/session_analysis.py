"""The whole metric set of one session, assembled from the pieces.

`app.domain.metrics` holds the arithmetic, one function per number.
This module is the **composition**: given a recording's cleaned columns, its
A4.4 durations, the anchor versions in force and the athlete's sex, it runs
every metric that the available inputs support and returns one value —
:class:`SessionAnalysis` — with a slot for each metric, filled either with a
number and its explanation or with the reason there is none.

It lives in the domain rather than in `app.ingest` because the composition is
where the rules are: which duration the load is computed over, which channel
feeds which zone model, when the HR model is preferred over the power model.
`app.ingest.analysis` reads the parquet file and resolves the anchors; it does
not decide anything. That split is also what lets the whole metric set be
tested over a synthetic stream with no file, no database and no I/O.

**Absence is never an error.** A ride with no power meter, an athlete with no
resting-HR anchor, a session whose stream is one long stop — each produces an
artefact whose affected slots carry a reason. There is no such thing as a
session that fails to be analysed; that is what makes computing on ingest safe
(a metric failure must never un-ingest a file).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.domain.activity import SessionDiscipline
from app.domain.alignment import WorkInterval, detect_work_intervals
from app.domain.anchors import AnchorType, AnchorVersion, describe_anchor
from app.domain.athlete import Sex
from app.domain.metrics import (
    NP_WINDOW_S,
    TSS_SCALE,
    Assessment,
    AveragingBasis,
    Measured,
    MetricExplanation,
    NotAssessed,
    PerformedSet,
    SelectedLoad,
    StrengthVolume,
    TimeInZone,
    average_power,
    average_speed_kmh,
    averaging_basis,
    channel_average,
    channel_maximum,
    channel_minimum,
    coasting_time_s,
    distance_km,
    efficiency_factor,
    elevation_gain_m,
    hrss,
    intensity_factor,
    max_speed_kmh,
    normalized_power,
    select_training_load,
    stopped_time_s,
    strength_volume,
    time_in_zone,
    training_load,
    value_of,
    variability_index,
    work_above_ftp_kj,
    work_kj,
)
from app.domain.streams import StreamChannel
from app.domain.zones import ZoneModel, zones_for

_COGGAN = "Allen & Coggan, Training and Racing with a Power Meter"

#: The channel each zone model is computed over, so the two enums that both
#: spell ``"power"`` (`app.domain.workout.Channel` and
#: `app.domain.streams.StreamChannel`) are crossed here, once, explicitly —
#: never by string coincidence at a call site.
ZONE_MODEL_CHANNEL: dict[ZoneModel, StreamChannel] = {
    ZoneModel.COGGAN_7: StreamChannel.POWER,
    ZoneModel.LTHR_5: StreamChannel.HR,
}


@dataclass(frozen=True, slots=True)
class SessionInputs:
    """Everything the metric set is computed from. No I/O has touched it.

    Args:
        discipline: What the session was, which is what decides whether the
            power or the heart-rate load model is preferred (A5.2).
        recording_time_s: Elapsed minus every stop over 30 s (A4.4). **The
            duration term in training load** (A5.1) — not moving time, not
            elapsed. Zero for a manual session, which has no recording.
        elapsed_time_s: Last sample minus first.
        moving_time_s: Time at or above `app.domain.streams.MOVING_SPEED_MS`.
            **The divisor of every average here** (D194) — average power,
            average speed — and never a load input. Zero when no speed was
            recorded, which is what makes the averages fall back to
            ``recording_time_s`` and say so.
        columns: Channel -> that channel's cleaned (``_fixed``) column on the
            1 Hz grid. Absent channels are simply absent.
        sex: The athlete's sex; HRSS's coefficient depends on it.
        anchors: Anchor type -> the version in force at computation time.
            Missing entries are what the guards report.
        sets: The logged sets of a strength session, empty for a ride.
    """

    discipline: SessionDiscipline
    recording_time_s: float
    elapsed_time_s: float
    moving_time_s: float
    columns: dict[StreamChannel, tuple[float | None, ...]]
    sex: Sex = Sex.UNSPECIFIED
    anchors: dict[AnchorType, AnchorVersion] | None = None
    sets: Sequence[PerformedSet] = ()

    def column(self, channel: StreamChannel) -> tuple[float | None, ...]:
        """One channel's cleaned column, empty when it was not recorded."""
        return self.columns.get(channel, ())

    def anchor(self, anchor_type: AnchorType) -> AnchorVersion | None:
        """The version of one anchor in force, or ``None``."""
        return (self.anchors or {}).get(anchor_type)


@dataclass(frozen=True, slots=True)
class PowerMetrics:
    """Everything derived from the power column."""

    normalized_power: Assessment
    average_power: Assessment
    max_power: Assessment
    intensity_factor: Assessment
    variability_index: Assessment
    work_kj: Assessment
    work_above_ftp_kj: Assessment
    coasting_time_s: Assessment


@dataclass(frozen=True, slots=True)
class HeartRateMetrics:
    """Everything derived from the heart-rate column."""

    average_hr: Assessment
    max_hr: Assessment
    hrss: Assessment
    efficiency_factor: Assessment


@dataclass(frozen=True, slots=True)
class CadenceMetrics:
    """Everything derived from the cadence column."""

    average_cadence: Assessment
    max_cadence: Assessment


@dataclass(frozen=True, slots=True)
class SpeedMetrics:
    """Everything derived from the speed column, in km/h and km.

    The ride log's basics, and the reason they are a block of their own rather
    than three loose slots: distance, average speed and maximum speed are one
    account of the same channel, and the average is only readable beside the
    basis it was taken over (D194).
    """

    distance_km: Assessment
    average_speed_kmh: Assessment
    max_speed_kmh: Assessment


@dataclass(frozen=True, slots=True)
class TemperatureMetrics:
    """Everything derived from the temperature channel, in degrees Celsius.

    As the head unit's own sensor read it, which is not the air temperature:
    a device on the stem in direct sun reports several degrees above it, and
    one in a jersey pocket reports body heat. Context for why a session felt
    the way it did, never a measurement of the weather — the MVP does not
    fetch one (see the delivery plan's analysis-parity list).

    The range is carried beside the mean because it is the interesting part: a
    dawn start at 4 °C finishing at 24 °C is why the second half was different,
    and an average of 14 °C describes neither half.
    """

    average_temp_c: Assessment
    min_temp_c: Assessment
    max_temp_c: Assessment


@dataclass(frozen=True, slots=True)
class SessionAnalysis:
    """One session's whole metric set, each slot answered or explained.

    This is the payload of the versioned metric artefact. Every field is
    either a number carrying its :class:`~app.domain.metrics.MetricExplanation`
    or a :class:`~app.domain.metrics.NotAssessed` carrying its reason; nothing
    is silently absent, and nothing is zero standing in for absent.
    """

    recording_time_s: float
    elapsed_time_s: float
    moving_time_s: float
    #: Elapsed minus moving — every second at a standstill, whether or not the
    #: device paused for it. Derived here so no client has to subtract two
    #: durations and hope it picked the pair the server meant (D194).
    stopped_time_s: Assessment
    power: PowerMetrics
    heart_rate: HeartRateMetrics
    cadence: CadenceMetrics
    speed: SpeedMetrics
    temperature: TemperatureMetrics
    elevation_gain_m: Assessment
    load: SelectedLoad | NotAssessed
    power_time_in_zone: TimeInZone | NotAssessed
    hr_time_in_zone: TimeInZone | NotAssessed
    intervals: tuple[WorkInterval, ...]
    strength: StrengthVolume | NotAssessed


def _normalized_power(
    power: Sequence[float | None], present: Sequence[float]
) -> Assessment:
    """NP over the recorded rows, concatenated across recording stops (D117)."""
    if not present:
        return NotAssessed("no power was recorded")
    stops = len(power) - len(present)
    assumptions = [
        "rows with no power reading are excluded rather than read as zero",
    ]
    if stops:
        assumptions.append(
            f"the {stops} unrecorded rows are removed and the remainder joined, "
            "so one rolling window may span a recording stop"
        )
    return Measured(
        value=normalized_power(present),
        explanation=MetricExplanation(
            formula=f"NP = mean(rolling_mean_{NP_WINDOW_S}s(P)^4)^(1/4)",
            inputs={"samples": f"{len(present)} power readings at 1 Hz"},
            assumptions=tuple(assumptions),
            citation=_COGGAN,
        ),
    )


def _power_load(
    np_watts: float | None, ftp: AnchorVersion | None, recording_time_s: float
) -> tuple[Assessment, Assessment]:
    """The intensity factor and the power-model training load, as a pair.

    They are computed together because they share every input and because a
    load whose IF was not assessed is not a load: ``TSS = duration × IF² /
    36``, so the two either both exist or neither does.

    **The duration term stays recording time** — D194 moved the *averages* onto
    moving time and deliberately stopped there. Two reasons, both about staying
    correct against the definition in the docstrings' citation. Coggan's TSS is
    the whole session's cost, and freewheeling down a descent or rolling to a
    stop at a light is time the body spends recovering *inside* the session; A5.1
    already measured what removing such time does — subtracting coasting alone
    put this system's TSS 7 % under the reference platform's. And IF comes from
    NP, which is a rolling window over the recorded series (D117): the pair
    ``(NP, duration)`` has to describe the same stretch of ride, so dividing an
    NP computed over every recorded second by a duration that excludes some of
    them would be a load for a session nobody rode.
    """
    if np_watts is None:
        absent = NotAssessed("no power was recorded")
        return absent, absent
    if ftp is None or ftp.value <= 0:
        absent = NotAssessed("no FTP anchor is in force to compare the power against")
        return absent, absent
    if recording_time_s <= 0:
        absent = NotAssessed("the recording has no recording time to load over")
        return absent, absent

    factor = intensity_factor(np_watts, ftp.value)
    duration_s = round(recording_time_s)
    load = training_load(duration_s, factor)
    ftp_input = describe_anchor(ftp)
    return (
        Measured(
            value=factor,
            explanation=MetricExplanation(
                formula="IF = NP / FTP",
                inputs={"NP": f"{np_watts:.0f} W", "FTP": ftp_input},
                citation=_COGGAN,
            ),
        ),
        Measured(
            value=load,
            explanation=MetricExplanation(
                formula=f"TSS = duration_s × IF² / {TSS_SCALE:g}",
                inputs={
                    "duration": f"{duration_s} s of recording time",
                    "IF": f"{factor:.3f}",
                    "FTP": ftp_input,
                },
                assumptions=(
                    (
                        "the duration is recording time — elapsed minus every "
                        "stop over 30 s — not moving time and not elapsed"
                    ),
                ),
                citation=_COGGAN,
            ),
        ),
    )


def _zone_distribution(
    values: Sequence[float | None],
    anchor: AnchorVersion | None,
    model: ZoneModel,
    *,
    anchor_name: str,
) -> TimeInZone | NotAssessed:
    """Time in zone for one channel, or the reason there is none."""
    if anchor is None:
        return NotAssessed(f"no {anchor_name} anchor is in force to derive zones from")
    return time_in_zone(values, zones_for(anchor, model), model, anchor=anchor)


def analyse_session(inputs: SessionInputs) -> SessionAnalysis:
    """Run every metric the available inputs support (work order A-2..A-6).

    The order matters in exactly two places and nowhere else: NP is computed
    before IF and the load because they are defined in terms of it, and both
    load models are computed before :func:`select_training_load` chooses
    between them — which is A5.2's whole point, since storing only the
    selected one throws the comparison away permanently.
    """
    power = inputs.column(StreamChannel.POWER)
    heart_rate = inputs.column(StreamChannel.HR)
    cadence = inputs.column(StreamChannel.CADENCE)
    speed = inputs.column(StreamChannel.SPEED)
    elevation = inputs.column(StreamChannel.ELEVATION)
    ftp = inputs.anchor(AnchorType.FTP)
    lthr = inputs.anchor(AnchorType.LTHR)

    temperature = inputs.column(StreamChannel.TEMP)
    present_watts = [value for value in power if value is not None]
    np_assessment = _normalized_power(power, present_watts)
    np_watts = value_of(np_assessment)
    # One basis for every average in the artefact (D194), resolved once: two
    # averages divided by two different clocks would not describe one ride.
    basis = averaging_basis(inputs.moving_time_s, inputs.recording_time_s)
    basis_label = basis.label if isinstance(basis, AveragingBasis) else "moving time"
    average = average_power(power, basis)
    average_watts = value_of(average)
    average_beats = channel_average("heart rate", heart_rate)
    distance = distance_km(speed)

    factor, power_load = _power_load(np_watts, ftp, inputs.recording_time_s)
    hr_load = hrss(
        heart_rate,
        resting_hr=inputs.anchor(AnchorType.RESTING_HR),
        max_hr=inputs.anchor(AnchorType.MAX_HR),
        lthr=lthr,
        sex=inputs.sex,
    )

    return SessionAnalysis(
        recording_time_s=inputs.recording_time_s,
        elapsed_time_s=inputs.elapsed_time_s,
        moving_time_s=inputs.moving_time_s,
        stopped_time_s=stopped_time_s(
            elapsed_time_s=inputs.elapsed_time_s,
            moving_time_s=inputs.moving_time_s,
            recording_time_s=inputs.recording_time_s,
        ),
        power=PowerMetrics(
            normalized_power=np_assessment,
            average_power=average,
            max_power=channel_maximum("power", power),
            intensity_factor=factor,
            variability_index=(
                variability_index(np_watts, average_watts, basis_label=basis_label)
                if np_watts is not None and average_watts is not None
                else NotAssessed("no power was recorded")
            ),
            work_kj=work_kj(power),
            work_above_ftp_kj=work_above_ftp_kj(power, ftp),
            coasting_time_s=coasting_time_s(power, speed),
        ),
        heart_rate=HeartRateMetrics(
            average_hr=average_beats,
            max_hr=channel_maximum("heart rate", heart_rate),
            hrss=hr_load,
            efficiency_factor=(
                efficiency_factor(np_watts, beats)
                if np_watts is not None and (beats := value_of(average_beats))
                else NotAssessed(
                    "the efficiency factor needs both power and heart rate"
                )
            ),
        ),
        cadence=CadenceMetrics(
            average_cadence=channel_average("cadence", cadence),
            max_cadence=channel_maximum("cadence", cadence),
        ),
        speed=SpeedMetrics(
            distance_km=distance,
            average_speed_kmh=average_speed_kmh(distance, basis),
            max_speed_kmh=max_speed_kmh(speed),
        ),
        temperature=TemperatureMetrics(
            average_temp_c=channel_average("temperature", temperature),
            min_temp_c=channel_minimum("temperature", temperature),
            max_temp_c=channel_maximum("temperature", temperature),
        ),
        elevation_gain_m=elevation_gain_m(elevation),
        load=select_training_load(power_load, hr_load, inputs.discipline),
        power_time_in_zone=_zone_distribution(
            power, ftp, ZoneModel.COGGAN_7, anchor_name="FTP"
        ),
        hr_time_in_zone=_zone_distribution(
            heart_rate, lthr, ZoneModel.LTHR_5, anchor_name="LTHR"
        ),
        intervals=tuple(detect_work_intervals(power, hr_fixed=heart_rate)),
        strength=strength_volume(inputs.sets),
    )


# --- the stored wire form -----------------------------------------------------
#
# Written out rather than derived from the dataclasses (`dataclasses.asdict`)
# for the reason `app.domain.workout` gives for its own JSON functions: the
# stored shape is a contract with every artefact already written, and it must
# not change because a field was renamed for readability.


def explanation_to_json(explanation: MetricExplanation) -> dict[str, Any]:
    """Render an explanation for storage and for the API."""
    return {
        "formula": explanation.formula,
        "inputs": dict(explanation.inputs),
        "assumptions": list(explanation.assumptions),
        "citation": explanation.citation,
    }


def assessment_to_json(assessment: Assessment) -> dict[str, Any]:
    """Render one metric slot: a value with its explanation, or a reason.

    Exactly one of ``value`` and ``not_assessed`` is ever non-null, which is
    what lets the UI branch once and render the `NotAssessed` component for
    every absent metric rather than inventing an empty state per number.
    """
    if isinstance(assessment, Measured):
        return {
            "value": assessment.value,
            "explanation": explanation_to_json(assessment.explanation),
            "not_assessed": None,
        }
    return {"value": None, "explanation": None, "not_assessed": assessment.reason}


def _time_in_zone_to_json(distribution: TimeInZone | NotAssessed) -> dict[str, Any]:
    """Render one channel's zone distribution."""
    if isinstance(distribution, NotAssessed):
        return {"not_assessed": distribution.reason}
    return {
        "not_assessed": None,
        "zone_model": distribution.model.value,
        "zones": [
            {"index": zone.index, "name": zone.name, "seconds": zone.seconds}
            for zone in distribution.zones
        ],
        "total_s": distribution.total_s,
        "easy_s": distribution.easy_s,
        "moderate_s": distribution.moderate_s,
        "hard_s": distribution.hard_s,
        "polarization_index": assessment_to_json(distribution.polarization_index),
        "explanation": explanation_to_json(distribution.explanation),
    }


def _load_to_json(load: SelectedLoad | NotAssessed) -> dict[str, Any]:
    """Render the dual-load block (A5.2's exact field names)."""
    if isinstance(load, NotAssessed):
        return {"not_assessed": load.reason}
    return {
        "not_assessed": None,
        "training_load": load.training_load,
        "load_basis": load.basis.value,
        "load_basis_rule": load.rule,
        "power_load": load.power_load,
        "hr_load": load.hr_load,
        "explanation": explanation_to_json(load.explanation),
    }


def _strength_to_json(volume: StrengthVolume | NotAssessed) -> dict[str, Any]:
    """Render the strength block. Kilograms, never a load."""
    if isinstance(volume, NotAssessed):
        return {"not_assessed": volume.reason}
    return {
        "not_assessed": None,
        "volume_load_kg": volume.volume_load_kg,
        "sets_completed": volume.sets_completed,
        "coverage": volume.coverage,
        "explanation": explanation_to_json(volume.explanation),
    }


def analysis_to_json(analysis: SessionAnalysis) -> dict[str, Any]:
    """Render the whole metric set as the artefact's stored payload."""
    return {
        "recording_time_s": analysis.recording_time_s,
        "elapsed_time_s": analysis.elapsed_time_s,
        "moving_time_s": analysis.moving_time_s,
        "stopped_time_s": assessment_to_json(analysis.stopped_time_s),
        "power": {
            "normalized_power": assessment_to_json(analysis.power.normalized_power),
            "average_power": assessment_to_json(analysis.power.average_power),
            "max_power": assessment_to_json(analysis.power.max_power),
            "intensity_factor": assessment_to_json(analysis.power.intensity_factor),
            "variability_index": assessment_to_json(analysis.power.variability_index),
            "work_kj": assessment_to_json(analysis.power.work_kj),
            "work_above_ftp_kj": assessment_to_json(analysis.power.work_above_ftp_kj),
            "coasting_time_s": assessment_to_json(analysis.power.coasting_time_s),
        },
        "heart_rate": {
            "average_hr": assessment_to_json(analysis.heart_rate.average_hr),
            "max_hr": assessment_to_json(analysis.heart_rate.max_hr),
            "hrss": assessment_to_json(analysis.heart_rate.hrss),
            "efficiency_factor": assessment_to_json(
                analysis.heart_rate.efficiency_factor
            ),
        },
        "cadence": {
            "average_cadence": assessment_to_json(analysis.cadence.average_cadence),
            "max_cadence": assessment_to_json(analysis.cadence.max_cadence),
        },
        "speed": {
            "distance_km": assessment_to_json(analysis.speed.distance_km),
            "average_speed_kmh": assessment_to_json(analysis.speed.average_speed_kmh),
            "max_speed_kmh": assessment_to_json(analysis.speed.max_speed_kmh),
        },
        "temperature": {
            "average_temp_c": assessment_to_json(analysis.temperature.average_temp_c),
            "min_temp_c": assessment_to_json(analysis.temperature.min_temp_c),
            "max_temp_c": assessment_to_json(analysis.temperature.max_temp_c),
        },
        "elevation_gain_m": assessment_to_json(analysis.elevation_gain_m),
        "load": _load_to_json(analysis.load),
        "time_in_zone": {
            "power": _time_in_zone_to_json(analysis.power_time_in_zone),
            "hr": _time_in_zone_to_json(analysis.hr_time_in_zone),
        },
        "intervals": [
            {
                "start_index": interval.start_index,
                "end_index": interval.end_index,
                "duration_s": interval.duration_s,
                "average_power": interval.average_power,
                "max_power": interval.max_power,
                "average_hr": interval.average_hr,
            }
            for interval in analysis.intervals
        ],
        "strength": _strength_to_json(analysis.strength),
    }


def zone_model_of(distribution: TimeInZone | NotAssessed) -> ZoneModel | None:
    """The model a distribution was banded by, for the artefact's pin (A5.5)."""
    return distribution.model if isinstance(distribution, TimeInZone) else None

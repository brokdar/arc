"""The whole metric set, composed — and what it does when inputs are missing.

The composition is where the rules that cross metrics live (which duration the
load is computed over, which channel feeds which zone model, when HR wins),
so these tests are about the *set* rather than about any one number. The
per-metric arithmetic is pinned in `test_domain_metrics.py`,
`test_domain_hrss.py` and `test_domain_time_in_zone.py`.

The last test is the one that makes computing on ingest safe: no combination
of absent inputs may raise, because a metric failure must never un-ingest a
file.
"""

import datetime as dt
from collections.abc import Sequence
from typing import Any

import pytest

from app.domain.activity import SessionDiscipline
from app.domain.anchors import (
    ANCHOR_UNITS,
    AnchorSource,
    AnchorType,
    AnchorVersion,
    Provenance,
)
from app.domain.athlete import Sex
from app.domain.metrics import (
    MS_TO_KMH,
    LoadBasis,
    Measured,
    NotAssessed,
    PerformedSet,
    SelectedLoad,
    StrengthVolume,
    TimeInZone,
    intensity_factor,
    normalized_power,
    training_load,
)
from app.domain.session_analysis import (
    SessionAnalysis,
    SessionInputs,
    analyse_session,
    analysis_to_json,
    zone_model_of,
)
from app.domain.streams import StreamChannel
from app.domain.zones import ZoneModel


def anchor(anchor_type: AnchorType, value: float) -> AnchorVersion:
    """One anchor version, everything but the value held constant."""
    return AnchorVersion(
        anchor_type=anchor_type,
        value=value,
        unit=ANCHOR_UNITS[anchor_type],
        provenance=Provenance.ESTIMATED,
        effective_date=dt.date(2026, 5, 1),
        created_at=dt.datetime(2026, 5, 1, tzinfo=dt.UTC),
        source=AnchorSource.ATHLETE,
    )


ANCHORS = {
    AnchorType.FTP: anchor(AnchorType.FTP, 250.0),
    AnchorType.LTHR: anchor(AnchorType.LTHR, 165.0),
    AnchorType.MAX_HR: anchor(AnchorType.MAX_HR, 190.0),
    AnchorType.RESTING_HR: anchor(AnchorType.RESTING_HR, 50.0),
}

#: A 20-minute ride: 10 min at 150 W / 130 bpm, 10 min at 260 W / 168 bpm.
WATTS: Sequence[float | None] = [150.0] * 600 + [260.0] * 600
BEATS: Sequence[float | None] = [130.0] * 600 + [168.0] * 600
#: 9 m/s for 1 150 s, then 50 s at a standstill. It **is** the ride's moving
#: time: the artefact counts this column rather than being told a number
#: alongside it (D196), so the fixture cannot state a moving time the stream
#: does not support.
SPEED: Sequence[float | None] = [9.0] * 1_150 + [0.0] * 50


def ride(**overrides: Any) -> SessionInputs:
    """The synthetic ride, with any input replaced."""
    inputs: dict[str, Any] = {
        "discipline": SessionDiscipline.CYCLING,
        "recording_time_s": 1_200.0,
        "elapsed_time_s": 1_260.0,
        "columns": {
            StreamChannel.POWER: tuple(WATTS),
            StreamChannel.HR: tuple(BEATS),
            StreamChannel.CADENCE: tuple([88.0] * 1_200),
            # 1 150 rows travelling and 50 standing at a light, which is
            # exactly the moving time above: a fixture whose speed column and
            # whose moving time disagree cannot check an average taken over
            # one against a distance integrated from the other (D194).
            StreamChannel.SPEED: tuple(SPEED),
            StreamChannel.ELEVATION: tuple(
                [100.0 + index / 10 for index in range(1_200)]
            ),
            StreamChannel.TEMP: tuple([14.0] * 600 + [20.0] * 600),
        },
        "sex": Sex.MALE,
        "anchors": dict(ANCHORS),
    }
    return SessionInputs(**(inputs | overrides))


def test_the_chain_agrees_with_the_domain_functions_run_directly() -> None:
    """The composition must not be a second implementation of the chain.

    Running NP → IF → TSS by hand over the same series has to reproduce the
    artefact's numbers exactly; if it does not, there are two implementations
    and the planned and recorded loads have stopped being comparable (A3.2).
    """
    analysis = analyse_session(ride())
    expected_np = normalized_power([value for value in WATTS if value is not None])
    expected_if = intensity_factor(expected_np, 250.0)

    assert isinstance(analysis.power.normalized_power, Measured)
    assert isinstance(analysis.power.intensity_factor, Measured)
    assert isinstance(analysis.load, SelectedLoad)
    assert analysis.power.normalized_power.value == pytest.approx(expected_np)
    assert analysis.power.intensity_factor.value == pytest.approx(expected_if)
    assert analysis.load.power_load == pytest.approx(training_load(1_200, expected_if))


def test_the_load_duration_is_recording_time_not_elapsed() -> None:
    """A5.1's whole point, at the place the choice is actually made."""
    shorter = analyse_session(ride(recording_time_s=600.0))
    longer = analyse_session(ride(recording_time_s=1_200.0))

    assert isinstance(shorter.load, SelectedLoad)
    assert isinstance(longer.load, SelectedLoad)
    assert shorter.load.power_load is not None
    assert longer.load.power_load is not None
    assert longer.load.power_load == pytest.approx(2 * shorter.load.power_load)
    assert any(
        "recording time" in note
        for note in longer.load.explanation.assumptions
        + tuple(longer.load.explanation.inputs.values())
    )


def test_a_ride_with_both_channels_stores_both_loads() -> None:
    analysis = analyse_session(ride())

    assert isinstance(analysis.load, SelectedLoad)
    assert analysis.load.basis is LoadBasis.POWER
    assert analysis.load.power_load is not None
    assert analysis.load.hr_load is not None


def test_a_ride_with_no_power_meter_answers_with_reasons_and_the_hr_load() -> None:
    analysis = analyse_session(
        ride(
            columns={
                StreamChannel.HR: tuple(BEATS),
                StreamChannel.CADENCE: tuple([88.0] * 1_200),
            }
        )
    )

    assert analysis.power.normalized_power == NotAssessed("no power was recorded")
    assert isinstance(analysis.power.intensity_factor, NotAssessed)
    assert isinstance(analysis.load, SelectedLoad)
    assert analysis.load.basis is LoadBasis.HR
    assert analysis.load.power_load is None
    # The HR distribution still exists: it never needed the power channel.
    assert isinstance(analysis.hr_time_in_zone, TimeInZone)
    assert isinstance(analysis.power_time_in_zone, NotAssessed)


def test_a_missing_anchor_blocks_only_what_depends_on_it() -> None:
    analysis = analyse_session(
        ride(anchors={AnchorType.LTHR: ANCHORS[AnchorType.LTHR]})
    )

    # No FTP: no IF, no TSS, no power zones, no work-above-threshold.
    assert isinstance(analysis.power.intensity_factor, NotAssessed)
    assert isinstance(analysis.power.work_above_ftp_kj, NotAssessed)
    assert isinstance(analysis.power_time_in_zone, NotAssessed)
    # But NP, average power and work never needed one.
    assert isinstance(analysis.power.normalized_power, Measured)
    assert isinstance(analysis.power.work_kj, Measured)
    # And no resting HR means no HRSS, so nothing selects a load at all.
    assert isinstance(analysis.load, NotAssessed)


def test_the_artefact_records_the_zone_model_of_each_channel() -> None:
    # A5.5: `(anchor version, zone model) -> zones` is deterministic only
    # while the model is pinned beside the anchor.
    analysis = analyse_session(ride())

    assert zone_model_of(analysis.power_time_in_zone) is ZoneModel.COGGAN_7
    assert zone_model_of(analysis.hr_time_in_zone) is ZoneModel.LTHR_5
    assert zone_model_of(NotAssessed("nope")) is None


def test_detected_intervals_travel_with_the_metrics() -> None:
    # D118: detection is deterministic from the stream, so it is versioned
    # with the metrics rather than recomputed by each consumer.
    analysis = analyse_session(ride())

    assert len(analysis.intervals) == 1
    assert analysis.intervals[0].average_power == pytest.approx(260.0, abs=5)


def test_a_manual_strength_session_reports_volume_and_nothing_it_cannot() -> None:
    analysis = analyse_session(
        SessionInputs(
            discipline=SessionDiscipline.STRENGTH,
            recording_time_s=0.0,
            elapsed_time_s=3_600.0,
            columns={},
            sets=[PerformedSet(reps=5, load_kg=100.0)] * 4,
        )
    )

    assert isinstance(analysis.strength, StrengthVolume)
    assert analysis.strength.volume_load_kg == pytest.approx(2_000.0)
    assert analysis.strength.sets_completed == 4
    assert isinstance(analysis.load, NotAssessed)
    assert analysis.intervals == ()


def moved(rows: int) -> dict[StreamChannel, tuple[float | None, ...]]:
    """The ride's columns with only the first ``rows`` seconds spent moving."""
    return dict(ride().columns) | {
        StreamChannel.SPEED: tuple([9.0] * rows + [0.0] * (1_200 - rows))
    }


def test_the_averages_use_moving_time_and_the_load_does_not() -> None:
    """D194's split, stated as the one thing that could silently be wrong.

    Two rides identical but for how much of them was spent moving: the average
    power differs (it is divided by moving time, and summed over the same
    seconds — D196) and the training load does not (its duration term is still
    recording time, A5.1). If a later change routes the load through the
    averaging basis, this fails.
    """
    riding = analyse_session(ride(columns=moved(1_150)))
    stopping = analyse_session(ride(columns=moved(575)))

    assert isinstance(riding.power.average_power, Measured)
    assert isinstance(stopping.power.average_power, Measured)
    assert isinstance(riding.load, SelectedLoad)
    assert isinstance(stopping.load, SelectedLoad)
    assert riding.moving_time_s == pytest.approx(1_150.0)
    assert stopping.moving_time_s == pytest.approx(575.0)
    assert riding.power.average_power.value == pytest.approx(
        sum(watts or 0.0 for watts in WATTS[:1_150]) / 1_150
    )
    assert stopping.power.average_power.value == pytest.approx(
        sum(watts or 0.0 for watts in WATTS[:575]) / 575
    )
    assert riding.load.training_load == pytest.approx(stopping.load.training_load)
    # And the number says which clock it was divided by, both ways round.
    assert "moving time" in riding.power.average_power.explanation.formula


def test_a_speed_sensor_that_dies_halfway_inflates_nothing() -> None:
    """D196's headline case, composed.

    The speed channel stops reporting at half distance and the athlete rides
    on at the same power. Before the fix the artefact divided a whole ride's
    work by half a ride's moving time — 2× the average power — and reported
    the missing half as thirty minutes standing at the kerb. Now the basis
    refuses to be moving time at all: the averages fall back to recording time
    and say why, and stopped time is a reason rather than a fabricated number.
    """
    half = analyse_session(
        ride(columns=dict(ride().columns) | {StreamChannel.SPEED: tuple([9.0] * 600)})
    )
    whole = analyse_session(ride(columns=moved(1_200)))

    assert isinstance(half.power.average_power, Measured)
    assert isinstance(whole.power.average_power, Measured)
    joules = sum(watts or 0.0 for watts in WATTS)
    # Over recording time, which is the honest divisor once the channel that
    # would have supplied a better one has been shown not to.
    assert half.power.average_power.value == pytest.approx(joules / 1_200)
    assert half.power.average_power.value < whole.power.average_power.value * 1.05
    assert any(
        "covers only 50%" in note
        for note in half.power.average_power.explanation.assumptions
    )
    # And the half hour the sensor was silent is not reported as standing still.
    assert isinstance(half.stopped_time_s, NotAssessed)
    assert "50%" in half.stopped_time_s.reason


def test_the_variability_index_never_falls_below_one() -> None:
    """VI is a ratio of two statistics of one series, so Jensen bounds it.

    The ride here is steady 200 W with twenty-four recorded traffic lights, so
    its moving-time average power is exactly the 200 W it rode at while NP is
    lower — dividing one by the other reports a ride *less* variable than a
    perfectly steady one, which is not a thing that exists (D196).
    """
    watts = tuple(0.0 if 25 <= second % 150 < 50 else 200.0 for second in range(1_200))
    speed = tuple(0.0 if 25 <= second % 150 < 50 else 9.0 for second in range(1_200))
    analysis = analyse_session(
        ride(
            columns={StreamChannel.POWER: watts, StreamChannel.SPEED: speed},
        )
    )

    assert isinstance(analysis.power.average_power, Measured)
    assert isinstance(analysis.power.variability_index, Measured)
    assert analysis.power.average_power.value == pytest.approx(200.0)
    assert analysis.power.variability_index.value >= 1.0


def test_a_ride_with_no_speed_channel_averages_over_recording_time() -> None:
    """The fallback, and what it refuses to invent.

    An indoor session records no speed, so there is no moving time to divide
    by and no distance to report — but there is still an average power, over
    the recording time that does exist, and it says so.
    """
    indoor = analyse_session(
        ride(
            columns={
                StreamChannel.POWER: tuple(WATTS),
                StreamChannel.HR: tuple(BEATS),
            },
        )
    )

    assert isinstance(indoor.power.average_power, Measured)
    joules = sum(value for value in WATTS if value is not None)
    assert indoor.power.average_power.value == pytest.approx(joules / 1_200)
    assert "recording time" in indoor.power.average_power.explanation.formula
    assert isinstance(indoor.speed.distance_km, NotAssessed)
    assert isinstance(indoor.speed.average_speed_kmh, NotAssessed)
    # And stopped time is refused rather than claimed to be the whole ride.
    assert isinstance(indoor.stopped_time_s, NotAssessed)


def test_the_ride_log_basics_come_off_the_speed_channel() -> None:
    analysis = analyse_session(ride())

    assert isinstance(analysis.speed.distance_km, Measured)
    assert isinstance(analysis.speed.average_speed_kmh, Measured)
    assert isinstance(analysis.speed.max_speed_kmh, Measured)
    # 1 150 s at 9 m/s is 10.35 km; over the same 1 150 s of moving time that
    # is 32.4 km/h, which at a constant speed is also the maximum.
    assert analysis.speed.distance_km.value == pytest.approx(10.35)
    assert analysis.speed.average_speed_kmh.value == pytest.approx(9.0 * MS_TO_KMH)
    assert analysis.speed.max_speed_kmh.value == pytest.approx(9.0 * MS_TO_KMH)


def test_stopped_time_is_the_ride_minus_the_riding() -> None:
    # Elapsed 1 260, moving 1 150: 110 s standing, of which the device paused
    # for 60 (1 260 − 1 200 of recording time) and kept recording through 50.
    analysis = analyse_session(ride())

    assert isinstance(analysis.stopped_time_s, Measured)
    assert analysis.stopped_time_s.value == pytest.approx(110.0)


def test_temperature_reports_the_range_and_not_just_the_mean() -> None:
    analysis = analyse_session(ride())

    assert isinstance(analysis.temperature.average_temp_c, Measured)
    assert isinstance(analysis.temperature.min_temp_c, Measured)
    assert isinstance(analysis.temperature.max_temp_c, Measured)
    assert analysis.temperature.average_temp_c.value == pytest.approx(17.0)
    assert analysis.temperature.min_temp_c.value == pytest.approx(14.0)
    assert analysis.temperature.max_temp_c.value == pytest.approx(20.0)


def test_a_ride_with_no_thermometer_says_so() -> None:
    analysis = analyse_session(ride(columns={StreamChannel.POWER: tuple(WATTS)}))

    assert analysis.temperature.average_temp_c == NotAssessed(
        "no temperature was recorded"
    )


def test_a_ride_never_reports_a_strength_volume() -> None:
    analysis = analyse_session(ride())

    assert analysis.strength == NotAssessed("no sets were logged")


def leaves(document: Any, path: str = "") -> list[tuple[str, dict[str, Any]]]:
    """Every metric slot in a rendered payload, with the path that reached it."""
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(document, dict):
        if "not_assessed" in document and "value" in document:
            found.append((path, document))
            return found
        for key, value in document.items():
            found.extend(leaves(value, f"{path}.{key}"))
    elif isinstance(document, list):
        for index, value in enumerate(document):
            found.extend(leaves(value, f"{path}[{index}]"))
    return found


def test_every_rendered_slot_answers_exactly_once() -> None:
    """The invariant the UI branches on: a value, or a reason, never both."""
    payload = analysis_to_json(analyse_session(ride()))
    slots = leaves(payload)

    assert slots
    for path, slot in slots:
        answered = slot["value"] is not None
        refused = slot["not_assessed"] is not None
        assert answered != refused, path
        assert (slot["explanation"] is not None) == answered, path


def test_the_payload_is_json_serialisable_and_round_trips() -> None:
    import json

    payload = analysis_to_json(analyse_session(ride()))

    assert json.loads(json.dumps(payload)) == payload


def test_nothing_raises_when_every_input_is_absent() -> None:
    """The guarantee compute-on-ingest depends on.

    A session with no stream, no anchors, no sex and no sets still produces a
    complete artefact — every slot carrying the reason it is empty. There is
    no such thing as a session that fails to be analysed.
    """
    analysis = analyse_session(
        SessionInputs(
            discipline=SessionDiscipline.OTHER,
            recording_time_s=0.0,
            elapsed_time_s=0.0,
            columns={},
        )
    )

    assert isinstance(analysis, SessionAnalysis)
    for path, slot in leaves(analysis_to_json(analysis)):
        assert slot["value"] is None, path
        assert slot["not_assessed"], path

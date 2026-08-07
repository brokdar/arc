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


def ride(**overrides: Any) -> SessionInputs:
    """The synthetic ride, with any input replaced."""
    inputs: dict[str, Any] = {
        "discipline": SessionDiscipline.CYCLING,
        "recording_time_s": 1_200.0,
        "elapsed_time_s": 1_260.0,
        "moving_time_s": 1_150.0,
        "columns": {
            StreamChannel.POWER: tuple(WATTS),
            StreamChannel.HR: tuple(BEATS),
            StreamChannel.CADENCE: tuple([88.0] * 1_200),
            StreamChannel.SPEED: tuple([9.0] * 1_200),
            StreamChannel.ELEVATION: tuple(
                [100.0 + index / 10 for index in range(1_200)]
            ),
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
            moving_time_s=0.0,
            columns={},
            sets=[PerformedSet(reps=5, load_kg=100.0)] * 4,
        )
    )

    assert isinstance(analysis.strength, StrengthVolume)
    assert analysis.strength.volume_load_kg == pytest.approx(2_000.0)
    assert analysis.strength.sets_completed == 4
    assert isinstance(analysis.load, NotAssessed)
    assert analysis.intervals == ()


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
            moving_time_s=0.0,
            columns={},
        )
    )

    assert isinstance(analysis, SessionAnalysis)
    for path, slot in leaves(analysis_to_json(analysis)):
        assert slot["value"] is None, path
        assert slot["not_assessed"], path

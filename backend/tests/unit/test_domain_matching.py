"""The matching rulebook, with no database in sight.

Three things are pinned here and nowhere else, because nowhere else can state
them cheaply: the arithmetic of the similarity score (including what happens
when an input is missing), the thresholds that turn it into a decision, and
the two date rules — the candidate window and when a planned session has run
out of grace.
"""

import datetime as dt
import uuid

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.anchors import (
    AnchorSource,
    AnchorType,
    AnchorUnit,
    AnchorVersion,
    Provenance,
)
from app.domain.matching import (
    AUTO_LINK_SIMILARITY,
    COMPONENT_WEIGHTS,
    MIN_STRUCTURE_UNITS,
    PROPOSAL_SIMILARITY,
    WEIGHT_DURATION,
    WEIGHT_INTENSITY,
    WEIGHT_STRUCTURE,
    IntensityBasis,
    MatchComponent,
    MatchEvidence,
    MatchLinkStatus,
    Similarity,
    StructureBasis,
    better,
    candidate_window,
    classify,
    date_distance,
    in_candidate_window,
    is_missed,
    missed_on_or_before,
    planned_hr_intensity,
    planned_power_intensity,
    planned_work_steps,
    similarity,
    similarity_to_json,
)
from app.domain.prediction import PinnedAnchor
from app.domain.workout import (
    Channel,
    EnduranceWorkout,
    PercentOfAnchor,
    RepeatBlock,
    SteadyStep,
    StepRole,
)

FTP_VERSION_ID = uuid.UUID("019fe000-0000-7000-8000-000000000001")


def pinned_ftp(value: float = 250.0) -> dict[AnchorType, PinnedAnchor]:
    """One pinned FTP, the only anchor the endurance prediction needs."""
    return {
        AnchorType.FTP: PinnedAnchor(
            version_id=FTP_VERSION_ID,
            version=AnchorVersion(
                anchor_type=AnchorType.FTP,
                value=value,
                unit=AnchorUnit.WATT,
                provenance=Provenance.TESTED,
                source=AnchorSource.ATHLETE,
                effective_date=dt.date(2026, 1, 1),
                created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                protocol="20 min",
            ),
        )
    }


def scores(result: Similarity) -> dict[MatchComponent, float]:
    """The assessed components of a similarity, by name."""
    return {part.component: part.score for part in result.components}


# --- the weights ---------------------------------------------------------------


def test_the_weights_are_the_build_plans_and_they_sum_to_one() -> None:
    """0.4 / 0.3 / 0.3, stated once and summing to a whole score."""
    assert (WEIGHT_DURATION, WEIGHT_INTENSITY, WEIGHT_STRUCTURE) == (0.4, 0.3, 0.3)
    assert sum(COMPONENT_WEIGHTS.values()) == pytest.approx(1.0)
    assert set(COMPONENT_WEIGHTS) == set(MatchComponent)


def test_a_perfect_match_scores_one_over_all_three_components() -> None:
    result = similarity(
        MatchEvidence(
            planned_duration_s=3_600,
            actual_duration_s=3_600,
            planned_intensity=230,
            actual_intensity=230,
            intensity_basis=IntensityBasis.POWER,
            planned_units=4,
            performed_units=4,
            structure_basis=StructureBasis.INTERVALS,
        )
    )

    assert result.score == pytest.approx(1.0)
    assert not result.not_assessed
    assert [part.weight for part in result.components] == [0.4, 0.3, 0.3]


def test_the_score_is_the_weighted_mean_of_its_components() -> None:
    """Half the duration, on-target intensity, half the intervals."""
    result = similarity(
        MatchEvidence(
            planned_duration_s=3_600,
            actual_duration_s=1_800,
            planned_intensity=200,
            actual_intensity=200,
            intensity_basis=IntensityBasis.POWER,
            planned_units=4,
            performed_units=2,
            structure_basis=StructureBasis.INTERVALS,
        )
    )

    assert scores(result) == {
        MatchComponent.DURATION: pytest.approx(0.5),
        MatchComponent.INTENSITY: pytest.approx(1.0),
        MatchComponent.STRUCTURE: pytest.approx(0.5),
    }
    assert result.score == pytest.approx(0.4 * 0.5 + 0.3 * 1.0 + 0.3 * 0.5)


# --- the renormalisation (D138) -------------------------------------------------


def test_a_missing_component_is_renormalised_not_defaulted() -> None:
    """The whole of D138 in one assertion.

    A ride with no power against a prescription with a power target has no
    intensity term. Scoring it 1.0 would invent agreement and 0.0 would invent
    disagreement; both are visible here as the numbers this test is *not*.
    """
    evidence = MatchEvidence(
        planned_duration_s=3_600,
        actual_duration_s=3_600,
        planned_units=4,
        performed_units=2,
        structure_basis=StructureBasis.INTERVALS,
    )

    result = similarity(evidence)

    assert [part.component for part in result.not_assessed] == [
        MatchComponent.INTENSITY
    ]
    # Duration 0.4 and structure 0.3 renormalise to 4/7 and 3/7.
    assert [part.weight for part in result.components] == [
        pytest.approx(0.4 / 0.7),
        pytest.approx(0.3 / 0.7),
    ]
    assert result.score == pytest.approx(1.0 * 4 / 7 + 0.5 * 3 / 7)
    # The two defaults this rule exists to refuse.
    as_one = 0.4 * 1.0 + 0.3 * 1.0 + 0.3 * 0.5
    as_zero = 0.4 * 1.0 + 0.3 * 0.0 + 0.3 * 0.5
    assert result.score != pytest.approx(as_one)
    assert result.score != pytest.approx(as_zero)


def test_the_applied_weights_always_sum_to_one() -> None:
    """However many components survived, the mean is still a mean."""
    for evidence in (
        MatchEvidence(planned_duration_s=60, actual_duration_s=60),
        MatchEvidence(
            planned_duration_s=60,
            actual_duration_s=30,
            planned_intensity=1,
            actual_intensity=2,
            intensity_basis=IntensityBasis.HR,
        ),
        MatchEvidence(
            planned_units=5,
            performed_units=5,
            structure_basis=StructureBasis.SETS,
        ),
    ):
        result = similarity(evidence)
        assert sum(part.weight for part in result.components) == pytest.approx(1.0)


def test_nothing_to_compare_scores_none_rather_than_zero() -> None:
    """A hand-typed gym session that logged no sets.

    ``None`` is the honest answer and 0.0 is not: the date and the discipline
    agree, and it is the *comparison* that is unavailable.
    """
    result = similarity(MatchEvidence())

    assert result.score is None
    assert not result.assessed
    assert len(result.not_assessed) == len(MatchComponent)
    assert classify(result.score) is MatchLinkStatus.PENDING


def test_a_single_work_step_is_not_a_structure_to_compare(  # D139
) -> None:
    """A steady endurance ride detects no intervals, and correctly so."""
    steady = MatchEvidence(
        planned_duration_s=3_600,
        actual_duration_s=3_600,
        planned_units=1,
        performed_units=0,
        structure_basis=StructureBasis.INTERVALS,
    )

    result = similarity(steady)

    assert [part.component for part in result.not_assessed] == [
        MatchComponent.INTENSITY,
        MatchComponent.STRUCTURE,
    ]
    # Without the floor this would have been 0.4/0.7 — below the auto-link
    # threshold — for a session ridden exactly as prescribed.
    assert result.score == pytest.approx(1.0)
    assert classify(result.score) is MatchLinkStatus.AUTO_HIGH


def test_a_structured_prescription_with_nothing_detected_scores_zero() -> None:
    """The other side of the floor: real disagreement is not absent evidence."""
    result = similarity(
        MatchEvidence(
            planned_units=MIN_STRUCTURE_UNITS,
            performed_units=0,
            structure_basis=StructureBasis.INTERVALS,
        )
    )

    assert scores(result) == {MatchComponent.STRUCTURE: 0.0}
    assert result.score == pytest.approx(0.0)


def test_the_breakdown_says_what_it_compared_and_what_it_could_not() -> None:
    document = similarity_to_json(
        similarity(
            MatchEvidence(
                planned_duration_s=3_600,
                actual_duration_s=1_800,
                planned_units=4,
                performed_units=4,
                structure_basis=StructureBasis.INTERVALS,
            )
        )
    )

    assert document["weights"] == {"duration": 0.4, "intensity": 0.3, "structure": 0.3}
    duration = next(
        part for part in document["components"] if part["component"] == "duration"
    )
    assert (duration["planned"], duration["actual"]) == (3_600.0, 1_800.0)
    assert duration["nominal_weight"] == 0.4
    assert duration["weight"] == pytest.approx(0.4 / 0.7)
    [absent] = document["not_assessed"]
    assert absent["component"] == "intensity"
    assert "power nor heart rate" in absent["reason"]


# --- properties -----------------------------------------------------------------

positives = st.floats(min_value=0.1, max_value=100_000, allow_nan=False)
counts = st.integers(min_value=0, max_value=50)
optional_positive = st.one_of(st.none(), positives)
optional_count = st.one_of(st.none(), counts)


@given(
    planned_duration=optional_positive,
    actual_duration=optional_positive,
    planned_intensity=optional_positive,
    actual_intensity=optional_positive,
    planned_units=optional_count,
    performed_units=optional_count,
)
def test_similarity_is_always_in_the_unit_interval_or_absent(
    planned_duration: float | None,
    actual_duration: float | None,
    planned_intensity: float | None,
    actual_intensity: float | None,
    planned_units: int | None,
    performed_units: int | None,
) -> None:
    """The one invariant every consumer depends on.

    ``classify`` compares the score against two constants and the column is a
    float — so "a number in [0, 1], or nothing at all" is the whole contract,
    and no combination of present and absent inputs may break it.
    """
    result = similarity(
        MatchEvidence(
            planned_duration_s=planned_duration,
            actual_duration_s=actual_duration,
            planned_intensity=planned_intensity,
            actual_intensity=actual_intensity,
            intensity_basis=IntensityBasis.POWER,
            planned_units=planned_units,
            performed_units=performed_units,
            structure_basis=StructureBasis.INTERVALS,
        )
    )

    assert result.score is None or 0.0 <= result.score <= 1.0
    assert all(0.0 <= part.score <= 1.0 for part in result.components)
    assert len(result.components) + len(result.not_assessed) == len(MatchComponent)


@given(planned=positives, actual=positives)
def test_the_score_is_symmetric_in_its_two_sides(planned: float, actual: float) -> None:
    """Half as long and twice as long agree equally badly."""
    forward = similarity(
        MatchEvidence(planned_duration_s=planned, actual_duration_s=actual)
    )
    backward = similarity(
        MatchEvidence(planned_duration_s=actual, actual_duration_s=planned)
    )

    assert forward.score == pytest.approx(backward.score)


# --- the thresholds -------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (1.0, MatchLinkStatus.AUTO_HIGH),
        (AUTO_LINK_SIMILARITY, MatchLinkStatus.AUTO_HIGH),
        (AUTO_LINK_SIMILARITY - 1e-9, MatchLinkStatus.PENDING),
        (0.5, MatchLinkStatus.PENDING),
        (PROPOSAL_SIMILARITY, MatchLinkStatus.PENDING),
        (PROPOSAL_SIMILARITY - 1e-9, None),
        (0.0, None),
        (None, MatchLinkStatus.PENDING),
    ],
)
def test_the_thresholds_are_inclusive_at_the_bottom_of_each_band(
    score: float | None, expected: MatchLinkStatus | None
) -> None:
    assert classify(score) is expected


def test_a_scored_candidate_beats_an_unscored_one_however_low_it_scores() -> None:
    scored = similarity(MatchEvidence(planned_duration_s=3_600, actual_duration_s=400))
    unscored = similarity(MatchEvidence())

    assert better(scored, unscored)
    assert not better(unscored, scored)
    assert not better(unscored, unscored)


# --- the two date rules ---------------------------------------------------------


def test_the_candidate_window_is_one_day_either_side() -> None:
    day = dt.date(2026, 5, 4)

    assert candidate_window(day) == (dt.date(2026, 5, 3), dt.date(2026, 5, 5))
    assert in_candidate_window(day, dt.date(2026, 5, 3))
    assert in_candidate_window(day, dt.date(2026, 5, 5))
    assert not in_candidate_window(day, dt.date(2026, 5, 2))
    assert not in_candidate_window(day, dt.date(2026, 5, 6))
    assert date_distance(day, dt.date(2026, 5, 3)) == 1
    assert date_distance(day, day) == 0


@pytest.mark.parametrize(
    ("planned", "today", "missed"),
    [
        # Planned Monday. Answerable through Tuesday; missed from Wednesday.
        (dt.date(2026, 5, 4), dt.date(2026, 5, 4), False),
        (dt.date(2026, 5, 4), dt.date(2026, 5, 5), False),
        (dt.date(2026, 5, 4), dt.date(2026, 5, 6), True),
        (dt.date(2026, 5, 4), dt.date(2026, 5, 20), True),
        # Tomorrow's session is not missed, and neither is one in a leap year's
        # awkward corner.
        (dt.date(2026, 5, 5), dt.date(2026, 5, 4), False),
        (dt.date(2028, 2, 28), dt.date(2028, 3, 1), True),
        (dt.date(2028, 2, 28), dt.date(2028, 2, 29), False),
    ],
)
def test_the_missed_boundary_is_the_end_of_the_following_day(
    planned: dt.date, today: dt.date, missed: bool
) -> None:
    assert is_missed(planned, today) is missed
    assert (planned <= missed_on_or_before(today)) is missed


# --- the planned side of the intensity term -------------------------------------


def ride(*, pct: float = 0.9, work_steps: int = 3) -> EnduranceWorkout:
    """A warm-up plus ``work_steps`` efforts at ``pct`` of FTP."""
    return EnduranceWorkout(
        steps=(
            SteadyStep(duration_s=600, role=StepRole.WARMUP),
            RepeatBlock(
                times=work_steps,
                children=(
                    SteadyStep(
                        duration_s=480,
                        role=StepRole.WORK,
                        targets={
                            Channel.POWER: PercentOfAnchor(
                                anchor_type=AnchorType.FTP,
                                pct_low=pct,
                                pct_high=pct,
                            )
                        },
                    ),
                    SteadyStep(duration_s=240, role=StepRole.RECOVERY),
                ),
            ),
        )
    )


def test_the_planned_intensity_comes_off_the_pins_not_todays_anchor() -> None:
    """Invariant 4: the score is against what was frozen, never re-derived."""
    workout = ride()

    against_pin = planned_power_intensity(workout, pinned_ftp(250))
    against_a_new_test = planned_power_intensity(workout, pinned_ftp(280))

    assert against_pin is not None
    assert against_a_new_test is not None
    # The same prescription resolves to different watts against different
    # pins, which is exactly why the pin — not the current anchor — is what
    # the service hands in.
    assert against_pin < against_a_new_test
    # 0.9 x 250 W over 3 x 480 s of a 2 760 s prescription, through the same
    # NP -> IF chain the session sheet uses. Well under the 225 W of the work
    # steps themselves, because the warm-up and the recoveries prescribe
    # nothing and count as 0 W — the coverage caveat `PredictedLoad` already
    # carries, inherited here rather than re-invented.
    assert against_pin == pytest.approx(189.4, abs=0.5)


def test_a_prescription_with_no_pinned_ftp_has_no_planned_intensity() -> None:
    assert planned_power_intensity(ride(), {}) is None


def test_the_heart_rate_fallback_is_duration_weighted() -> None:
    """Ten minutes at 150 and twenty at 120 average to 130, not 135."""
    workout = EnduranceWorkout(
        steps=(
            SteadyStep(
                duration_s=600,
                role=StepRole.WORK,
                targets={
                    Channel.HR: PercentOfAnchor(
                        anchor_type=AnchorType.LTHR, pct_low=1.0, pct_high=1.0
                    )
                },
            ),
            SteadyStep(
                duration_s=1_200,
                role=StepRole.WORK,
                targets={
                    Channel.HR: PercentOfAnchor(
                        anchor_type=AnchorType.LTHR, pct_low=0.8, pct_high=0.8
                    )
                },
            ),
            # No heart-rate target: left out of both sides of the average.
            SteadyStep(duration_s=3_000, role=StepRole.RECOVERY),
        )
    )
    anchors = {
        AnchorType.LTHR: PinnedAnchor(
            version_id=FTP_VERSION_ID,
            version=AnchorVersion(
                anchor_type=AnchorType.LTHR,
                value=150,
                unit=AnchorUnit.BPM,
                provenance=Provenance.TESTED,
                source=AnchorSource.ATHLETE,
                effective_date=dt.date(2026, 1, 1),
                created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                protocol="30 min",
            ),
        )
    }

    assert planned_hr_intensity(workout, anchors) == pytest.approx(130.0)
    assert planned_hr_intensity(workout, {}) is None


def test_only_work_steps_count_toward_the_structure_hint() -> None:
    """The warm-up and the recoveries are not prescribed efforts."""
    assert planned_work_steps(ride(work_steps=3)) == 3
    assert planned_work_steps(ride(work_steps=1)) == 1

"""Resolving prescribed targets against the anchor versions a session pinned.

The cases here are the ones awkward to reach through HTTP: a point target, a
percentage of an anchor nothing pinned, a ramp whose ends resolve separately.
What the API does with all of it is pinned in `test_planned_sessions_api.py`.
"""

import datetime as dt
import uuid

from app.domain.anchors import (
    AnchorSource,
    AnchorType,
    AnchorUnit,
    AnchorVersion,
    Provenance,
)
from app.domain.prediction import PinnedAnchor
from app.domain.resolution import render_target, resolve_steps, resolve_target
from app.domain.strength import (
    Load,
    LoadKind,
    StrengthGroup,
    StrengthSet,
    StrengthWorkout,
)
from app.domain.workout import (
    AbsoluteRange,
    Channel,
    ChannelUnit,
    EnduranceWorkout,
    PercentOfAnchor,
    RampStep,
    SteadyStep,
    StepRole,
)

FTP_VERSION_ID = uuid.UUID("0199a000-0000-7000-8000-0000000000f1")


def pinned_ftp(value: float = 250) -> dict[AnchorType, PinnedAnchor]:
    """One pinned FTP version, the way a planned session carries it."""
    return {
        AnchorType.FTP: PinnedAnchor(
            version_id=FTP_VERSION_ID,
            version=AnchorVersion(
                anchor_type=AnchorType.FTP,
                value=value,
                unit=AnchorUnit.WATT,
                provenance=Provenance.ESTIMATED,
                effective_date=dt.date(2026, 6, 1),
                created_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
                source=AnchorSource.ATHLETE,
            ),
        )
    }


# --- how a prescription reads -------------------------------------------------


def test_a_range_renders_with_an_en_dash_and_a_point_renders_as_one_number() -> None:
    # A hyphen reads as a minus sign next to numbers, and "88–88 % FTP" is
    # not something anyone writes.
    assert (
        render_target(
            PercentOfAnchor(anchor_type=AnchorType.FTP, pct_low=0.88, pct_high=0.93)
        )
        == "88–93 % FTP"
    )
    assert (
        render_target(
            PercentOfAnchor(anchor_type=AnchorType.FTP, pct_low=0.9, pct_high=0.9)
        )
        == "90 % FTP"
    )
    assert (
        render_target(AbsoluteRange(low=250, high=270, unit=ChannelUnit.WATT))
        == "250–270 W"
    )
    assert (
        render_target(AbsoluteRange(low=250, high=250, unit=ChannelUnit.WATT))
        == "250 W"
    )


def test_bounds_that_render_identically_collapse_to_a_point() -> None:
    # The collapse tests the rendered strings, not the floats. Two bounds that
    # differ in the seventh decimal are one prescription to every reader, and
    # comparing floats printed the "88–88 % FTP" the docstring above rules out.
    assert (
        render_target(
            PercentOfAnchor(
                anchor_type=AnchorType.FTP, pct_low=0.8800001, pct_high=0.8800002
            )
        )
        == "88 % FTP"
    )
    assert (
        render_target(
            AbsoluteRange(low=250.0000001, high=250.0000002, unit=ChannelUnit.WATT)
        )
        == "250 W"
    )


def test_an_anchor_is_named_the_way_an_athlete_writes_it() -> None:
    assert (
        render_target(
            PercentOfAnchor(anchor_type=AnchorType.MAX_HR, pct_low=0.8, pct_high=0.85)
        )
        == "80–85 % MAX HR"
    )


# --- what resolves and what does not ------------------------------------------


def test_a_percentage_resolves_against_the_pinned_version() -> None:
    resolved = resolve_target(
        Channel.POWER,
        PercentOfAnchor(anchor_type=AnchorType.FTP, pct_low=0.88, pct_high=0.93),
        pinned_ftp(250),
    )

    assert (resolved.resolved_low, resolved.resolved_high) == (220.0, 232.5)
    assert resolved.unit is ChannelUnit.WATT
    assert resolved.anchor_version_id == FTP_VERSION_ID


def test_an_unpinned_anchor_resolves_to_nothing_rather_than_zero() -> None:
    # Missing means "not resolved". A zero here would render as an easy step.
    resolved = resolve_target(
        Channel.HR,
        PercentOfAnchor(anchor_type=AnchorType.LTHR, pct_low=0.8, pct_high=0.85),
        pinned_ftp(),
    )

    assert resolved.prescribed == "80–85 % LTHR"
    assert resolved.resolved_low is None
    assert resolved.resolved_high is None
    assert resolved.anchor_version_id is None


def test_a_ramps_two_ends_resolve_separately() -> None:
    workout = EnduranceWorkout(
        steps=(
            RampStep(
                duration_s=600,
                start_targets={
                    Channel.POWER: PercentOfAnchor(
                        anchor_type=AnchorType.FTP, pct_low=0.5, pct_high=0.5
                    )
                },
                end_targets={
                    Channel.POWER: PercentOfAnchor(
                        anchor_type=AnchorType.FTP, pct_low=1.0, pct_high=1.0
                    )
                },
                role=StepRole.WARMUP,
                name="Build",
            ),
        )
    )

    (step,) = resolve_steps(workout, pinned_ftp(250))

    assert step.is_ramp is True
    assert step.name == "Build"
    assert step.start_targets[0].resolved_low == 125.0
    assert step.end_targets[0].resolved_low == 250.0


def test_a_steady_steps_two_ends_are_the_same_targets() -> None:
    workout = EnduranceWorkout(
        steps=(
            SteadyStep(
                duration_s=1_200,
                targets={
                    Channel.POWER: AbsoluteRange(
                        low=180, high=200, unit=ChannelUnit.WATT
                    )
                },
            ),
        )
    )

    (step,) = resolve_steps(workout, {})

    assert step.is_ramp is False
    assert step.start_targets == step.end_targets


def test_a_strength_prescription_resolves_to_no_steps() -> None:
    # Kilograms, reps and RPE are not anchor percentages; there is nothing to
    # resolve, and an empty tuple says so without refusing the call.
    workout = StrengthWorkout(
        groups=(
            StrengthGroup(
                items=(
                    StrengthSet(
                        exercise_id="back_squat",
                        sets=5,
                        reps=3,
                        load=Load(kind=LoadKind.KG, value=100),
                    ),
                )
            ),
        )
    )

    assert resolve_steps(workout, pinned_ftp()) == ()

"""Warm-up ramps: which lines get one, and what the ramp is.

The numbers are the athlete's first sets of the day, generated on the server
and carried to the phone in the session packet (`app.services.packet`), so the
phone only renders them. AC-45 states the two canonical ramps; the edge cases
pin the equipment split and the empty ramp.
"""

from typing import Any

import pytest

from app.domain.loading import DEFAULT_GYM, Equipment, loadable_weights
from app.domain.muscles import MuscleRegion
from app.domain.strength import (
    Exercise,
    ExerciseCategory,
    ExerciseEquipment,
    Load,
    LoadKind,
    LoadType,
    Mechanic,
    MovementPattern,
    StrengthGroup,
    StrengthSet,
    StrengthWorkout,
    WarmupMode,
)
from app.domain.warmups import WarmupSet, warmup_lines, warmup_sets

# --- AC-45: the ramp -------------------------------------------------------------


def as_pairs(ramp: tuple[WarmupSet, ...]) -> list[tuple[float, int]]:
    return [(step.kg, step.reps) for step in ramp]


def test_a_100_kg_squat_ramps_bar_40_60_80() -> None:
    assert as_pairs(warmup_sets(100.0)) == [(20.0, 10), (40.0, 5), (60.0, 3), (80.0, 2)]


def test_a_40_kg_line_ramps_bar_then_30_for_5_only() -> None:
    assert as_pairs(warmup_sets(40.0)) == [(20.0, 10), (30.0, 5)]


@pytest.mark.parametrize(
    "working_kg", [22.5, 27.5, 40.0, 52.5, 61.25, 77.5, 100.0, 143.75, 180.0, 250.0]
)
def test_every_warm_up_weight_is_loadable_and_lighter_than_the_work(
    working_kg: float,
) -> None:
    loadable = loadable_weights(Equipment.BARBELL)
    ramp = warmup_sets(working_kg)
    for step in ramp:
        assert step.kg in loadable
        assert step.kg < working_kg
    weights = [step.kg for step in ramp]
    assert weights == sorted(set(weights)), "a ramp climbs, never repeats"


def test_a_working_weight_equal_to_the_bar_has_no_warm_ups() -> None:
    assert warmup_sets(DEFAULT_GYM.bar_kg) == ()


def test_a_working_weight_below_the_bar_has_no_warm_ups() -> None:
    # 15 kg rounds to the empty bar: still nothing lighter to warm up with.
    assert warmup_sets(15.0) == ()


def test_a_dumbbell_line_ramps_in_rack_steps_without_a_bar() -> None:
    ramp = warmup_sets(20.0, Equipment.DUMBBELL)

    assert as_pairs(ramp) == [(8.0, 5), (12.0, 3), (16.0, 2)]
    rack = loadable_weights(Equipment.DUMBBELL)
    assert all(step.kg in rack for step in ramp)
    assert DEFAULT_GYM.bar_kg not in [step.kg for step in ramp]


def test_a_stack_line_ramps_in_pin_steps() -> None:
    ramp = warmup_sets(50.0, Equipment.STACK)

    assert as_pairs(ramp) == [(20.0, 5), (30.0, 3), (40.0, 2)]


def test_the_lightest_dumbbell_has_no_warm_ups() -> None:
    assert warmup_sets(DEFAULT_GYM.dumbbell_step_kg, Equipment.DUMBBELL) == ()


# --- which lines get a ramp --------------------------------------------------------


def entry(slug: str, **overrides: Any) -> Exercise:
    fields: dict[str, Any] = {
        "id": slug,
        "name": slug.replace("_", " ").title(),
        "category": ExerciseCategory.SQUAT,
        "muscles": {MuscleRegion.QUADS_VASTI: 1.0},
        "mechanic": Mechanic.MULTI_JOINT,
        "pattern": MovementPattern.SQUAT,
        "equipment": frozenset({ExerciseEquipment.BARBELL}),
        "load_type": LoadType.BARBELL,
        "source": "arc",
    }
    fields.update(overrides)
    return Exercise(**fields)


CATALOGUE = {
    "back_squat": entry("back_squat"),
    "front_squat": entry("front_squat"),
    "bench_press": entry(
        "bench_press",
        category=ExerciseCategory.PRESS,
        pattern=MovementPattern.HORIZONTAL_PUSH,
        muscles={MuscleRegion.PEC_STERNAL: 1.0},
    ),
    "leg_extension": entry(
        "leg_extension",
        mechanic=Mechanic.SINGLE_JOINT,
        pattern=MovementPattern.KNEE_EXTENSION,
        load_type=LoadType.STACK,
        equipment=frozenset({ExerciseEquipment.MACHINE}),
    ),
}


def line(slug: str, **overrides: Any) -> StrengthSet:
    fields: dict[str, Any] = {
        "exercise_id": slug,
        "sets": 3,
        "reps": 5,
        "load": Load(LoadKind.KG, 100.0),
    }
    fields.update(overrides)
    return StrengthSet(**fields)


def workout(*lines: StrengthSet) -> StrengthWorkout:
    return StrengthWorkout(groups=tuple(StrengthGroup(items=(item,)) for item in lines))


def test_unset_warmup_ramps_only_the_first_main_lift_of_each_pattern() -> None:
    session = workout(
        line("back_squat"),
        line("front_squat"),
        line("bench_press"),
        line("leg_extension"),
    )

    assert warmup_lines(session, CATALOGUE) == (True, False, True, False)


def test_auto_ramps_the_line_it_is_written_on() -> None:
    session = workout(
        line("back_squat", warmup=WarmupMode.AUTO),
        line("leg_extension", warmup=WarmupMode.AUTO),
    )

    assert warmup_lines(session, CATALOGUE) == (True, True)


def test_none_switches_the_ramp_off_even_on_the_main_lift() -> None:
    session = workout(line("back_squat", warmup=WarmupMode.NONE), line("front_squat"))

    # `none` is about this line; the pattern's main lift is still the squat
    # (resolve_main_lines), so the front squat does not inherit its ramp.
    assert warmup_lines(session, CATALOGUE) == (False, False)


def test_a_second_explicit_main_lift_of_one_pattern_gets_no_default_ramp() -> None:
    session = workout(line("back_squat", main=True), line("front_squat", main=True))

    assert warmup_lines(session, CATALOGUE) == (True, False)

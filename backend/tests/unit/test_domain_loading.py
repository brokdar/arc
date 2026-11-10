"""Loading arithmetic: what weight can actually go on the bar, rack or pin.

Every number here is one the athlete will see on the phone in the gym, and
the phone computes it offline from `frontend/lib/strength` — which is checked
against vectors this module emits. So these tests pin the Python side to the
criteria; `test_loading_vectors.py` pins the TypeScript side to Python.
"""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.loading import (
    DEFAULT_GYM,
    KG_PER_LB,
    MAX_PERCENT_OF_SET,
    Equipment,
    Gym,
    LoadingError,
    kg_to_lb,
    lb_to_kg,
    loadable_weights,
    loading_for,
    nearest_loadable,
    percent_of_set,
    plates_per_side,
    starting_step,
    step_down,
)

# --- AC-1: nearest loadable weight and the plate breakdown --------------------


def test_nearest_loadable_rounds_to_the_closer_barbell_weight() -> None:
    assert nearest_loadable(101.3) == 102.5
    assert nearest_loadable(101.2) == 100.0


def test_tie_rounds_down() -> None:
    # 101.25 sits exactly between 100.0 and 102.5. Rounding a tie up would
    # turn a capped increase into an uncapped one; down never overshoots.
    assert nearest_loadable(101.25) == 100.0


def test_plates_per_side_uses_the_fewest_plates() -> None:
    # 41.25 kg a side: 25 + 10 + 5 + 1.25 is four plates, 20 + 20 + 1.25 three.
    assert plates_per_side(102.5) == [20, 20, 1.25]


def test_a_target_below_the_empty_bar_returns_the_bar() -> None:
    assert nearest_loadable(12.0) == 20.0
    assert nearest_loadable(0.0) == 20.0
    assert plates_per_side(20.0) == []
    assert plates_per_side(12.0) == []


def test_a_target_above_the_maximum_returns_the_maximum_and_capped() -> None:
    heaviest = loadable_weights(Equipment.BARBELL)[-1]
    result = loading_for(heaviest + 50.0)
    assert result.kg == heaviest
    assert result.capped is True
    assert nearest_loadable(heaviest + 50.0) == heaviest


def test_a_reachable_target_is_not_capped() -> None:
    result = loading_for(101.3)
    assert result.kg == 102.5
    assert result.capped is False
    assert result.plates_per_side == (20, 20, 1.25)


def test_the_exact_maximum_is_not_capped() -> None:
    heaviest = loadable_weights(Equipment.BARBELL)[-1]
    assert loading_for(heaviest).capped is False


def test_dumbbells_return_the_nearest_rack_value_and_no_plates() -> None:
    assert nearest_loadable(13.1, Equipment.DUMBBELL) == 14.0
    assert nearest_loadable(12.9, Equipment.DUMBBELL) == 12.0
    assert nearest_loadable(13.0, Equipment.DUMBBELL) == 12.0  # tie rounds down
    assert nearest_loadable(0.5, Equipment.DUMBBELL) == 2.0
    result = loading_for(13.1, Equipment.DUMBBELL)
    assert result.kg == 14.0
    assert result.plates_per_side is None
    with pytest.raises(LoadingError, match="dumbbell"):
        plates_per_side(14.0, Equipment.DUMBBELL)


def test_a_stack_returns_the_nearest_pin_and_no_plates() -> None:
    assert nearest_loadable(37.4, Equipment.STACK) == 35.0
    assert nearest_loadable(37.6, Equipment.STACK) == 40.0
    assert nearest_loadable(1.0, Equipment.STACK) == 5.0
    result = loading_for(1000.0, Equipment.STACK)
    assert result.kg == DEFAULT_GYM.stack_max_kg
    assert result.capped is True
    assert result.plates_per_side is None
    with pytest.raises(LoadingError, match="stack"):
        plates_per_side(40.0, Equipment.STACK)


@pytest.mark.parametrize("bad", [-1.0, math.nan, math.inf])
def test_a_target_that_is_not_a_weight_is_refused(bad: float) -> None:
    with pytest.raises(LoadingError, match="target"):
        nearest_loadable(bad)


def test_default_gym_is_the_one_the_plan_names() -> None:
    assert DEFAULT_GYM.bar_kg == 20.0
    plates = [kg for kg, _ in DEFAULT_GYM.plate_pairs]
    assert min(plates) == 1.25
    assert max(plates) == 25.0
    assert DEFAULT_GYM.dumbbell_step_kg == 2.0
    assert DEFAULT_GYM.stack_step_kg == 5.0


def test_a_gym_with_no_plates_loads_only_the_bar() -> None:
    gym = Gym(
        bar_kg=15.0,
        plate_pairs=(),
        dumbbell_step_kg=2.5,
        dumbbell_max_kg=30.0,
        stack_step_kg=7.0,
        stack_max_kg=70.0,
    )
    assert nearest_loadable(60.0, gym=gym) == 15.0
    assert loading_for(60.0, gym=gym).capped is True
    assert nearest_loadable(8.0, Equipment.DUMBBELL, gym) == 7.5


def test_an_invalid_gym_is_refused() -> None:
    with pytest.raises(LoadingError, match="plate"):
        Gym(
            bar_kg=20.0,
            plate_pairs=((0.0, 1),),
            dumbbell_step_kg=2.0,
            dumbbell_max_kg=50.0,
            stack_step_kg=5.0,
            stack_max_kg=100.0,
        )
    with pytest.raises(LoadingError, match="bar"):
        Gym(
            bar_kg=-1.0,
            plate_pairs=(),
            dumbbell_step_kg=2.0,
            dumbbell_max_kg=50.0,
            stack_step_kg=5.0,
            stack_max_kg=100.0,
        )


@given(st.floats(min_value=0.0, max_value=600.0, allow_nan=False))
def test_the_answer_is_always_loadable_and_its_plates_add_up(target: float) -> None:
    kg = nearest_loadable(target)
    assert kg in loadable_weights(Equipment.BARBELL)
    assert DEFAULT_GYM.bar_kg + 2 * sum(plates_per_side(kg)) == pytest.approx(kg)


# --- AC-2: the starting weight from the first-set answer -----------------------


def test_starting_step_easy_adds_a_fifth() -> None:
    assert starting_step(20.0, "easy") == 25.0


def test_starting_step_good_adds_a_tenth() -> None:
    assert starting_step(40.0, "good") == 45.0


def test_starting_step_hard_steps_down() -> None:
    assert starting_step(40.0, "hard") == 37.5


def test_starting_step_hard_at_the_empty_bar_stays_at_the_bar() -> None:
    assert starting_step(20.0, "hard") == 20.0


def test_an_increase_that_rounds_to_the_current_weight_stays() -> None:
    # +10 % of 2 kg is 2.2 kg, and the rack's nearest is still 2 kg: no bump.
    assert starting_step(2.0, "good", Equipment.DUMBBELL) == 2.0


def test_an_unknown_answer_raises_naming_it() -> None:
    with pytest.raises(LoadingError, match="'meh'"):
        starting_step(40.0, "meh")


# --- AC-3: step-down, percent of a set, units -----------------------------------


def test_step_down_goes_one_loadable_weight_lower() -> None:
    assert step_down(102.5) == 100.0
    assert step_down(14.0, Equipment.DUMBBELL) == 12.0
    assert step_down(40.0, Equipment.STACK) == 35.0


def test_step_down_at_the_bar_stays_at_the_bar() -> None:
    assert step_down(20.0) == 20.0
    assert step_down(2.0, Equipment.DUMBBELL) == 2.0


def test_percent_of_set_rounds_to_loadable() -> None:
    assert percent_of_set(102.5, 0.925) == 95.0


@pytest.mark.parametrize("bad", [0.0, -0.5, 1.5000001, 2.0, math.nan])
def test_percent_zero_or_above_the_bound_is_refused(bad: float) -> None:
    with pytest.raises(LoadingError, match="fraction"):
        percent_of_set(102.5, bad)


def test_percent_at_the_bound_is_accepted() -> None:
    assert MAX_PERCENT_OF_SET == 1.5
    assert percent_of_set(100.0, 1.5) == 150.0


def test_kg_lb_factor_is_the_international_pound() -> None:
    assert KG_PER_LB == 0.45359237
    assert lb_to_kg(1.0) == 0.45359237
    assert kg_to_lb(0.45359237) == 1.0


@given(st.floats(min_value=0.0, max_value=2000.0, allow_nan=False))
def test_conversions_round_trip(value: float) -> None:
    assert abs(lb_to_kg(kg_to_lb(value)) - value) <= 1e-9
    assert abs(kg_to_lb(lb_to_kg(value)) - value) <= 1e-9

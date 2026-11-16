"""`app.domain.swaps`: which exercises stand in for another, in what order.

The golden cases run over the bundled catalogue (`load_exercise_catalogue`),
the same snapshot every deployment seeds, so a catalogue edit that changes
what the gym offers for a back squat fails here and is looked at, rather than
changing the gym floor silently.
"""

import pytest

from app.domain.muscles import MuscleGroup, main_groups
from app.domain.strength import (
    Exercise,
    ExerciseEquipment,
    MovementPattern,
    Restriction,
)
from app.domain.swaps import (
    DEFAULT_GYM_EQUIPMENT,
    SWAP_COUNT,
    SwapContext,
    ease_back_ladder,
    is_valid_substitute,
    pain_swap,
    restrictions_from_pain,
    substitutes,
)
from app.services.templates import load_exercise_catalogue

CATALOGUE = {exercise.id: exercise for exercise in load_exercise_catalogue()}
LIBRARY = tuple(CATALOGUE.values())
SQUAT = CATALOGUE["back_squat"]

#: The default gym with the rack taken: AC-75's case.
NO_RACK = DEFAULT_GYM_EQUIPMENT - {ExerciseEquipment.RACK}

#: What the ranking offers for a back squat with the rack taken, pinned.
#: All three are lunges because the lunges load both of the squat's main
#: groups (glutes and quads) where a goblet squat or leg press loads one, and
#: main-group overlap is the ranking's first key; none of them adds a demand
#: (restriction tag) the back squat does not already make. Among the lunges
#: the most shared muscle weight decides, so the Bulgarian split squat leads.
GOLDEN_NO_RACK = ("bulgarian_split_squat", "forward_lunge", "reverse_lunge")


def ids(exercises: tuple[Exercise, ...] | list[Exercise]) -> list[str]:
    return [exercise.id for exercise in exercises]


# --- AC-75 -------------------------------------------------------------------------


def test_back_squat_without_the_rack_offers_three_quad_squats_or_lunges() -> None:
    context = SwapContext(equipment=NO_RACK)

    offered = substitutes(SQUAT, LIBRARY, context)

    assert ids(offered) == list(GOLDEN_NO_RACK)
    assert len(offered) == SWAP_COUNT
    for exercise in offered:
        assert MuscleGroup.QUADS in main_groups(exercise.muscles), exercise.id
        assert exercise.pattern in {MovementPattern.SQUAT, MovementPattern.LUNGE}
        assert ExerciseEquipment.RACK not in exercise.equipment


def test_avoided_and_restricted_exercises_are_never_offered() -> None:
    avoided = frozenset({"bulgarian_split_squat"})
    # `unsupported_single_leg` is a restriction the athlete has.
    restrictions = frozenset({Restriction.UNSUPPORTED_SINGLE_LEG})
    context = SwapContext(equipment=NO_RACK, avoided=avoided, restrictions=restrictions)

    offered = substitutes(SQUAT, LIBRARY, context)

    assert len(offered) == SWAP_COUNT
    for exercise in offered:
        assert exercise.id not in avoided
        assert not exercise.restrictions & restrictions, exercise.id
        assert MuscleGroup.QUADS in main_groups(exercise.muscles)
        assert exercise.pattern in {MovementPattern.SQUAT, MovementPattern.LUNGE}
    assert ids(offered) == ["forward_lunge", "reverse_lunge", "split_squat"]


def test_a_remembered_pick_ranks_first() -> None:
    context = SwapContext(equipment=NO_RACK, remembered={"back_squat": "leg_press"})

    offered = substitutes(SQUAT, LIBRARY, context)

    assert ids(offered) == ["leg_press", "bulgarian_split_squat", "forward_lunge"]


def test_clearing_the_remembered_pick_restores_the_computed_order() -> None:
    remembered = substitutes(
        SQUAT,
        LIBRARY,
        SwapContext(equipment=NO_RACK, remembered={"back_squat": "leg_press"}),
    )
    cleared = substitutes(SQUAT, LIBRARY, SwapContext(equipment=NO_RACK))

    assert ids(remembered)[0] == "leg_press"
    assert ids(cleared) == list(GOLDEN_NO_RACK)


def test_a_remembered_pick_that_is_no_longer_valid_is_not_offered() -> None:
    # Remembered, then avoided: the avoid list wins, and the order is computed.
    context = SwapContext(
        equipment=NO_RACK,
        remembered={"back_squat": "leg_press"},
        avoided=frozenset({"leg_press"}),
    )

    assert ids(substitutes(SQUAT, LIBRARY, context)) == list(GOLDEN_NO_RACK)


def test_an_exercise_with_no_valid_alternative_offers_fewer_never_an_invalid_one() -> (
    None
):
    # Nothing else in the catalogue is an anti-lateral-flexion move for
    # adductors: the Copenhagen plank has no stand-in at all.
    copenhagen = CATALOGUE["copenhagen_plank"]
    assert substitutes(copenhagen, LIBRARY) == ()

    # A back squat for someone who may not bend the knee deeply, with no rack:
    # only movements without that demand qualify, and there are fewer than three.
    context = SwapContext(
        equipment=NO_RACK, restrictions=frozenset({Restriction.DEEP_KNEE_FLEXION})
    )
    offered = substitutes(SQUAT, LIBRARY, context)
    assert 0 < len(offered) < SWAP_COUNT
    for exercise in offered:
        assert is_valid_substitute(SQUAT, exercise, context)
        assert Restriction.DEEP_KNEE_FLEXION not in exercise.restrictions


def test_equipment_the_gym_lacks_rules_a_movement_out() -> None:
    bodyweight_only = frozenset({ExerciseEquipment.BODYWEIGHT})
    context = SwapContext(equipment=bodyweight_only)

    offered = substitutes(SQUAT, LIBRARY, context)

    assert offered
    assert all(e.equipment == {ExerciseEquipment.BODYWEIGHT} for e in offered)


def test_less_often_sinks_below_every_other_valid_swap() -> None:
    context = SwapContext(
        equipment=NO_RACK, less_often=frozenset({"bulgarian_split_squat"})
    )

    offered = substitutes(SQUAT, LIBRARY, context, limit=None)

    assert offered[-1].id == "bulgarian_split_squat"
    assert ids(offered[:3]) == ["forward_lunge", "reverse_lunge", "split_squat"]


def test_a_known_exercise_wins_a_tie() -> None:
    plain = substitutes(SQUAT, LIBRARY, SwapContext(equipment=NO_RACK))
    known = substitutes(
        SQUAT,
        LIBRARY,
        SwapContext(equipment=NO_RACK, known=frozenset({"reverse_lunge"})),
    )

    assert ids(plain) == list(GOLDEN_NO_RACK)
    # The reverse lunge ties the two ahead of it on every key before "known"
    # (both lunges, no new demand), so having logged it moves it past both.
    assert ids(known)[0] == "reverse_lunge"


def test_the_ranking_is_deterministic_whatever_the_catalogue_order() -> None:
    context = SwapContext(equipment=NO_RACK)
    forward = substitutes(SQUAT, LIBRARY, context, limit=None)
    backward = substitutes(SQUAT, tuple(reversed(LIBRARY)), context, limit=None)

    assert ids(forward) == ids(backward)


def test_the_exercise_itself_is_never_its_own_swap() -> None:
    context = SwapContext(remembered={"back_squat": "back_squat"})

    assert "back_squat" not in ids(substitutes(SQUAT, LIBRARY, context, limit=None))


# --- the pain card -----------------------------------------------------------------


def test_the_pain_swap_spares_the_painful_joint() -> None:
    lunge = CATALOGUE["forward_lunge"]

    swap = pain_swap(lunge, LIBRARY)

    assert swap is not None
    assert swap.id == "step_up"
    assert Restriction.DEEP_KNEE_FLEXION not in swap.restrictions


def test_the_pain_swap_honours_the_avoid_list() -> None:
    lunge = CATALOGUE["forward_lunge"]

    swap = pain_swap(lunge, LIBRARY, SwapContext(avoided=frozenset({"step_up"})))

    assert swap is None or swap.id != "step_up"


def test_restrictions_come_from_the_joints_sharp_pain_was_reported_on() -> None:
    assert restrictions_from_pain([CATALOGUE["forward_lunge"]]) == frozenset(
        {Restriction.DEEP_KNEE_FLEXION}
    )
    assert restrictions_from_pain([CATALOGUE["barbell_curl"]]) == frozenset()


# --- ease back in ------------------------------------------------------------------


def test_ease_back_in_climbs_from_easiest_to_the_exercise_itself() -> None:
    ladder = ease_back_ladder(SQUAT, LIBRARY)

    assert 0 < len(ladder) <= SWAP_COUNT
    assert "back_squat" not in ids(ladder)
    for step in ladder:
        # Each step makes no demand the squat itself does not.
        assert step.restrictions <= SQUAT.restrictions
        assert step.restrictions != SQUAT.restrictions or step.load_type != (
            SQUAT.load_type
        )
    demands = [len(step.restrictions) for step in ladder]
    assert demands == sorted(demands)


def test_ease_back_in_never_names_an_avoided_exercise() -> None:
    plain = ease_back_ladder(SQUAT, LIBRARY)
    avoided = frozenset({plain[0].id})

    ladder = ease_back_ladder(SQUAT, LIBRARY, SwapContext(avoided=avoided))

    assert plain[0].id not in ids(ladder)


@pytest.mark.parametrize("exercise_id", ["air_squat", "copenhagen_plank"])
def test_ease_back_in_for_the_easiest_movement_is_empty(exercise_id: str) -> None:
    assert ease_back_ladder(CATALOGUE[exercise_id], LIBRARY) == ()

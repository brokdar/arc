"""Strength prescriptions: loads, sets, supersets, and the bundled catalogue."""

import json
from pathlib import Path
from typing import Any

import pytest

from app.domain.activity import SetSide
from app.domain.athlete import Discipline
from app.domain.metrics import PerformedSet, StrengthVolume, strength_volume
from app.domain.strength import (
    SLUG_PATTERN,
    Exercise,
    ExerciseCategory,
    Load,
    LoadKind,
    StrengthGroup,
    StrengthSet,
    StrengthWorkout,
    check_sidedness,
    exercise_ids,
    logged_set_units,
    logged_sides_worked,
    parse_catalogue,
    strength_workout_from_json,
    strength_workout_to_json,
)
from app.domain.workout import workout_body_from_json, workout_body_to_json
from app.services.templates import EXERCISE_CATALOGUE_FILE
from tests.unit.prescriptions import LIFT, bodyweight


def squat(**overrides: object) -> StrengthSet:
    """A back-squat prescription with everything but the overrides fixed."""
    fields: dict[str, object] = {
        "exercise_id": "back_squat",
        "sets": 5,
        "reps": 3,
        "load": Load(LoadKind.PERCENT_E1RM, 0.85),
        "rir": 2,
        "rest_s": 180,
    } | overrides
    return StrengthSet(**fields)  # type: ignore[arg-type]


def workout() -> StrengthWorkout:
    """A max-strength session with one straight lift and one superset."""
    return StrengthWorkout(
        groups=(
            StrengthGroup(items=(squat(),)),
            StrengthGroup(
                items=(
                    StrengthSet(
                        exercise_id="romanian_deadlift",
                        sets=3,
                        reps=8,
                        load=Load(LoadKind.KG, 80),
                    ),
                    StrengthSet(
                        exercise_id="front_plank",
                        sets=3,
                        duration_s=45,
                        load=Load.bodyweight(),
                    ),
                ),
                label="B1/B2",
            ),
        )
    )


# --- shape --------------------------------------------------------------------


def test_a_group_of_one_is_not_a_superset() -> None:
    assert not StrengthGroup(items=(squat(),)).is_superset


def test_a_group_of_more_than_one_is_a_superset() -> None:
    # Supersets are grouping, exactly as the build plan asks: there is no flag
    # to keep in step with the item count.
    assert workout().groups[1].is_superset


def test_prescriptions_flatten_in_execution_order() -> None:
    assert [item.exercise_id for item in workout().prescriptions] == [
        "back_squat",
        "romanian_deadlift",
        "front_plank",
    ]


def test_total_sets_counts_every_line() -> None:
    assert workout().total_sets == 5 + 3 + 3


def test_a_bilateral_line_works_the_rounds_it_prescribes() -> None:
    line = squat(sets=3, reps=8)

    assert line.working_sets == 3.0
    assert type(line.working_sets) is float
    assert line.total_reps == 24


def test_pair_counts_once() -> None:
    """AC-26: a left-plus-right pair is one set; its reps count both sides.

    Displaces "one per-side round is two working sets": a left and a right set
    of split squats inflated completion and volume counts to twice what the
    athlete would call the session, and per-muscle set counting cannot be
    honest on top of that. Reps and kilograms are still worked by both legs,
    so `total_reps` keeps counting both sides — 3 × 8 per side is 48 reps.
    """
    line = StrengthSet(
        exercise_id="bulgarian_split_squat",
        sets=3,
        reps=8,
        per_side=True,
        load=Load(LoadKind.KG, 30.0),
    )

    assert line.sets == 3
    assert line.working_sets == 3.0
    assert type(line.working_sets) is float
    assert line.total_reps == 48


def test_logged_side_rows_count_as_pairs() -> None:
    """AC-26: L8 R8 three times is 3.0 sets; one more lone left makes 3.5."""
    pairs = [
        PerformedSet(reps=8, load_kg=30.0, side=side)
        for _ in range(3)
        for side in (SetSide.LEFT, SetSide.RIGHT)
    ]

    session = strength_volume(pairs)
    assert isinstance(session, StrengthVolume)
    assert session.sets_completed == 3.0
    assert type(session.sets_completed) is float
    # Every one of the six rows lifted 8 × 30 kg with one leg: the volume is
    # the sum of the rows, not of the pairs.
    assert session.volume_load_kg == 6 * 8 * 30.0

    longer = strength_volume(
        [*pairs, PerformedSet(reps=8, load_kg=30.0, side=SetSide.LEFT)]
    )
    assert isinstance(longer, StrengthVolume)
    assert longer.sets_completed == 3.5
    assert longer.volume_load_kg == 7 * 8 * 30.0


def test_a_legacy_per_side_row_is_one_set_with_its_reps_twice() -> None:
    """A `per_side` row stored before `side` existed is a whole pair.

    It was backfilled to `side = both` (migration 0026): the row says "8 each
    side" in one line, so it is one set — the same as an L row plus an R row —
    and its reps reach the volume load twice, once per limb.
    """
    session = strength_volume([PerformedSet(reps=8, load_kg=30.0, per_side=True)])

    assert isinstance(session, StrengthVolume)
    assert session.sets_completed == 1.0
    assert session.volume_load_kg == 2 * 8 * 30.0
    assert logged_set_units(SetSide.BOTH) == 1.0
    assert logged_sides_worked(per_side=True, side=SetSide.BOTH) == 2


def test_a_lone_side_is_half_a_set_worked_by_one_limb() -> None:
    for side in (SetSide.LEFT, SetSide.RIGHT):
        assert logged_set_units(side) == 0.5
        assert logged_sides_worked(per_side=False, side=side) == 1
    assert logged_set_units(SetSide.BOTH) == 1.0
    assert logged_sides_worked(per_side=False, side=SetSide.BOTH) == 1


def test_a_movement_that_is_not_unilateral_refuses_one_side() -> None:
    bench = Exercise(
        id="bench_press", name="Bench Press", category=ExerciseCategory.PRESS
    )

    with pytest.raises(ValueError, match="both sides at once"):
        check_sidedness(bench.unilateral, per_side=False, side=SetSide.LEFT)
    with pytest.raises(ValueError, match="both sides at once"):
        check_sidedness(bench.unilateral, per_side=True, side=SetSide.BOTH)
    check_sidedness(bench.unilateral, per_side=False, side=SetSide.BOTH)


def test_a_row_is_one_side_or_a_pair_never_both() -> None:
    # `per_side` says the row holds both sides; `left` says it holds one. The
    # two together have no count to give, so the write is refused rather than
    # one of them silently winning.
    with pytest.raises(ValueError, match="one side or a pair"):
        check_sidedness(True, per_side=True, side=SetSide.LEFT)
    # A free-text movement has no catalogue opinion, but the rule still holds.
    with pytest.raises(ValueError, match="one side or a pair"):
        check_sidedness(None, per_side=True, side=SetSide.RIGHT)
    check_sidedness(None, per_side=False, side=SetSide.RIGHT)


def test_total_sets_counts_pairs_not_limbs() -> None:
    per_side = StrengthWorkout(
        groups=(
            StrengthGroup(
                items=(
                    StrengthSet(
                        exercise_id="split_squat",
                        sets=4,
                        reps=8,
                        per_side=True,
                        load=Load.bodyweight(),
                    ),
                )
            ),
        )
    )

    assert per_side.total_sets == 4.0
    assert type(per_side.total_sets) is float


# --- reps or a hold, never both -----------------------------------------------


def test_a_timed_hold_carries_seconds_and_no_rep_count() -> None:
    plank = StrengthSet(
        exercise_id="front_plank", sets=3, duration_s=45, load=Load.bodyweight()
    )

    assert plank.reps is None
    assert plank.total_reps is None
    assert plank.total_hold_s == 135


def test_a_per_side_hold_holds_for_both_sides() -> None:
    side_plank = StrengthSet(
        exercise_id="side_plank",
        sets=2,
        duration_s=30,
        per_side=True,
        load=Load.bodyweight(),
    )

    # Two pairs, and each of the four sides held for thirty seconds.
    assert side_plank.working_sets == 2.0
    assert side_plank.total_hold_s == 120


def test_a_rep_based_line_holds_for_no_seconds() -> None:
    assert squat().total_hold_s is None


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"reps": None}, id="neither"),
        pytest.param({"duration_s": 45}, id="both"),
    ],
)
def test_a_line_prescribes_reps_or_a_hold_and_not_the_other_combination(
    overrides: dict[str, object],
) -> None:
    # A 45-second plank written as `reps: 1` puts an invented number into
    # volume arithmetic; a line with neither is not a prescription at all.
    with pytest.raises(ValueError, match="exactly one of reps or duration_s"):
        squat(**overrides)


def test_an_implausible_hold_is_rejected() -> None:
    with pytest.raises(ValueError, match="duration_s must be between"):
        squat(reps=None, duration_s=100_000)


def test_the_exercises_a_workout_references_are_discoverable() -> None:
    # What the service checks against the catalogue before storing anything.
    assert exercise_ids(workout()) == frozenset(
        {"back_squat", "romanian_deadlift", "front_plank"}
    )


# --- loads --------------------------------------------------------------------


def test_a_bodyweight_load_carries_no_value() -> None:
    with pytest.raises(ValueError, match="carries no value"):
        Load(LoadKind.BODYWEIGHT, 60)


def test_every_other_load_kind_needs_one() -> None:
    with pytest.raises(ValueError, match="needs a value"):
        Load(LoadKind.KG)


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        (LoadKind.KG, 900.0),
        (LoadKind.PERCENT_E1RM, 12.0),
        (LoadKind.RPE, 30.0),
    ],
)
def test_implausible_loads_are_rejected(kind: LoadKind, value: float) -> None:
    # A percent_e1rm is a FRACTION, so 12 is not "12 %" — it is twelve times
    # the athlete's one-rep max, which is the typo this catches.
    with pytest.raises(ValueError, match="must be between"):
        Load(kind, value)


# --- prescription rules -------------------------------------------------------


def test_a_line_needs_an_exercise() -> None:
    with pytest.raises(ValueError, match="needs an exercise"):
        squat(exercise_id="  ")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("sets", 0, "sets must be between"),
        ("reps", 0, "reps must be between"),
        ("rir", 20, "rir must be between"),
        ("rest_s", 100_000, "rest_s must be between"),
    ],
)
def test_implausible_prescriptions_are_rejected(
    field: str, value: int, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        squat(**{field: value})


def test_a_group_and_a_workout_both_need_content() -> None:
    with pytest.raises(ValueError, match="at least one prescribed set"):
        StrengthGroup(items=())
    with pytest.raises(ValueError, match="at least one group"):
        StrengthWorkout(groups=())


# --- serialization ------------------------------------------------------------


def test_serialization_round_trips() -> None:
    assert strength_workout_from_json(strength_workout_to_json(workout())) == workout()


@pytest.mark.parametrize(
    "document",
    [
        pytest.param(LIFT, id="lift"),
        pytest.param(bodyweight(3), id="bodyweight"),
    ],
)
def test_a_prescription_stored_before_per_side_existed_still_parses(
    document: dict[str, Any],
) -> None:
    # AC-20. Every document already in the database has `reps` and neither
    # `per_side` nor `duration_s`, and the meaning it always had is a
    # bilateral, rep-based set.
    body = workout_body_from_json(document)

    assert isinstance(body, StrengthWorkout)
    assert all(not item.per_side for item in body.prescriptions)
    assert all(item.duration_s is None for item in body.prescriptions)


@pytest.mark.parametrize(
    "document",
    [
        pytest.param(LIFT, id="lift"),
        pytest.param(bodyweight(3), id="bodyweight"),
    ],
)
def test_re_serializing_an_old_document_does_not_grow_the_new_keys(
    document: dict[str, Any],
) -> None:
    # AC-20's second half, and the one a reader would not think to write: a
    # serializer that emits `"per_side": false` makes every stored
    # prescription's next diff a lie about what changed.
    body = workout_body_from_json(document)

    assert workout_body_to_json(body) == document


def test_the_new_fields_survive_a_round_trip_when_they_are_set() -> None:
    line = StrengthSet(
        exercise_id="single_arm_dumbbell_row",
        sets=3,
        reps=11,
        per_side=True,
        load=Load(LoadKind.KG, 15.0),
    )
    hold = StrengthSet(
        exercise_id="side_plank",
        sets=2,
        duration_s=30,
        per_side=True,
        load=Load.bodyweight(),
    )
    original = StrengthWorkout(groups=(StrengthGroup(items=(line, hold)),))

    document = strength_workout_to_json(original)

    assert document["groups"][0]["items"][0]["per_side"] is True
    assert "reps" not in document["groups"][0]["items"][1]
    assert strength_workout_from_json(document) == original


def test_the_body_envelope_names_the_strength_discipline() -> None:
    document = workout_body_to_json(workout())

    assert document["discipline"] == Discipline.STRENGTH.value
    assert workout_body_from_json(document) == workout()


def test_an_unknown_field_is_rejected_rather_than_ignored() -> None:
    with pytest.raises(ValueError, match="unknown field"):
        strength_workout_from_json(
            {
                "groups": [
                    {
                        "items": [
                            {
                                "exercise_id": "back_squat",
                                "sets": 3,
                                "reps": 5,
                                "load": {"kind": "bodyweight"},
                                "superset": True,
                            }
                        ]
                    }
                ]
            }
        )


def test_the_error_says_where_in_the_document_it_happened() -> None:
    # The message reaches the client verbatim as a 422, so it has to locate
    # the problem in a document that may be several levels deep.
    with pytest.raises(ValueError, match=r"groups\[0\].items\[0\].sets"):
        strength_workout_from_json(
            {
                "groups": [
                    {
                        "items": [
                            {
                                "exercise_id": "back_squat",
                                "sets": "five",
                                "reps": 5,
                                "load": {"kind": "bodyweight"},
                            }
                        ]
                    }
                ]
            }
        )


def test_a_semantic_failure_keeps_its_place_in_the_document() -> None:
    # `sets must be between 1 and 50` comes from `__post_init__`, which knows
    # the number and not where it came from — but the codec's contract is that
    # every message locates itself, and a prescription has many lines.
    with pytest.raises(ValueError, match=r"groups\[0\].items\[1\]: sets must be"):
        strength_workout_from_json(
            {
                "groups": [
                    {
                        "items": [
                            {
                                "exercise_id": "back_squat",
                                "sets": 3,
                                "reps": 5,
                                "load": {"kind": "bodyweight"},
                            },
                            {
                                "exercise_id": "front_plank",
                                "sets": 0,
                                "reps": 5,
                                "load": {"kind": "bodyweight"},
                            },
                        ]
                    }
                ]
            }
        )


# --- the catalogue ------------------------------------------------------------


def test_a_catalogue_entry_needs_a_slug_and_a_name() -> None:
    with pytest.raises(ValueError, match="lowercase slug"):
        Exercise(id="Back Squat", name="Back Squat", category=ExerciseCategory.SQUAT)
    with pytest.raises(ValueError, match="non-empty name"):
        Exercise(id="back_squat", name=" ", category=ExerciseCategory.SQUAT)


@pytest.mark.parametrize(
    "slug",
    ["back\tsquat", "back\nsquat", "Back_squat", "back squat", "_back_squat", "bäck"],
)
def test_a_slug_is_checked_against_its_shape_not_against_one_bad_character(
    slug: str,
) -> None:
    # A slug is part of the data contract — it sits inside stored
    # prescriptions and in URLs — so it is checked against what is allowed.
    # Spelling the rule as "no spaces" let a tab and a newline through.
    with pytest.raises(ValueError, match="lowercase slug"):
        Exercise(id=slug, name="Back Squat", category=ExerciseCategory.SQUAT)


def test_every_bundled_slug_satisfies_the_shape() -> None:
    catalogue = parse_catalogue(
        json.loads(Path(EXERCISE_CATALOGUE_FILE).read_text(encoding="utf-8"))
    )

    assert catalogue
    assert all(SLUG_PATTERN.fullmatch(exercise.id) for exercise in catalogue)


def test_a_duplicate_slug_is_fatal_rather_than_deduplicated() -> None:
    # Two entries claiming one slug makes every prescription referencing it
    # ambiguous, and the ambiguity would never surface on its own.
    with pytest.raises(ValueError, match="duplicate exercise id"):
        parse_catalogue(
            {
                "exercises": [
                    {"id": "back_squat", "name": "Back Squat", "category": "squat"},
                    {"id": "back_squat", "name": "Squat", "category": "squat"},
                ]
            }
        )


def test_an_empty_catalogue_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        parse_catalogue({"exercises": []})


def test_an_unknown_category_names_the_ones_that_exist() -> None:
    with pytest.raises(ValueError, match="is not one of: squat"):
        parse_catalogue({"exercises": [{"id": "x", "name": "X", "category": "biceps"}]})

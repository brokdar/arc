"""Strength prescriptions: loads, sets, supersets, and the bundled catalogue."""

import json
from pathlib import Path
from typing import Any

import pytest

from app.domain.athlete import Discipline
from app.domain.strength import (
    SLUG_PATTERN,
    Exercise,
    ExerciseCategory,
    Load,
    LoadKind,
    StrengthGroup,
    StrengthSet,
    StrengthWorkout,
    exercise_ids,
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
    assert squat(sets=3, reps=8).working_sets == 3
    assert squat(sets=3, reps=8).total_reps == 24


def test_a_per_side_round_is_two_working_sets() -> None:
    # Issue #25's own case, by number: three rounds of eleven single-arm reps
    # is six worked sets and 66 reps, not three and 33. `sets` stays what the
    # athlete wrote on the card.
    line = StrengthSet(
        exercise_id="single_arm_dumbbell_row",
        sets=3,
        reps=11,
        per_side=True,
        load=Load(LoadKind.KG, 15.0),
    )

    assert line.sets == 3
    assert line.working_sets == 6
    assert line.total_reps == 66


def test_total_sets_counts_working_sets_not_rounds() -> None:
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

    assert per_side.total_sets == 8


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

    assert side_plank.working_sets == 4
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

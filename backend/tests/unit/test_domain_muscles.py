"""The muscle taxonomy: 37 region keys, 16 display groups, the wellness bridge."""

from collections.abc import Sequence

import pytest

from app.domain.muscles import (
    GROUP_LABELS,
    JOINT,
    REGION_GROUP,
    WELLNESS_REGION_GROUPS,
    MuscleGroup,
    MuscleRegion,
    main_groups,
)
from app.domain.wellness import BodyRegion

#: Every region key ever released, in release order. **Append to this list;
#: never edit or delete a line.** A stored `{region: weight}` mapping, a later
#: coach proposal and the muscle map's SVG all name regions by these strings,
#: so a key that vanishes or changes spelling orphans data nobody can see.
COMMITTED_REGION_KEYS: tuple[str, ...] = (
    "pec_clavicular",
    "pec_sternal",
    "serratus_anterior",
    "delt_anterior",
    "delt_lateral",
    "delt_posterior",
    "rotator_cuff",
    "lats",
    "teres_major",
    "traps_upper",
    "traps_middle",
    "traps_lower",
    "rhomboids",
    "erector_spinae",
    "quadratus_lumborum",
    "biceps",
    "brachialis",
    "brachioradialis",
    "triceps_long",
    "triceps_lateral_medial",
    "forearm_flexors",
    "forearm_extensors",
    "rectus_abdominis",
    "obliques",
    "transversus_abdominis",
    "glute_max",
    "glute_med_min",
    "hip_flexors",
    "adductors",
    "tfl_abductors",
    "quads_rectus_femoris",
    "quads_vasti",
    "hamstrings",
    "gastrocnemius",
    "soleus",
    "tibialis_anterior",
    "neck",
)

#: The direction's 16 muscle groups (§2.3 glossary), word for word and in order.
DIRECTION_GROUPS: tuple[str, ...] = (
    "chest",
    "front shoulders",
    "side & rear shoulders",
    "upper back",
    "lats",
    "lower back",
    "biceps",
    "triceps",
    "forearms",
    "abs & obliques",
    "glutes",
    "quads",
    "hamstrings",
    "adductors",
    "calves",
    "neck",
)


def removed_keys(committed: Sequence[str], current: Sequence[str]) -> list[str]:
    """The committed keys ``current`` no longer has — a removal or a rename."""
    present = set(current)
    return [key for key in committed if key not in present]


# --- region keys -------------------------------------------------------------


def test_keys_append_only() -> None:
    # The binding check: every key ever released is still a member, spelled
    # the same. New keys may follow; none may go.
    current = [region.value for region in MuscleRegion]

    assert removed_keys(COMMITTED_REGION_KEYS, current) == []
    assert current[: len(COMMITTED_REGION_KEYS)] == list(COMMITTED_REGION_KEYS)


def test_there_are_37_region_keys() -> None:
    assert len(MuscleRegion) == 37
    assert len(set(COMMITTED_REGION_KEYS)) == len(COMMITTED_REGION_KEYS)


def test_adding_a_key_passes_the_append_only_check() -> None:
    grown = [*COMMITTED_REGION_KEYS, "pec_abdominal"]

    assert removed_keys(COMMITTED_REGION_KEYS, grown) == []


def test_removing_a_key_fails_the_append_only_check() -> None:
    shrunk = [key for key in COMMITTED_REGION_KEYS if key != "teres_major"]

    assert removed_keys(COMMITTED_REGION_KEYS, shrunk) == ["teres_major"]


def test_renaming_a_key_fails_the_append_only_check() -> None:
    renamed = [
        "upper_traps" if key == "traps_upper" else key for key in COMMITTED_REGION_KEYS
    ]

    assert removed_keys(COMMITTED_REGION_KEYS, renamed) == ["traps_upper"]


# --- groups ------------------------------------------------------------------


def test_the_16_group_names_equal_the_directions_list_exactly() -> None:
    assert len(MuscleGroup) == 16
    assert [GROUP_LABELS[group] for group in MuscleGroup] == list(DIRECTION_GROUPS)


def test_every_region_rolls_up_to_exactly_one_group() -> None:
    assert set(REGION_GROUP) == set(MuscleRegion)
    assert all(isinstance(group, MuscleGroup) for group in REGION_GROUP.values())


def test_every_group_has_at_least_one_region() -> None:
    assert set(REGION_GROUP.values()) == set(MuscleGroup)


@pytest.mark.parametrize(
    ("region", "group"),
    [
        (MuscleRegion.PEC_STERNAL, MuscleGroup.CHEST),
        (MuscleRegion.DELT_ANTERIOR, MuscleGroup.FRONT_SHOULDERS),
        (MuscleRegion.DELT_POSTERIOR, MuscleGroup.SIDE_REAR_SHOULDERS),
        (MuscleRegion.QUADS_VASTI, MuscleGroup.QUADS),
        (MuscleRegion.SOLEUS, MuscleGroup.CALVES),
        (MuscleRegion.TRAPS_UPPER, MuscleGroup.UPPER_BACK),
    ],
)
def test_regions_roll_up_where_an_athlete_would_look(
    region: MuscleRegion, group: MuscleGroup
) -> None:
    assert REGION_GROUP[region] is group


def test_main_groups_are_the_groups_of_full_weight_regions_only() -> None:
    # A bench press: chest is the main group; triceps and front shoulders help.
    bench = {
        MuscleRegion.PEC_STERNAL: 1.0,
        MuscleRegion.PEC_CLAVICULAR: 0.5,
        MuscleRegion.DELT_ANTERIOR: 0.5,
        MuscleRegion.TRICEPS_LATERAL_MEDIAL: 0.5,
    }

    assert main_groups(bench) == frozenset({MuscleGroup.CHEST})


# --- the wellness bridge -----------------------------------------------------


def test_every_wellness_region_maps_to_a_group_or_is_a_joint() -> None:
    assert set(WELLNESS_REGION_GROUPS) == {region.value for region in BodyRegion}
    for name, target in WELLNESS_REGION_GROUPS.items():
        if target == JOINT:
            continue
        assert isinstance(target, tuple), name
        assert target, name
        assert all(isinstance(group, MuscleGroup) for group in target), name


def test_knees_ankles_and_feet_are_joints_and_nothing_else_is() -> None:
    joints = {
        name for name, target in WELLNESS_REGION_GROUPS.items() if target == JOINT
    }

    assert joints == {"knees", "ankles", "feet"}


def test_every_group_is_reachable_from_some_wellness_region() -> None:
    # A soreness overlay that can never light a group up is a mapping hole.
    reached = {
        group
        for target in WELLNESS_REGION_GROUPS.values()
        if target != JOINT
        for group in target
    }

    assert reached == set(MuscleGroup)

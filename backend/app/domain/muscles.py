"""The muscle taxonomy: what an exercise trains, at two levels of detail.

Shared by the exercise library (which regions a movement loads), set counting
per muscle group, and the later muscle map — which is why it is a module of
its own rather than part of `app.domain.strength`, a prescription model.

Two levels:

* :class:`MuscleRegion` — 37 internal keys, the detail the library is tagged
  in and the Full-control map draws.
* :class:`MuscleGroup` — the 16 groups every athlete-facing count is shown
  in. Each region rolls up to exactly one group (:data:`REGION_GROUP`).

**Region keys are append-only.** A key is stored inside every exercise's
``{region: weight}`` mapping, will be named by coach proposals and by the
map's SVG, and is the string a later release reads back. Renaming one in
place would orphan all of that silently, so a new region is a new key added
at the end, and an old one is never removed or respelled. Free renames were
the alternative; they are refused by
``tests/unit/test_domain_muscles.py::test_keys_append_only``, which compares
the enum against a committed list of every key ever released.

The wellness soreness form keeps its own coarse :class:`~app.domain.wellness.
BodyRegion` (16 members, answered before coffee), untouched. The bridge
between the two is the declared table :data:`WELLNESS_REGION_GROUPS`, keyed by
the wellness *values* rather than the enum: importing wellness here would make
every module that reads the taxonomy a wellness importer, and the
import-linter contract keeps proposals and guardrails away from wellness.
`test_domain_muscles.py` pins the keys against ``BodyRegion``.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Final, Literal

#: The weight of a main muscle in an exercise's ``{region: weight}`` mapping.
#: Helpers are ``HELPER_WEIGHT``; any value in [0, 1] is legal, but only a
#: region at exactly this weight makes a group the exercise's *main* group.
MAIN_WEIGHT: Final = 1.0
#: The weight of a helper muscle — half, per the direction's counting rule.
HELPER_WEIGHT: Final = 0.5


class MuscleRegion(StrEnum):
    """One of the 37 internal muscle regions. Append-only — see the module."""

    PEC_CLAVICULAR = "pec_clavicular"
    PEC_STERNAL = "pec_sternal"
    SERRATUS_ANTERIOR = "serratus_anterior"
    DELT_ANTERIOR = "delt_anterior"
    DELT_LATERAL = "delt_lateral"
    DELT_POSTERIOR = "delt_posterior"
    ROTATOR_CUFF = "rotator_cuff"
    LATS = "lats"
    TERES_MAJOR = "teres_major"
    TRAPS_UPPER = "traps_upper"
    TRAPS_MIDDLE = "traps_middle"
    TRAPS_LOWER = "traps_lower"
    RHOMBOIDS = "rhomboids"
    ERECTOR_SPINAE = "erector_spinae"
    QUADRATUS_LUMBORUM = "quadratus_lumborum"
    BICEPS = "biceps"
    BRACHIALIS = "brachialis"
    BRACHIORADIALIS = "brachioradialis"
    TRICEPS_LONG = "triceps_long"
    TRICEPS_LATERAL_MEDIAL = "triceps_lateral_medial"
    FOREARM_FLEXORS = "forearm_flexors"
    FOREARM_EXTENSORS = "forearm_extensors"
    RECTUS_ABDOMINIS = "rectus_abdominis"
    OBLIQUES = "obliques"
    TRANSVERSUS_ABDOMINIS = "transversus_abdominis"
    GLUTE_MAX = "glute_max"
    GLUTE_MED_MIN = "glute_med_min"
    HIP_FLEXORS = "hip_flexors"
    ADDUCTORS = "adductors"
    TFL_ABDUCTORS = "tfl_abductors"
    QUADS_RECTUS_FEMORIS = "quads_rectus_femoris"
    QUADS_VASTI = "quads_vasti"
    HAMSTRINGS = "hamstrings"
    GASTROCNEMIUS = "gastrocnemius"
    SOLEUS = "soleus"
    TIBIALIS_ANTERIOR = "tibialis_anterior"
    NECK = "neck"


class MuscleGroup(StrEnum):
    """One of the 16 groups counts and the map are shown in.

    Member order is the direction's order, which is also display order;
    :data:`GROUP_LABELS` holds the words an athlete reads.
    """

    CHEST = "chest"
    FRONT_SHOULDERS = "front_shoulders"
    SIDE_REAR_SHOULDERS = "side_rear_shoulders"
    UPPER_BACK = "upper_back"
    LATS = "lats"
    LOWER_BACK = "lower_back"
    BICEPS = "biceps"
    TRICEPS = "triceps"
    FOREARMS = "forearms"
    ABS_OBLIQUES = "abs_obliques"
    GLUTES = "glutes"
    QUADS = "quads"
    HAMSTRINGS = "hamstrings"
    ADDUCTORS = "adductors"
    CALVES = "calves"
    NECK = "neck"


#: The direction's display name of each group, word for word.
GROUP_LABELS: Final[Mapping[MuscleGroup, str]] = {
    MuscleGroup.CHEST: "chest",
    MuscleGroup.FRONT_SHOULDERS: "front shoulders",
    MuscleGroup.SIDE_REAR_SHOULDERS: "side & rear shoulders",
    MuscleGroup.UPPER_BACK: "upper back",
    MuscleGroup.LATS: "lats",
    MuscleGroup.LOWER_BACK: "lower back",
    MuscleGroup.BICEPS: "biceps",
    MuscleGroup.TRICEPS: "triceps",
    MuscleGroup.FOREARMS: "forearms",
    MuscleGroup.ABS_OBLIQUES: "abs & obliques",
    MuscleGroup.GLUTES: "glutes",
    MuscleGroup.QUADS: "quads",
    MuscleGroup.HAMSTRINGS: "hamstrings",
    MuscleGroup.ADDUCTORS: "adductors",
    MuscleGroup.CALVES: "calves",
    MuscleGroup.NECK: "neck",
}

#: Which group each region counts toward.
#:
#: Two placements are judgement calls, recorded here because they decide what
#: a group filter returns: **hip flexors count as quads** (the rectus femoris
#: is itself a hip flexor, and there is no hip group among the 16), and
#: **upper traps count as upper back** rather than neck (they move the shoulder
#: blade; "neck" is the neck's own flexors and extensors).
REGION_GROUP: Final[Mapping[MuscleRegion, MuscleGroup]] = {
    MuscleRegion.PEC_CLAVICULAR: MuscleGroup.CHEST,
    MuscleRegion.PEC_STERNAL: MuscleGroup.CHEST,
    MuscleRegion.SERRATUS_ANTERIOR: MuscleGroup.CHEST,
    MuscleRegion.DELT_ANTERIOR: MuscleGroup.FRONT_SHOULDERS,
    MuscleRegion.DELT_LATERAL: MuscleGroup.SIDE_REAR_SHOULDERS,
    MuscleRegion.DELT_POSTERIOR: MuscleGroup.SIDE_REAR_SHOULDERS,
    MuscleRegion.ROTATOR_CUFF: MuscleGroup.SIDE_REAR_SHOULDERS,
    MuscleRegion.LATS: MuscleGroup.LATS,
    MuscleRegion.TERES_MAJOR: MuscleGroup.LATS,
    MuscleRegion.TRAPS_UPPER: MuscleGroup.UPPER_BACK,
    MuscleRegion.TRAPS_MIDDLE: MuscleGroup.UPPER_BACK,
    MuscleRegion.TRAPS_LOWER: MuscleGroup.UPPER_BACK,
    MuscleRegion.RHOMBOIDS: MuscleGroup.UPPER_BACK,
    MuscleRegion.ERECTOR_SPINAE: MuscleGroup.LOWER_BACK,
    MuscleRegion.QUADRATUS_LUMBORUM: MuscleGroup.LOWER_BACK,
    MuscleRegion.BICEPS: MuscleGroup.BICEPS,
    MuscleRegion.BRACHIALIS: MuscleGroup.BICEPS,
    MuscleRegion.BRACHIORADIALIS: MuscleGroup.FOREARMS,
    MuscleRegion.TRICEPS_LONG: MuscleGroup.TRICEPS,
    MuscleRegion.TRICEPS_LATERAL_MEDIAL: MuscleGroup.TRICEPS,
    MuscleRegion.FOREARM_FLEXORS: MuscleGroup.FOREARMS,
    MuscleRegion.FOREARM_EXTENSORS: MuscleGroup.FOREARMS,
    MuscleRegion.RECTUS_ABDOMINIS: MuscleGroup.ABS_OBLIQUES,
    MuscleRegion.OBLIQUES: MuscleGroup.ABS_OBLIQUES,
    MuscleRegion.TRANSVERSUS_ABDOMINIS: MuscleGroup.ABS_OBLIQUES,
    MuscleRegion.GLUTE_MAX: MuscleGroup.GLUTES,
    MuscleRegion.GLUTE_MED_MIN: MuscleGroup.GLUTES,
    MuscleRegion.HIP_FLEXORS: MuscleGroup.QUADS,
    MuscleRegion.ADDUCTORS: MuscleGroup.ADDUCTORS,
    MuscleRegion.TFL_ABDUCTORS: MuscleGroup.GLUTES,
    MuscleRegion.QUADS_RECTUS_FEMORIS: MuscleGroup.QUADS,
    MuscleRegion.QUADS_VASTI: MuscleGroup.QUADS,
    MuscleRegion.HAMSTRINGS: MuscleGroup.HAMSTRINGS,
    MuscleRegion.GASTROCNEMIUS: MuscleGroup.CALVES,
    MuscleRegion.SOLEUS: MuscleGroup.CALVES,
    MuscleRegion.TIBIALIS_ANTERIOR: MuscleGroup.CALVES,
    MuscleRegion.NECK: MuscleGroup.NECK,
}

#: The marker for a wellness region that is a joint, not a muscle group.
JOINT: Final = "joint"

type WellnessTarget = tuple[MuscleGroup, ...] | Literal["joint"]

#: Where each wellness soreness region (`BodyRegion` value) lands among the
#: groups, or :data:`JOINT`. Knees, ankles and feet are joints: soreness there
#: is shown as a joint marker on the map, never spread over a muscle group.
#: A coarse wellness region covering several groups maps to all of them.
WELLNESS_REGION_GROUPS: Final[Mapping[str, WellnessTarget]] = {
    "neck": (MuscleGroup.NECK,),
    "shoulders": (MuscleGroup.FRONT_SHOULDERS, MuscleGroup.SIDE_REAR_SHOULDERS),
    "upper_back": (MuscleGroup.UPPER_BACK, MuscleGroup.LATS),
    "lower_back": (MuscleGroup.LOWER_BACK,),
    "chest": (MuscleGroup.CHEST,),
    "arms": (MuscleGroup.BICEPS, MuscleGroup.TRICEPS),
    "forearms": (MuscleGroup.FOREARMS,),
    "abdomen": (MuscleGroup.ABS_OBLIQUES,),
    "hips": (MuscleGroup.GLUTES, MuscleGroup.ADDUCTORS),
    "glutes": (MuscleGroup.GLUTES,),
    "quads": (MuscleGroup.QUADS,),
    "hamstrings": (MuscleGroup.HAMSTRINGS,),
    "calves": (MuscleGroup.CALVES,),
    "knees": JOINT,
    "ankles": JOINT,
    "feet": JOINT,
}


def main_groups(muscles: Mapping[MuscleRegion, float]) -> frozenset[MuscleGroup]:
    """The groups of the regions an exercise loads at :data:`MAIN_WEIGHT`.

    What "this exercise trains quads" means for a filter or a swap: a helper
    region rolling up to quads does not make a movement a quad exercise.
    """
    return frozenset(
        REGION_GROUP[region]
        for region, weight in muscles.items()
        if weight == MAIN_WEIGHT
    )

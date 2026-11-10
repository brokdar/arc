"""Strength prescriptions: exercises, sets, loads, supersets.

The strength half of the workout model (build plan WP-2.2). Two things live
here that look similar and are not:

* :class:`Exercise` is a *catalogue* entry — a hand-curated, bundled list of
  movements with a stable identifier. It is reference data, not a
  prescription.
* :class:`StrengthSet` is one line of a *prescription*: this exercise, this
  many sets of this many reps, at this load, leaving this many reps in
  reserve.

Exercises are identified by a **slug** (``back_squat``), not a uuid. A
prescription is stored as JSON inside a workout, where a foreign key cannot
reach anyway, so the reference has to be readable and stable on its own — and
a bundled catalogue has a natural key by construction.

Supersets are grouping, exactly as the plan asks: a :class:`StrengthGroup`
with more than one item is performed as a superset (its items back to back,
rest taken after the round). There is no `is_superset` flag to keep in step
with the item count.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import StrEnum
from typing import Any

from app.domain.activity import SetSide
from app.domain.coding import (
    as_bool,
    as_enum,
    as_float,
    as_int,
    as_mapping,
    as_sequence,
    as_str,
    field,
    located,
    no_extra_fields,
    optional,
)
from app.domain.muscles import MAIN_WEIGHT, MuscleRegion


class ExerciseCategory(StrEnum):
    """Movement families the catalogue is organised by.

    Patterns rather than muscles: the build plan names squat/hinge/press/pull/
    core, and the remaining four cover what a general strength programme needs
    without inviting a taxonomy argument.
    """

    SQUAT = "squat"
    HINGE = "hinge"
    LUNGE = "lunge"
    PRESS = "press"
    PULL = "pull"
    CORE = "core"
    CARRY = "carry"
    MOBILITY = "mobility"
    CONDITIONING = "conditioning"


class LoadKind(StrEnum):
    """How the load of a prescribed set is expressed.

    Four kinds because four are genuinely different questions: an absolute
    weight, a fraction of a one-rep max, a target effort, and "your own body".
    Scoring treats them differently — `load_within` tolerance is meaningless
    for bodyweight — so the kind is part of the value, not a formatting hint.
    """

    KG = "kg"
    PERCENT_E1RM = "percent_e1rm"
    RPE = "rpe"
    BODYWEIGHT = "bodyweight"


#: Plausibility bounds per load kind, as a typo guard at the boundary.
#: ``percent_e1rm`` is a **fraction** (0.85 = 85 %), matching the percentage
#: convention of `app.domain.zones` and `app.domain.workout`.
LOAD_BOUNDS: dict[LoadKind, tuple[float, float]] = {
    LoadKind.KG: (0.0, 500.0),
    LoadKind.PERCENT_E1RM: (0.05, 1.5),
    LoadKind.RPE: (1.0, 10.0),
}

#: Longest a prescribed rest may be, in seconds.
MAX_REST_SECONDS = 3_600
#: Most sets one prescription line may ask for.
MAX_SETS = 50
#: Most reps one set may ask for.
MAX_REPS = 500
#: Longest a prescribed hold may be, in seconds. The same bound as rest, for
#: the same reason: past an hour it is not a set, it is a typo.
MAX_HOLD_SECONDS = 3_600
#: Reps-in-reserve is a 0-10 scale; anything else is a typo.
MAX_RIR = 10

#: The shape a catalogue slug must have, stated positively. A slug is part of
#: the data contract — it appears inside stored prescriptions, in URLs and in
#: the MCP tools' arguments — so it is checked against what is allowed rather
#: than against a list of characters someone remembered to forbid: spelling the
#: rule as "no spaces" let a tab or a newline through.
SLUG_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]*")


#: What a catalogue entry's ``source`` may say: the free-exercise-db entry its
#: muscles were hand-mapped from, or ``arc`` where that database has no
#: counterpart and the mapping is arc's own.
SOURCE_PATTERN = re.compile(r"arc|free-exercise-db:[A-Za-z0-9][A-Za-z0-9_()-]*")


class Mechanic(StrEnum):
    """Multi-joint or single-joint, as the direction's glossary uses the words.

    Decides the default main lift (the first multi-joint movement of each
    pattern) and, with the main muscle group, which exercises progress in
    small steps.
    """

    MULTI_JOINT = "multi_joint"
    SINGLE_JOINT = "single_joint"


class MovementPattern(StrEnum):
    """What the movement does, at the grain a swap needs.

    Finer than :class:`ExerciseCategory`: a swap for a lat pulldown wants a
    vertical pull, not any pull. The five main-lift patterns (squat, hinge,
    lunge, push, pull) are recovered by reading ``horizontal_push`` and
    ``vertical_push`` as push, and likewise for pull.
    """

    SQUAT = "squat"
    HINGE = "hinge"
    LUNGE = "lunge"
    HORIZONTAL_PUSH = "horizontal_push"
    VERTICAL_PUSH = "vertical_push"
    HORIZONTAL_PULL = "horizontal_pull"
    VERTICAL_PULL = "vertical_pull"
    CHEST_FLY = "chest_fly"
    SHOULDER_RAISE = "shoulder_raise"
    REAR_DELT = "rear_delt"
    SHRUG = "shrug"
    ELBOW_FLEXION = "elbow_flexion"
    ELBOW_EXTENSION = "elbow_extension"
    KNEE_FLEXION = "knee_flexion"
    KNEE_EXTENSION = "knee_extension"
    CALF_RAISE = "calf_raise"
    ANTI_EXTENSION = "anti_extension"
    ANTI_ROTATION = "anti_rotation"
    ANTI_LATERAL_FLEXION = "anti_lateral_flexion"
    TRUNK_FLEXION = "trunk_flexion"
    ROTATION = "rotation"
    CARRY = "carry"
    JUMP = "jump"
    MOBILITY = "mobility"
    CONDITIONING = "conditioning"


class ExerciseEquipment(StrEnum):
    """Something a movement, as named, needs in the room.

    An entry's equipment is **everything** it needs, not a list of
    alternatives: a Bulgarian split squat is ``dumbbell`` *and* ``bench``.
    Alternatives arrive with variations in a later step. ``bodyweight`` means
    "needs nothing" and therefore stands alone.

    Not :class:`app.domain.loading.Equipment`, which is how a *weight* is made
    up (bar and plates, rack steps, a stack); that question is
    :class:`LoadType`.
    """

    BARBELL = "barbell"
    TRAP_BAR = "trap_bar"
    SAFETY_BAR = "safety_bar"
    DUMBBELL = "dumbbell"
    KETTLEBELL = "kettlebell"
    CABLE = "cable"
    MACHINE = "machine"
    BENCH = "bench"
    RACK = "rack"
    PULL_UP_BAR = "pull_up_bar"
    DIP_BARS = "dip_bars"
    BAND = "band"
    BOX = "box"
    SLED = "sled"
    LANDMINE = "landmine"
    AB_WHEEL = "ab_wheel"
    JUMP_ROPE = "jump_rope"
    BATTLE_ROPES = "battle_ropes"
    ROWER = "rower"
    SKI_ERG = "ski_erg"
    AIR_BIKE = "air_bike"
    BODYWEIGHT = "bodyweight"


class LoadType(StrEnum):
    """How the load of the movement is made up, which decides what is loadable.

    ``barbell``, ``dumbbell`` and ``stack`` are the three implements
    `app.domain.loading` rounds for, spelled the same. ``plate_loaded`` is a
    machine or sled loaded with plates and no bar; ``bodyweight`` moves the
    athlete's own body (weight may be added); ``unloaded`` has no load to
    progress — a stretch, an erg.
    """

    BARBELL = "barbell"
    DUMBBELL = "dumbbell"
    STACK = "stack"
    KETTLEBELL = "kettlebell"
    PLATE_LOADED = "plate_loaded"
    BAND = "band"
    BODYWEIGHT = "bodyweight"
    UNLOADED = "unloaded"


class Restriction(StrEnum):
    """What a movement asks of the body that a safety filter may rule out.

    The direction's list (§12), verbatim. A tag says the movement *involves*
    the demand, never that it is unsafe.
    """

    DEEP_KNEE_FLEXION = "deep_knee_flexion"
    DEEP_HIP_FLEXION = "deep_hip_flexion"
    HIP_ROTATION_LOADED = "hip_rotation_loaded"
    LOADED_SPINAL_FLEXION = "loaded_spinal_flexion"
    IMPACT = "impact"
    LONG_BREATH_HOLD = "long_breath_hold"
    UNSUPPORTED_SINGLE_LEG = "unsupported_single_leg"
    OVERHEAD = "overhead"
    SUPINE_LOADED = "supine_loaded"
    PRONE = "prone"
    ADVANCED = "advanced"
    POWER = "power"


@dataclass(frozen=True, slots=True)
class Exercise:
    """One catalogue movement.

    Args:
        id: Stable slug (``back_squat``). Referenced from prescriptions, so it
            is part of the data contract and is never renamed in place.
        name: Display name.
        category: Movement family.
        unilateral: Whether the movement is performed one side at a time —
            which is what lets "3 x 8" be prescribed per side: three sets of
            left-right pairs, 48 reps.
        muscles: ``{region: weight}``, 1.0 for a main muscle and 0.5 for a
            helper (any value in [0, 1] is legal). At least one region is
            main: a movement that only helps everywhere trains nothing a
            count or a swap could name. Excluded from the hash (a dict is not
            hashable); equality still compares it.
        mechanic: Multi- or single-joint.
        pattern: What the movement does.
        equipment: Everything the movement needs; never empty.
        load_type: How its load is made up.
        restrictions: Demands a safety filter may rule out; often empty.
        source: Where the muscle mapping's hint came from — see
            :data:`SOURCE_PATTERN`. Recorded per entry because the muscles
            are hand-mapped, not copied: the source says what was read, the
            mapping says what arc concluded.
    """

    id: str
    name: str
    category: ExerciseCategory
    unilateral: bool = False
    muscles: Mapping[MuscleRegion, float] = dataclass_field(kw_only=True, hash=False)
    mechanic: Mechanic = dataclass_field(kw_only=True)
    pattern: MovementPattern = dataclass_field(kw_only=True)
    equipment: frozenset[ExerciseEquipment] = dataclass_field(kw_only=True)
    load_type: LoadType = dataclass_field(kw_only=True)
    restrictions: frozenset[Restriction] = dataclass_field(
        kw_only=True, default=frozenset()
    )
    source: str = dataclass_field(kw_only=True)

    def __post_init__(self) -> None:
        """Reject entries that could not have come from the catalogue file."""
        if not self.id.strip():
            raise ValueError("an exercise needs a non-empty id")
        if not SLUG_PATTERN.fullmatch(self.id):
            raise ValueError(
                f"exercise id {self.id!r} must be a lowercase slug: digits and "
                "letters a-z, separated by '_' or '-', starting with either"
            )
        if not self.name.strip():
            raise ValueError(f"exercise {self.id} needs a non-empty name")
        for region, weight in self.muscles.items():
            if not 0.0 <= weight <= 1.0:
                raise ValueError(
                    f"exercise {self.id}: muscle weight of {region} must be "
                    f"between 0 and 1, got {weight}"
                )
        if not any(weight == MAIN_WEIGHT for weight in self.muscles.values()):
            raise ValueError(
                f"exercise {self.id} needs at least one main muscle (weight "
                f"{MAIN_WEIGHT}); helpers alone train no group"
            )
        if not self.equipment:
            raise ValueError(
                f"exercise {self.id} needs equipment; use 'bodyweight' for none"
            )
        if ExerciseEquipment.BODYWEIGHT in self.equipment and len(self.equipment) > 1:
            raise ValueError(
                f"exercise {self.id}: equipment 'bodyweight' means it needs "
                "nothing, so it cannot be listed with anything else"
            )
        if not SOURCE_PATTERN.fullmatch(self.source):
            raise ValueError(
                f"exercise {self.id}: source {self.source!r} must be 'arc' or "
                "'free-exercise-db:<entry id>'"
            )


@dataclass(frozen=True, slots=True)
class Load:
    """How heavy a prescribed set is.

    **A kilogram value is the external load moved in one rep of the set as
    prescribed.** For a per-side set that is the load on *that side* — one
    15 kg dumbbell is ``15`` — and for a bilateral set held with two
    implements it is the total — two 15 kg dumbbells are ``30``. Volume is then
    ``working_sets × reps × value`` with no per-implement multiplier anywhere,
    and there is no fourth field for the athlete to get wrong. Declared rather
    than modelled because "two 15 kg dumbbells" is genuinely ambiguous — it
    reads as 15 or 30 depending on who is asked — and an ambiguity that
    silently doubles :attr:`~app.domain.prediction.PredictedVolume.
    volume_load_kg` is worse than a convention someone has to look up once.

    Args:
        kind: See :class:`LoadKind`.
        value: The number, in the kind's own scale. ``None`` — and only
            ``None`` — for :attr:`LoadKind.BODYWEIGHT`.
    """

    kind: LoadKind
    value: float | None = None

    def __post_init__(self) -> None:
        """Enforce that the value matches the kind that describes it."""
        if self.kind is LoadKind.BODYWEIGHT:
            if self.value is not None:
                raise ValueError(
                    "a bodyweight load carries no value; use kg for added weight"
                )
            return
        if self.value is None:
            raise ValueError(f"a {self.kind.value} load needs a value")
        low, high = LOAD_BOUNDS[self.kind]
        if not low <= self.value <= high:
            raise ValueError(
                f"{self.kind.value} load must be between {low} and {high}, "
                f"got {self.value}"
            )

    @classmethod
    def bodyweight(cls) -> Load:
        """The athlete's own mass, with nothing added."""
        return cls(LoadKind.BODYWEIGHT)


@dataclass(frozen=True, slots=True)
class StrengthSet:
    """One line of a strength prescription.

    **``sets`` counts rounds; a per-side round is one set worked twice.** "3 ×
    11 single-arm row" is three rounds, and each round is a left-right pair:
    one set in every count (:attr:`working_sets`), two sides in every sum of
    work (:attr:`total_reps`, :attr:`total_hold_s`, volume load). See
    :attr:`working_sets` for why the pair, not the limb, is the unit.

    **A line prescribes reps or a hold, never both.** A 45-second plank is
    ``duration_s=45``, not ``reps=1`` with an apologetic note — the note is not
    a number anything can sum, and a rep count invented for a hold enters
    volume arithmetic as if it were work.

    Args:
        exercise_id: Slug of a catalogue :class:`Exercise`.
        sets: Number of **rounds** prescribed.
        load: How heavy. For a per-side line this is the load on **one** side —
            see :class:`Load`.
        reps: Reps per set (each side, per side), or ``None`` for a timed hold. Exactly one
            of ``reps`` and ``duration_s`` is given.
        duration_s: Seconds held per set (each side), or ``None`` for a rep-based
            set.
        per_side: Whether each round is performed one side at a time — one set
            of a left-right pair, its reps worked by both sides. Stored on the prescription rather than looked up
            from :attr:`Exercise.unilateral` at read time: a prescription is
            stored JSON and has to stay self-describing when the catalogue
            changes under it. The *service* refuses ``per_side`` on a movement
            the catalogue marks bilateral, so the two cannot disagree in the
            direction that matters.
        rir: Target reps in reserve, 0-10, or ``None`` when unprescribed.
        rest_s: Prescribed rest after each round, in seconds.
        tempo: Free-form tempo notation (``"3-1-1-0"``), uninterpreted by the
            MVP.
        notes: Free-form coaching note for this line.
    """

    exercise_id: str
    sets: int
    load: Load
    reps: int | None = None
    duration_s: int | None = None
    per_side: bool = False
    rir: int | None = None
    rest_s: int | None = None
    tempo: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        """Reject prescriptions nobody could execute."""
        if not self.exercise_id.strip():
            raise ValueError("a prescribed set needs an exercise")
        if not 1 <= self.sets <= MAX_SETS:
            raise ValueError(f"sets must be between 1 and {MAX_SETS}, got {self.sets}")
        if (self.reps is None) == (self.duration_s is None):
            raise ValueError(
                "a prescribed set needs exactly one of reps or duration_s: it "
                "prescribes repetitions or it prescribes a hold, not both and "
                "not neither"
            )
        if self.reps is not None and not 1 <= self.reps <= MAX_REPS:
            raise ValueError(f"reps must be between 1 and {MAX_REPS}, got {self.reps}")
        if self.duration_s is not None and not 1 <= self.duration_s <= MAX_HOLD_SECONDS:
            raise ValueError(
                f"duration_s must be between 1 and {MAX_HOLD_SECONDS}, "
                f"got {self.duration_s}"
            )
        if self.rir is not None and not 0 <= self.rir <= MAX_RIR:
            raise ValueError(f"rir must be between 0 and {MAX_RIR}, got {self.rir}")
        if self.rest_s is not None and not 0 <= self.rest_s <= MAX_REST_SECONDS:
            raise ValueError(
                f"rest_s must be between 0 and {MAX_REST_SECONDS}, got {self.rest_s}"
            )

    @property
    def working_sets(self) -> float:
        """Sets this line counts as: a left-right pair is **one** set.

        Set counts use pairs; a lone logged side is ½
        (:func:`logged_set_units`); reps, holds and volume load count both
        sides (:attr:`sides`). This displaces the earlier rule that a per-side
        round was two working sets, one per limb: a left and a right set of
        split squats then inflated completion, matching and the week rail's
        set count to twice what the athlete would call the session, and
        per-muscle set counting — which asks "how many hard sets did this
        muscle get" — cannot be honest when one side of a pair is a set of its
        own. The limb still does the work, so the multiplier moved to the
        sums: ``3 × 8 per side`` is 3.0 sets and 48 reps.

        A float with 0.5 resolution because the logged side of the same unit
        has halves (a lone left set), and completion divides the two.
        `test_domain_strength.py::test_pair_counts_once` pins the rule.
        """
        return float(self.sets)

    @property
    def sides(self) -> int:
        """How many sides each round works: two per side, one otherwise.

        The multiplier for sums of work — reps, seconds held, kilograms —
        never for counts of sets (:attr:`working_sets`).
        """
        return 2 if self.per_side else 1

    @property
    def total_reps(self) -> int | None:
        """Prescribed reps across every round and side, or ``None`` for a hold."""
        if self.reps is None:
            return None
        return self.sets * self.sides * self.reps

    @property
    def total_hold_s(self) -> int | None:
        """Prescribed seconds held across every round and side, or ``None``.

        Reported beside volume load rather than folded into it: a hold has no
        reps to multiply by kilograms, and seconds and kilograms are no more
        addable than kilograms and TSS.
        """
        if self.duration_s is None:
            return None
        return self.sets * self.sides * self.duration_s


@dataclass(frozen=True, slots=True)
class StrengthGroup:
    """One or more prescription lines performed together.

    A group with a single item is an ordinary exercise. A group with more than
    one is a **superset**: the items are performed back to back and the rest is
    taken after the round.

    Args:
        items: The lines, in the order they are performed.
        label: Optional name for the group (``"A1/A2"``, ``"finisher"``).
    """

    items: tuple[StrengthSet, ...]
    label: str | None = None

    def __post_init__(self) -> None:
        """Reject empty groups."""
        if not self.items:
            raise ValueError("a strength group needs at least one prescribed set")

    @property
    def is_superset(self) -> bool:
        """Whether the items are performed back to back."""
        return len(self.items) > 1


#: Most groups one strength workout may hold — a bound on user-supplied JSON.
MAX_STRENGTH_GROUPS = 40


@dataclass(frozen=True, slots=True)
class StrengthWorkout:
    """A complete strength prescription: an ordered list of groups."""

    groups: tuple[StrengthGroup, ...]

    def __post_init__(self) -> None:
        """Reject workouts with nothing in them, or implausibly many groups."""
        if not self.groups:
            raise ValueError("a strength workout needs at least one group")
        if len(self.groups) > MAX_STRENGTH_GROUPS:
            raise ValueError(
                f"a strength workout may hold at most {MAX_STRENGTH_GROUPS} groups, "
                f"got {len(self.groups)}"
            )

    @property
    def prescriptions(self) -> tuple[StrengthSet, ...]:
        """Every prescription line, in execution order, groups flattened."""
        return tuple(item for group in self.groups for item in group.items)

    @property
    def total_sets(self) -> float:
        """Prescribed sets across the whole workout, a per-side pair counting once.

        See :attr:`StrengthSet.working_sets`.
        """
        return sum((item.working_sets for item in self.prescriptions), 0.0)


# --- logged sides --------------------------------------------------------------


def logged_set_units(side: SetSide) -> float:
    """How many sets one logged row counts as, in the prescription's unit.

    The logged half of :attr:`StrengthSet.working_sets`: a row that worked one
    side (``left`` / ``right``) is half of a left-right pair, so ½; a row that
    worked both — at once, or one after the other written as one ``per_side``
    row ("8 each side", every such row stored before ``side`` existed) — is a
    whole set. L8 R8 L8 R8 L8 R8 is therefore 3.0, and one more L8 is 3.5.

    ``per_side`` takes no part: a row with a single ``side`` is refused at
    write time when it also claims ``per_side`` (:func:`check_sidedness`), and
    a stored row that somehow carries both is counted by the side it names.
    """
    return 1.0 if side is SetSide.BOTH else 0.5


def logged_sides_worked(*, per_side: bool, side: SetSide) -> int:
    """How many sides one logged row's reps, seconds and kilograms were worked by.

    Two only for a ``per_side`` row that holds the pair in one line; a single
    side and an ordinary bilateral set are each worked once. The multiplier
    for volume load and seconds held, never for set counts
    (:func:`logged_set_units`).
    """
    return 2 if per_side and side is SetSide.BOTH else 1


def check_sidedness(unilateral: bool | None, *, per_side: bool, side: SetSide) -> None:
    """Refuse a logged row whose sidedness has no count, or no catalogue sense.

    Args:
        unilateral: The catalogue's :attr:`Exercise.unilateral` for the
            movement, or ``None`` for a free-text movement the catalogue has
            no opinion on.
        per_side: Whether the row claims to hold a left-right pair.
        side: Which side the row worked.

    Raises:
        ValueError: When the row claims both ``per_side`` and a single side —
            a pair and half a pair at once — or claims either on a movement the
            catalogue marks bilateral, where a single side would halve the
            session's set count and ``per_side`` would double its volume.
    """
    if per_side and side is not SetSide.BOTH:
        raise ValueError(
            "a set is one side or a pair, not both: send side left/right for "
            "one side, or per_side for both sides written as one row"
        )
    if unilateral is False and (per_side or side is not SetSide.BOTH):
        raise ValueError(
            "the movement is performed with both sides at once, so it cannot "
            "be logged per side or for one side"
        )


def exercise_ids(workout: StrengthWorkout) -> frozenset[str]:
    """Return every catalogue slug the workout prescribes."""
    return frozenset(item.exercise_id for item in workout.prescriptions)


# --- serialization ------------------------------------------------------------


def load_to_json(load: Load) -> dict[str, Any]:
    """Serialize a load."""
    document: dict[str, Any] = {"kind": load.kind.value}
    if load.value is not None:
        document["value"] = load.value
    return document


_LOAD_FIELDS = frozenset({"kind", "value"})


def load_from_json(document: Any, path: str = "load") -> Load:
    """Deserialize a load.

    Raises:
        ValueError: When the document is not a legal load.
    """
    body = as_mapping(document, path)
    no_extra_fields(body, _LOAD_FIELDS, path)
    kind = as_enum(LoadKind, field(body, "kind", path), f"{path}.kind")
    raw_value = optional(body, "value")
    value = None if raw_value is None else as_float(raw_value, f"{path}.value")
    with located(path):
        return Load(kind=kind, value=value)


def strength_set_to_json(prescription: StrengthSet) -> dict[str, Any]:
    """Serialize one prescription line.

    Absent optional fields are **omitted, never written as their default**. A
    prescription stored before ``per_side`` and ``duration_s`` existed carries
    neither key, and re-serializing it after an unrelated edit must not start
    writing ``"per_side": false`` into it: a document that grows keys on every
    read makes every stored prescription's diff a lie about what changed, and
    makes the round-trip test that guards this file unable to say anything.
    """
    document: dict[str, Any] = {
        "exercise_id": prescription.exercise_id,
        "sets": prescription.sets,
    }
    if prescription.reps is not None:
        document["reps"] = prescription.reps
    if prescription.duration_s is not None:
        document["duration_s"] = prescription.duration_s
    document["load"] = load_to_json(prescription.load)
    if prescription.per_side:
        document["per_side"] = True
    for name in ("rir", "rest_s", "tempo", "notes"):
        value = getattr(prescription, name)
        if value is not None:
            document[name] = value
    return document


_SET_FIELDS = frozenset(
    {
        "exercise_id",
        "sets",
        "reps",
        "duration_s",
        "per_side",
        "load",
        "rir",
        "rest_s",
        "tempo",
        "notes",
    }
)


def strength_set_from_json(document: Any, path: str) -> StrengthSet:
    """Deserialize one prescription line.

    ``reps`` is optional here because a timed hold has none — the
    exactly-one-of rule is :class:`StrengthSet`'s, so a document carrying
    neither is refused by the same message on every surface rather than by a
    missing-key error that names only ``reps``. ``per_side`` absent means
    ``False``, which is what every document stored before it existed means.

    Raises:
        ValueError: When the document is not a legal prescription line.
    """
    body = as_mapping(document, path)
    no_extra_fields(body, _SET_FIELDS, path)
    reps = optional(body, "reps")
    duration_s = optional(body, "duration_s")
    per_side = optional(body, "per_side")
    rir = optional(body, "rir")
    rest_s = optional(body, "rest_s")
    tempo = optional(body, "tempo")
    notes = optional(body, "notes")
    exercise_id = as_str(field(body, "exercise_id", path), f"{path}.exercise_id")
    sets = as_int(field(body, "sets", path), f"{path}.sets")
    load = load_from_json(field(body, "load", path), f"{path}.load")
    with located(path):
        return StrengthSet(
            exercise_id=exercise_id,
            sets=sets,
            load=load,
            reps=None if reps is None else as_int(reps, f"{path}.reps"),
            duration_s=(
                None if duration_s is None else as_int(duration_s, f"{path}.duration_s")
            ),
            per_side=(
                False if per_side is None else as_bool(per_side, f"{path}.per_side")
            ),
            rir=None if rir is None else as_int(rir, f"{path}.rir"),
            rest_s=None if rest_s is None else as_int(rest_s, f"{path}.rest_s"),
            tempo=None if tempo is None else as_str(tempo, f"{path}.tempo"),
            notes=None if notes is None else as_str(notes, f"{path}.notes"),
        )


def strength_group_to_json(group: StrengthGroup) -> dict[str, Any]:
    """Serialize one group."""
    document: dict[str, Any] = {
        "items": [strength_set_to_json(item) for item in group.items]
    }
    if group.label is not None:
        document["label"] = group.label
    return document


_GROUP_FIELDS = frozenset({"items", "label"})


def strength_group_from_json(document: Any, path: str) -> StrengthGroup:
    """Deserialize one group.

    Raises:
        ValueError: When the document is not a legal group.
    """
    body = as_mapping(document, path)
    no_extra_fields(body, _GROUP_FIELDS, path)
    items = as_sequence(field(body, "items", path), f"{path}.items")
    label = optional(body, "label")
    decoded = tuple(
        strength_set_from_json(item, f"{path}.items[{index}]")
        for index, item in enumerate(items)
    )
    with located(path):
        return StrengthGroup(
            items=decoded,
            label=None if label is None else as_str(label, f"{path}.label"),
        )


def strength_workout_to_json(workout: StrengthWorkout) -> dict[str, Any]:
    """Serialize a strength workout (without the discipline tag)."""
    return {"groups": [strength_group_to_json(group) for group in workout.groups]}


_WORKOUT_FIELDS = frozenset({"groups"})


def strength_workout_from_json(document: Any, path: str = "") -> StrengthWorkout:
    """Deserialize a strength workout.

    Raises:
        ValueError: When the document is not a legal strength workout.
    """
    body = as_mapping(document, path)
    no_extra_fields(body, _WORKOUT_FIELDS, path)
    groups = as_sequence(field(body, "groups", path), _join(path, "groups"))
    decoded = tuple(
        strength_group_from_json(group, _join(path, f"groups[{index}]"))
        for index, group in enumerate(groups)
    )
    with located(path or "workout"):
        return StrengthWorkout(groups=decoded)


def exercise_to_json(exercise: Exercise) -> dict[str, Any]:
    """Serialize a catalogue entry, in the catalogue file's own shape.

    Sets are written sorted so the output is stable across runs.
    """
    return {
        "id": exercise.id,
        "name": exercise.name,
        "category": exercise.category.value,
        "unilateral": exercise.unilateral,
        "muscles": {
            region.value: weight for region, weight in exercise.muscles.items()
        },
        "mechanic": exercise.mechanic.value,
        "pattern": exercise.pattern.value,
        "equipment": sorted(item.value for item in exercise.equipment),
        "load_type": exercise.load_type.value,
        "restrictions": sorted(tag.value for tag in exercise.restrictions),
        "source": exercise.source,
    }


_EXERCISE_FIELDS = frozenset(
    {
        "id",
        "name",
        "category",
        "unilateral",
        "muscles",
        "mechanic",
        "pattern",
        "equipment",
        "load_type",
        "restrictions",
        "source",
    }
)


def _enum_set[E: StrEnum](enum_class: type[E], value: Any, path: str) -> frozenset[E]:
    """Decode a JSON array of ``enum_class`` values into a set."""
    return frozenset(
        as_enum(enum_class, item, f"{path}[{index}]")
        for index, item in enumerate(as_sequence(value, path))
    )


def exercise_from_json(document: Any, path: str = "exercise") -> Exercise:
    """Deserialize a catalogue entry.

    Every tag but ``restrictions`` (absent means none) and ``unilateral``
    (absent means false) is required: an untagged entry is exactly what a
    swap or a safety filter cannot reason about.

    Raises:
        ValueError: When the document is not a legal catalogue entry.
    """
    body = as_mapping(document, path)
    no_extra_fields(body, _EXERCISE_FIELDS, path)
    unilateral = optional(body, "unilateral")
    restrictions = optional(body, "restrictions")
    slug = as_str(field(body, "id", path), f"{path}.id")
    name = as_str(field(body, "name", path), f"{path}.name")
    category = as_enum(
        ExerciseCategory, field(body, "category", path), f"{path}.category"
    )
    muscles_path = f"{path}.muscles"
    muscles = {
        as_enum(MuscleRegion, region, f"{muscles_path}.{region}"): as_float(
            weight, f"{muscles_path}.{region}"
        )
        for region, weight in as_mapping(
            field(body, "muscles", path), muscles_path
        ).items()
    }
    mechanic = as_enum(Mechanic, field(body, "mechanic", path), f"{path}.mechanic")
    pattern = as_enum(MovementPattern, field(body, "pattern", path), f"{path}.pattern")
    equipment = _enum_set(
        ExerciseEquipment, field(body, "equipment", path), f"{path}.equipment"
    )
    load_type = as_enum(LoadType, field(body, "load_type", path), f"{path}.load_type")
    source = as_str(field(body, "source", path), f"{path}.source")
    with located(path):
        return Exercise(
            id=slug,
            name=name,
            category=category,
            unilateral=(
                False
                if unilateral is None
                else as_bool(unilateral, f"{path}.unilateral")
            ),
            muscles=muscles,
            mechanic=mechanic,
            pattern=pattern,
            equipment=equipment,
            load_type=load_type,
            restrictions=(
                frozenset()
                if restrictions is None
                else _enum_set(Restriction, restrictions, f"{path}.restrictions")
            ),
            source=source,
        )


def parse_catalogue(document: Any) -> tuple[Exercise, ...]:
    """Parse the bundled exercise catalogue.

    Args:
        document: The decoded JSON file — ``{"exercises": [...]}``.

    Returns:
        The catalogue, in file order.

    Raises:
        ValueError: When the file is malformed or holds a duplicate slug. A
            duplicate is fatal rather than deduplicated: two entries claiming
            one slug means every prescription referencing it is ambiguous.
    """
    body = as_mapping(document, "catalogue")
    no_extra_fields(body, frozenset({"exercises"}), "catalogue")
    entries = as_sequence(field(body, "exercises", "catalogue"), "catalogue.exercises")
    exercises = tuple(
        exercise_from_json(entry, _entry_path(index, entry))
        for index, entry in enumerate(entries)
    )
    if not exercises:
        raise ValueError("catalogue.exercises: the catalogue must not be empty")
    seen: set[str] = set()
    duplicates: set[str] = set()
    for exercise in exercises:
        if exercise.id in seen:
            duplicates.add(exercise.id)
        seen.add(exercise.id)
    if duplicates:
        raise ValueError(
            f"catalogue: duplicate exercise id(s): {', '.join(sorted(duplicates))}"
        )
    return exercises


def _entry_path(index: int, entry: Any) -> str:
    """Where a catalogue entry sits, naming its slug when it has one.

    ``catalogue.exercises[41] (bench_press)``: a position alone sends whoever
    edits a hundred-entry file counting.
    """
    path = f"catalogue.exercises[{index}]"
    slug = entry.get("id") if isinstance(entry, Mapping) else None
    return f"{path} ({slug})" if isinstance(slug, str) else path


def _join(path: str, name: str) -> str:
    """Append ``name`` to a possibly-empty document path."""
    return f"{path}.{name}" if path else name


def sorted_by_name(exercises: Sequence[Exercise]) -> list[Exercise]:
    """Return the catalogue sorted by category, then display name."""
    return sorted(exercises, key=lambda entry: (entry.category.value, entry.name))

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
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

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


@dataclass(frozen=True, slots=True)
class Exercise:
    """One catalogue movement.

    Args:
        id: Stable slug (``back_squat``). Referenced from prescriptions, so it
            is part of the data contract and is never renamed in place.
        name: Display name.
        category: Movement family.
        unilateral: Whether the movement is performed one side at a time —
            which is what makes "3 x 8" mean six working sets, not three.
    """

    id: str
    name: str
    category: ExerciseCategory
    unilateral: bool = False

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

    **``sets`` counts rounds, not working sets.** It is what the athlete writes
    on the card — "3 × 11 single-arm row" is three rounds — and a per-side round
    is two working sets, one per limb. The two numbers are kept apart rather
    than reconciled at every reader: :attr:`sets` stays the prescription as
    written, and :attr:`working_sets` is the unit volume, completion and
    alignment all count in. Before ``per_side`` existed the two were the same
    number, and a unilateral session reported half the work it asked for.

    **A line prescribes reps or a hold, never both.** A 45-second plank is
    ``duration_s=45``, not ``reps=1`` with an apologetic note — the note is not
    a number anything can sum, and a rep count invented for a hold enters
    volume arithmetic as if it were work.

    Args:
        exercise_id: Slug of a catalogue :class:`Exercise`.
        sets: Number of **rounds** prescribed.
        load: How heavy. For a per-side line this is the load on **one** side —
            see :class:`Load`.
        reps: Reps per working set, or ``None`` for a timed hold. Exactly one
            of ``reps`` and ``duration_s`` is given.
        duration_s: Seconds held per working set, or ``None`` for a rep-based
            set.
        per_side: Whether each round is performed one side at a time, making it
            two working sets. Stored on the prescription rather than looked up
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
    def working_sets(self) -> int:
        """Sets actually worked: a per-side round is two, one per limb.

        The unit every count downstream is in — predicted volume, the week
        rail's set count, completion scoring and the prescribed/logged
        alignment. Issue #25's case: ``sets=3, reps=11, per_side=True`` is six
        working sets, not three.
        """
        return self.sets * 2 if self.per_side else self.sets

    @property
    def total_reps(self) -> int | None:
        """Prescribed reps across every working set, or ``None`` for a hold."""
        if self.reps is None:
            return None
        return self.working_sets * self.reps

    @property
    def total_hold_s(self) -> int | None:
        """Prescribed seconds held across every working set, or ``None``.

        Reported beside volume load rather than folded into it: a hold has no
        reps to multiply by kilograms, and seconds and kilograms are no more
        addable than kilograms and TSS.
        """
        if self.duration_s is None:
            return None
        return self.working_sets * self.duration_s


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
    def total_sets(self) -> int:
        """Prescribed **working** sets across the whole workout.

        Working sets, not rounds: see :attr:`StrengthSet.working_sets`.
        """
        return sum(item.working_sets for item in self.prescriptions)


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
    """Serialize a catalogue entry."""
    return {
        "id": exercise.id,
        "name": exercise.name,
        "category": exercise.category.value,
        "unilateral": exercise.unilateral,
    }


_EXERCISE_FIELDS = frozenset({"id", "name", "category", "unilateral"})


def exercise_from_json(document: Any, path: str = "exercise") -> Exercise:
    """Deserialize a catalogue entry.

    Raises:
        ValueError: When the document is not a legal catalogue entry.
    """
    body = as_mapping(document, path)
    no_extra_fields(body, _EXERCISE_FIELDS, path)
    unilateral = optional(body, "unilateral")
    slug = as_str(field(body, "id", path), f"{path}.id")
    name = as_str(field(body, "name", path), f"{path}.name")
    category = as_enum(
        ExerciseCategory, field(body, "category", path), f"{path}.category"
    )
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
        exercise_from_json(entry, f"catalogue.exercises[{index}]")
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


def _join(path: str, name: str) -> str:
    """Append ``name`` to a possibly-empty document path."""
    return f"{path}.{name}" if path else name


def sorted_by_name(exercises: Sequence[Exercise]) -> list[Exercise]:
    """Return the catalogue sorted by category, then display name."""
    return sorted(exercises, key=lambda entry: (entry.category.value, entry.name))

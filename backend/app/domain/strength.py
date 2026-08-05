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
#: Reps-in-reserve is a 0-10 scale; anything else is a typo.
MAX_RIR = 10


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
        if self.id != self.id.strip().lower() or " " in self.id:
            raise ValueError(
                f"exercise id {self.id!r} must be a lowercase slug without spaces"
            )
        if not self.name.strip():
            raise ValueError(f"exercise {self.id} needs a non-empty name")


@dataclass(frozen=True, slots=True)
class Load:
    """How heavy a prescribed set is.

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

    Args:
        exercise_id: Slug of a catalogue :class:`Exercise`.
        sets: Number of working sets.
        reps: Reps per set (per side, for a unilateral movement).
        load: How heavy.
        rir: Target reps in reserve, 0-10, or ``None`` when unprescribed.
        rest_s: Prescribed rest after each set, in seconds.
        tempo: Free-form tempo notation (``"3-1-1-0"``), uninterpreted by the
            MVP.
        notes: Free-form coaching note for this line.
    """

    exercise_id: str
    sets: int
    reps: int
    load: Load
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
        if not 1 <= self.reps <= MAX_REPS:
            raise ValueError(f"reps must be between 1 and {MAX_REPS}, got {self.reps}")
        if self.rir is not None and not 0 <= self.rir <= MAX_RIR:
            raise ValueError(f"rir must be between 0 and {MAX_RIR}, got {self.rir}")
        if self.rest_s is not None and not 0 <= self.rest_s <= MAX_REST_SECONDS:
            raise ValueError(
                f"rest_s must be between 0 and {MAX_REST_SECONDS}, got {self.rest_s}"
            )

    @property
    def total_reps(self) -> int:
        """Prescribed reps across every set of this line."""
        return self.sets * self.reps


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
        """Prescribed working sets across the whole workout."""
        return sum(item.sets for item in self.prescriptions)


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
    return Load(kind=kind, value=value)


def strength_set_to_json(prescription: StrengthSet) -> dict[str, Any]:
    """Serialize one prescription line."""
    document: dict[str, Any] = {
        "exercise_id": prescription.exercise_id,
        "sets": prescription.sets,
        "reps": prescription.reps,
        "load": load_to_json(prescription.load),
    }
    for name in ("rir", "rest_s", "tempo", "notes"):
        value = getattr(prescription, name)
        if value is not None:
            document[name] = value
    return document


_SET_FIELDS = frozenset(
    {"exercise_id", "sets", "reps", "load", "rir", "rest_s", "tempo", "notes"}
)


def strength_set_from_json(document: Any, path: str) -> StrengthSet:
    """Deserialize one prescription line.

    Raises:
        ValueError: When the document is not a legal prescription line.
    """
    body = as_mapping(document, path)
    no_extra_fields(body, _SET_FIELDS, path)
    rir = optional(body, "rir")
    rest_s = optional(body, "rest_s")
    tempo = optional(body, "tempo")
    notes = optional(body, "notes")
    return StrengthSet(
        exercise_id=as_str(field(body, "exercise_id", path), f"{path}.exercise_id"),
        sets=as_int(field(body, "sets", path), f"{path}.sets"),
        reps=as_int(field(body, "reps", path), f"{path}.reps"),
        load=load_from_json(field(body, "load", path), f"{path}.load"),
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
    return StrengthGroup(
        items=tuple(
            strength_set_from_json(item, f"{path}.items[{index}]")
            for index, item in enumerate(items)
        ),
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
    return StrengthWorkout(
        groups=tuple(
            strength_group_from_json(group, _join(path, f"groups[{index}]"))
            for index, group in enumerate(groups)
        )
    )


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
    return Exercise(
        id=as_str(field(body, "id", path), f"{path}.id"),
        name=as_str(field(body, "name", path), f"{path}.name"),
        category=as_enum(
            ExerciseCategory, field(body, "category", path), f"{path}.category"
        ),
        unilateral=(
            False if unilateral is None else as_bool(unilateral, f"{path}.unilateral")
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

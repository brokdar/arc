"""The structured workout: a recursive step tree, and how it is flattened.

The endurance half of the workout model (build plan WP-2.1), plus the union
that lets one workout row hold either discipline.

**Shape.** A workout is an ordered tuple of steps. A step is one of three
things: a :class:`SteadyStep` (hold these targets for this long), a
:class:`RampStep` (move from one set of targets to another over this long), or
a :class:`RepeatBlock` (do these children N times). Blocks nest.

**Targets** are per channel — power, heart rate, cadence — and each is either
a percentage range of an anchor (``85-95 % FTP``) or an absolute range
(``250-270 W``). Percentages are stored as **fractions** (``0.85``), the same
convention `app.domain.zones` uses, so a target and a zone can be compared
without anyone remembering which of them is scaled by 100.

**Flattening** (:func:`flatten`) is what display and scoring consume. Two
things about it are decisions rather than mechanics:

* **Repeat blocks expand.** The fifth rep of a 5x block becomes five separate
  flat steps, because that is what the athlete does and what a recording can
  be aligned against. Each flat step remembers which iteration of which block
  it came from (:attr:`FlatStep.repetition`), so WP-7's `pacing` axis can ask
  "first rep versus last rep" without re-walking the tree.
* **Ramps stay ramps.** A ramp is *not* chopped into steady slices: doing so
  would invent a step count the athlete never rode and a precision the
  prescription does not have. A flat step therefore carries a start and an end
  target set, equal for a steady step and different for a ramp, and a consumer
  that only understands steady blocks can read the midpoint.

The result is a list that can be rebuilt into a repeat-free workout
(:func:`expand`) — flattening twice changes nothing, which is the round-trip
the property tests pin.
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from app.domain.anchors import AnchorType
from app.domain.athlete import Discipline
from app.domain.coding import (
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
from app.domain.strength import (
    StrengthWorkout,
    strength_workout_from_json,
    strength_workout_to_json,
)


class Channel(StrEnum):
    """A prescribable (and recordable) measurement channel."""

    POWER = "power"
    HR = "hr"
    CADENCE = "cadence"


class ChannelUnit(StrEnum):
    """The unit each channel is measured in."""

    WATT = "W"
    BPM = "bpm"
    RPM = "rpm"


class StepRole(StrEnum):
    """What a step is for within the session.

    Roles are what a success criterion selects on (``time in band across the
    work steps``) and what WP-5's alignment matches against, so they are part
    of the prescription rather than a display hint.
    """

    WARMUP = "warmup"
    WORK = "work"
    RECOVERY = "recovery"
    REST = "rest"
    COOLDOWN = "cooldown"


#: The unit each channel is measured in. One per channel for the same reason
#: anchors have one unit per type: a choice would mean every consumer
#: converting before comparing.
CHANNEL_UNITS: dict[Channel, ChannelUnit] = {
    Channel.POWER: ChannelUnit.WATT,
    Channel.HR: ChannelUnit.BPM,
    Channel.CADENCE: ChannelUnit.RPM,
}

#: Plausibility bounds for an absolute target, per channel. The same ranges
#: WP-4 validates recordings against — a prescription outside them could never
#: be executed by a value the ingest pipeline would accept.
CHANNEL_BOUNDS: dict[Channel, tuple[float, float]] = {
    Channel.POWER: (0.0, 2_500.0),
    Channel.HR: (25.0, 230.0),
    Channel.CADENCE: (0.0, 250.0),
}

#: Which anchors a channel may be prescribed as a percentage of. Cadence has
#: none: there is no cadence anchor, and "80 % of FTP rpm" is not a quantity.
CHANNEL_ANCHORS: dict[Channel, frozenset[AnchorType]] = {
    Channel.POWER: frozenset({AnchorType.FTP}),
    Channel.HR: frozenset({AnchorType.LTHR, AnchorType.MAX_HR}),
    Channel.CADENCE: frozenset(),
}

#: Widest percentage a target may prescribe (300 % of the anchor).
MAX_TARGET_FRACTION = 3.0
#: Longest a single step may last (12 hours).
MAX_STEP_SECONDS = 43_200
#: Longest a single step may cover, in metres (1000 km).
MAX_STEP_METRES = 1_000_000.0
#: Most iterations one repeat block may ask for.
MAX_REPEAT_TIMES = 100
#: How deeply repeat blocks may nest. Bounded because the tree arrives as
#: user-supplied JSON and flattening is exponential in nesting depth.
MAX_NESTING_DEPTH = 4
#: Most steps a workout may flatten to, for the same reason.
MAX_FLAT_STEPS = 1_000


@dataclass(frozen=True, slots=True)
class PercentOfAnchor:
    """A target expressed as a fraction range of an anchor value.

    Args:
        anchor_type: Which anchor the percentages are of.
        pct_low: Lower bound as a fraction (``0.85`` is 85 %).
        pct_high: Upper bound as a fraction. Equal to ``pct_low`` for a point
            target.
    """

    anchor_type: AnchorType
    pct_low: float
    pct_high: float

    def __post_init__(self) -> None:
        """Reject ranges that are inverted, negative or absurd."""
        if self.pct_low <= 0:
            raise ValueError(f"pct_low must be above 0, got {self.pct_low}")
        if self.pct_high < self.pct_low:
            raise ValueError(
                f"pct_high {self.pct_high} is below pct_low {self.pct_low}"
            )
        if self.pct_high > MAX_TARGET_FRACTION:
            raise ValueError(
                f"pct_high must be at most {MAX_TARGET_FRACTION} "
                f"({MAX_TARGET_FRACTION:.0%} of the anchor), got {self.pct_high}"
            )


@dataclass(frozen=True, slots=True)
class AbsoluteRange:
    """A target expressed as an absolute range in the channel's own unit.

    Args:
        low: Lower bound, inclusive.
        high: Upper bound, inclusive. Equal to ``low`` for a point target.
        unit: Must be the channel's unit (:data:`CHANNEL_UNITS`) — carried
            explicitly so a stored target is self-describing.
    """

    low: float
    high: float
    unit: ChannelUnit

    def __post_init__(self) -> None:
        """Reject inverted ranges."""
        if self.high < self.low:
            raise ValueError(f"high {self.high} is below low {self.low}")


#: A prescribed target on one channel.
type Target = PercentOfAnchor | AbsoluteRange

#: Targets keyed by channel. At most one target per channel per step.
type Targets = Mapping[Channel, Target]

_NO_TARGETS: Targets = MappingProxyType({})


def _target_kind(target: Target) -> str:
    """Name what kind of quantity a target is, for an error message."""
    if isinstance(target, PercentOfAnchor):
        return f"a percentage of {target.anchor_type.value}"
    return f"an absolute range in {target.unit.value}"


def validate_targets(targets: Targets, *, where: str) -> None:
    """Check every target against the channel it is prescribed on.

    Args:
        targets: The per-channel targets to check.
        where: What to name in the error message.

    Raises:
        ValueError: When a percentage target names an anchor the channel does
            not derive from, or an absolute target uses the wrong unit or an
            implausible value.
    """
    for channel, target in targets.items():
        if isinstance(target, PercentOfAnchor):
            allowed = CHANNEL_ANCHORS[channel]
            if target.anchor_type not in allowed:
                names = ", ".join(sorted(anchor.value for anchor in allowed))
                raise ValueError(
                    f"{where}: {channel.value} cannot be prescribed as a "
                    f"percentage of {target.anchor_type.value}"
                    + (f"; use one of: {names}" if names else "")
                )
            continue
        expected = CHANNEL_UNITS[channel]
        if target.unit is not expected:
            raise ValueError(
                f"{where}: {channel.value} is measured in {expected.value}, "
                f"not {target.unit.value}"
            )
        low, high = CHANNEL_BOUNDS[channel]
        if not (low <= target.low and target.high <= high):
            raise ValueError(
                f"{where}: {channel.value} target must lie between {low} and "
                f"{high} {expected.value}, got {target.low}-{target.high}"
            )


def _validate_extent(duration_s: int | None, distance_m: float | None) -> None:
    """Check that a step states exactly one plausible extent."""
    if (duration_s is None) == (distance_m is None):
        raise ValueError("a step needs exactly one of duration_s or distance_m")
    if duration_s is not None and not 1 <= duration_s <= MAX_STEP_SECONDS:
        raise ValueError(
            f"duration_s must be between 1 and {MAX_STEP_SECONDS}, got {duration_s}"
        )
    if distance_m is not None and not 0 < distance_m <= MAX_STEP_METRES:
        raise ValueError(
            f"distance_m must be between 0 and {MAX_STEP_METRES}, got {distance_m}"
        )


@dataclass(frozen=True, slots=True)
class SteadyStep:
    """Hold a set of targets for a duration or a distance.

    Args:
        duration_s: How long, in seconds. Mutually exclusive with
            ``distance_m``; exactly one must be given.
        distance_m: How far, in metres.
        targets: Per-channel targets. May be empty — an unstructured ride is a
            single steady step with nothing prescribed.
        role: What the step is for.
        name: Optional display label (``"Over"``, ``"Z2"``).
    """

    duration_s: int | None = None
    distance_m: float | None = None
    targets: Targets = dataclass_field(default_factory=lambda: _NO_TARGETS)
    role: StepRole = StepRole.WORK
    name: str | None = None

    def __post_init__(self) -> None:
        """Reject steps that state no extent, two extents, or a bad target."""
        _validate_extent(self.duration_s, self.distance_m)
        validate_targets(self.targets, where="steady step")


@dataclass(frozen=True, slots=True)
class RampStep:
    """Move from one set of targets to another over a duration or distance.

    Both ends prescribe the *same* channels **in the same terms**: a ramp that
    starts on power and ends on heart rate is not a ramp, it is two steps, and
    a ramp from ``60 % FTP`` to ``250 W`` has no defined interpolation until
    the anchor resolves (D53). Percentage ends must also name one anchor:
    "from 60 % of FTP to 90 % of LTHR" is two prescriptions in a trench coat.

    Args:
        duration_s: How long, in seconds. Mutually exclusive with
            ``distance_m``.
        distance_m: How far, in metres.
        start_targets: Targets at the beginning of the ramp.
        end_targets: Targets at the end.
        role: What the step is for.
        name: Optional display label.
    """

    start_targets: Targets
    end_targets: Targets
    duration_s: int | None = None
    distance_m: float | None = None
    role: StepRole = StepRole.WORK
    name: str | None = None

    def __post_init__(self) -> None:
        """Reject ramps with no extent, no targets, or mismatched ends."""
        _validate_extent(self.duration_s, self.distance_m)
        if not self.start_targets:
            raise ValueError("a ramp must prescribe at least one channel")
        if set(self.start_targets) != set(self.end_targets):
            raise ValueError(
                "a ramp must start and end on the same channels; got "
                f"{sorted(channel.value for channel in self.start_targets)} and "
                f"{sorted(channel.value for channel in self.end_targets)}"
            )
        for channel, start in self.start_targets.items():
            end = self.end_targets[channel]
            if isinstance(start, PercentOfAnchor) != isinstance(end, PercentOfAnchor):
                raise ValueError(
                    f"a ramp's {channel.value} ends must be the same kind of "
                    f"target; got {_target_kind(start)} and {_target_kind(end)}"
                )
            if (
                isinstance(start, PercentOfAnchor)
                and isinstance(end, PercentOfAnchor)
                and start.anchor_type is not end.anchor_type
            ):
                raise ValueError(
                    f"a ramp's {channel.value} ends must be percentages of one "
                    f"anchor; got {start.anchor_type.value} and "
                    f"{end.anchor_type.value}"
                )
        validate_targets(self.start_targets, where="ramp start")
        validate_targets(self.end_targets, where="ramp end")


@dataclass(frozen=True, slots=True)
class RepeatBlock:
    """Perform ``children`` ``times`` over.

    Args:
        times: How many iterations, at least one.
        children: The steps of one iteration, in order. May contain further
            repeat blocks, up to :data:`MAX_NESTING_DEPTH`.
    """

    times: int
    children: tuple[Step, ...]

    def __post_init__(self) -> None:
        """Reject blocks that repeat nothing, or repeat it implausibly often."""
        if not 1 <= self.times <= MAX_REPEAT_TIMES:
            raise ValueError(
                f"times must be between 1 and {MAX_REPEAT_TIMES}, got {self.times}"
            )
        if not self.children:
            raise ValueError("a repeat block needs at least one child step")


#: Any step in the tree.
type Step = SteadyStep | RampStep | RepeatBlock
#: A step that does something, as opposed to grouping steps that do.
type LeafStep = SteadyStep | RampStep


@dataclass(frozen=True, slots=True)
class EnduranceWorkout:
    """A complete structured endurance prescription."""

    steps: tuple[Step, ...]

    def __post_init__(self) -> None:
        """Reject empty, over-nested and over-long workouts."""
        if not self.steps:
            raise ValueError("a workout needs at least one step")
        depth = max(_depth(step) for step in self.steps)
        if depth > MAX_NESTING_DEPTH:
            raise ValueError(
                f"repeat blocks may nest at most {MAX_NESTING_DEPTH} deep, got {depth}"
            )
        count = sum(_leaf_count(step) for step in self.steps)
        if count > MAX_FLAT_STEPS:
            raise ValueError(
                f"a workout may flatten to at most {MAX_FLAT_STEPS} steps, got {count}"
            )


@dataclass(frozen=True, slots=True)
class FlatStep:
    """One executable step, with where it came from.

    Args:
        index: 0-based position in the flattened sequence.
        step: The leaf step itself.
        path: Index path through the *tree* — ``(2, 0)`` is the first child of
            the third top-level step. Identifies the prescription a flat step
            came from; several flat steps share a path when a block repeats.
        repetition: 1-based iteration number of each enclosing repeat block,
            outermost first. Empty for a step outside every block. This is
            what makes "the last rep versus the first" answerable without
            re-walking the tree (WP-7's `pacing` axis).
    """

    index: int
    step: LeafStep
    path: tuple[int, ...]
    repetition: tuple[int, ...]

    @property
    def block(self) -> tuple[int, ...]:
        """Tree path of the **outermost repeat block** this step sits in.

        Empty for a step outside every block. Every ancestor of a leaf is a
        :class:`RepeatBlock` — nothing else has children — so the outermost one
        is always the top-level element ``path[:1]`` names.

        :attr:`repetition` counts iterations of *this* block and of nothing
        else, so it only identifies a repetition when read beside it: two
        sibling blocks both number their iterations from 1, and grouping on the
        number alone concatenates the first sprint with the first threshold
        effort (WP-7's `pacing` axis).
        """
        return self.path[:1] if self.repetition else ()

    @property
    def role(self) -> StepRole:
        """The leaf step's role."""
        return self.step.role

    @property
    def duration_s(self) -> int | None:
        """The leaf step's duration, if it is time-based."""
        return self.step.duration_s

    @property
    def distance_m(self) -> float | None:
        """The leaf step's distance, if it is distance-based.

        The symmetric half of :attr:`duration_s`: a step states exactly one
        extent, and a consumer reading only one of the two would silently skip
        every step prescribed the other way.
        """
        return self.step.distance_m

    @property
    def start_targets(self) -> Targets:
        """Targets at the start of the step (the only targets, if steady)."""
        if isinstance(self.step, RampStep):
            return self.step.start_targets
        return self.step.targets

    @property
    def end_targets(self) -> Targets:
        """Targets at the end of the step (the only targets, if steady)."""
        if isinstance(self.step, RampStep):
            return self.step.end_targets
        return self.step.targets

    @property
    def is_ramp(self) -> bool:
        """Whether the step's targets move across it."""
        return isinstance(self.step, RampStep)


def _depth(step: Step) -> int:
    """Nesting depth of a step: 0 for a leaf, 1 + the deepest child."""
    if isinstance(step, RepeatBlock):
        return 1 + max(_depth(child) for child in step.children)
    return 0


def _leaf_count(step: Step) -> int:
    """How many flat steps this step expands to."""
    if isinstance(step, RepeatBlock):
        return step.times * sum(_leaf_count(child) for child in step.children)
    return 1


def _walk(
    steps: Sequence[Step], path: tuple[int, ...], repetition: tuple[int, ...]
) -> Iterator[tuple[LeafStep, tuple[int, ...], tuple[int, ...]]]:
    """Yield every leaf below ``steps`` with its path and repetition."""
    for index, step in enumerate(steps):
        here = (*path, index)
        if isinstance(step, RepeatBlock):
            for iteration in range(1, step.times + 1):
                yield from _walk(step.children, here, (*repetition, iteration))
        else:
            yield step, here, repetition


def flatten(workout: EnduranceWorkout) -> list[FlatStep]:
    """Expand the tree into the sequence of steps the athlete performs.

    Repeat blocks expand into one copy of their children per iteration; ramps
    are left whole (see the module docstring).

    Args:
        workout: The structured workout.

    Returns:
        The leaves in execution order, each carrying its tree path and the
        repeat iterations it sits inside.
    """
    return [
        FlatStep(index=index, step=step, path=path, repetition=repetition)
        for index, (step, path, repetition) in enumerate(_walk(workout.steps, (), ()))
    ]


def expand(workout: EnduranceWorkout) -> EnduranceWorkout:
    """Return the same prescription with every repeat block written out.

    Idempotent: the result contains no repeat blocks, so expanding it again
    returns an equal workout. That, plus ``flatten(w) == flatten(expand(w))``
    step for step, is the round-trip the property tests assert.
    """
    return EnduranceWorkout(steps=tuple(flat.step for flat in flatten(workout)))


def total_duration_s(workout: EnduranceWorkout) -> int | None:
    """Total prescribed duration in seconds, or ``None`` if any step is distance-based.

    ``None`` rather than a partial sum: a workout that mixes "20 minutes" with
    "5 km" has no duration until the ride happens, and a number that silently
    ignores half the prescription is worse than no number.
    """
    total = 0
    for flat in flatten(workout):
        if flat.step.duration_s is None:
            return None
        total += flat.step.duration_s
    return total


#: Either discipline's prescription, as stored on a workout or a planned session.
type WorkoutBody = EnduranceWorkout | StrengthWorkout


def discipline_of(body: WorkoutBody) -> Discipline:
    """Return the discipline a workout body belongs to."""
    if isinstance(body, StrengthWorkout):
        return Discipline.STRENGTH
    return Discipline.CYCLING


def referenced_anchor_types(body: WorkoutBody) -> frozenset[AnchorType]:
    """Return every anchor type the body's targets are a percentage of.

    These are the anchors a planned session must pin at creation time
    (build-plan invariant 4): without them the prescription cannot be resolved
    to numbers, so the frozen version of each is what makes a score
    reproducible.
    """
    if isinstance(body, StrengthWorkout):
        return frozenset()
    anchors: set[AnchorType] = set()
    for flat in flatten(body):
        for targets in (flat.start_targets, flat.end_targets):
            anchors.update(
                target.anchor_type
                for target in targets.values()
                if isinstance(target, PercentOfAnchor)
            )
    return frozenset(anchors)


# --- serialization ------------------------------------------------------------
#
# Tagged unions throughout: every polymorphic node carries a `kind`. The wire
# form is what is stored in the database and what the API schema mirrors, so it
# is written out explicitly rather than derived from the dataclass fields — a
# field rename must be a deliberate migration, not an accidental one.


def target_to_json(target: Target) -> dict[str, Any]:
    """Serialize one channel target."""
    if isinstance(target, PercentOfAnchor):
        return {
            "kind": "percent_of_anchor",
            "anchor_type": target.anchor_type.value,
            "pct_low": target.pct_low,
            "pct_high": target.pct_high,
        }
    return {
        "kind": "absolute",
        "low": target.low,
        "high": target.high,
        "unit": target.unit.value,
    }


_PERCENT_FIELDS = frozenset({"kind", "anchor_type", "pct_low", "pct_high"})
_ABSOLUTE_FIELDS = frozenset({"kind", "low", "high", "unit"})


def target_from_json(document: Any, path: str) -> Target:
    """Deserialize one channel target.

    Raises:
        ValueError: When the document is not a legal target.
    """
    body = as_mapping(document, path)
    kind = as_str(field(body, "kind", path), f"{path}.kind")
    if kind == "percent_of_anchor":
        no_extra_fields(body, _PERCENT_FIELDS, path)
        anchor_type = as_enum(
            AnchorType, field(body, "anchor_type", path), f"{path}.anchor_type"
        )
        pct_low = as_float(field(body, "pct_low", path), f"{path}.pct_low")
        pct_high = as_float(field(body, "pct_high", path), f"{path}.pct_high")
        with located(path):
            return PercentOfAnchor(
                anchor_type=anchor_type, pct_low=pct_low, pct_high=pct_high
            )
    if kind == "absolute":
        no_extra_fields(body, _ABSOLUTE_FIELDS, path)
        low = as_float(field(body, "low", path), f"{path}.low")
        high = as_float(field(body, "high", path), f"{path}.high")
        unit = as_enum(ChannelUnit, field(body, "unit", path), f"{path}.unit")
        with located(path):
            return AbsoluteRange(low=low, high=high, unit=unit)
    raise ValueError(
        f"{path}.kind: {kind!r} is not one of: percent_of_anchor, absolute"
    )


def targets_to_json(targets: Targets) -> dict[str, Any]:
    """Serialize a per-channel target map, channels in a stable order."""
    return {
        channel.value: target_to_json(targets[channel])
        for channel in Channel
        if channel in targets
    }


def targets_from_json(document: Any, path: str) -> Targets:
    """Deserialize a per-channel target map.

    Raises:
        ValueError: When a key is not a channel, or a value is not a target.
    """
    body = as_mapping(document, path)
    return MappingProxyType(
        {
            as_enum(Channel, name, path): target_from_json(value, f"{path}.{name}")
            for name, value in body.items()
        }
    )


def _extent_to_json(step: LeafStep, document: dict[str, Any]) -> None:
    """Write whichever extent the step states into ``document``."""
    if step.duration_s is not None:
        document["duration_s"] = step.duration_s
    else:
        document["distance_m"] = step.distance_m


def step_to_json(step: Step) -> dict[str, Any]:
    """Serialize one step of the tree, recursively."""
    if isinstance(step, RepeatBlock):
        return {
            "kind": "repeat",
            "times": step.times,
            "children": [step_to_json(child) for child in step.children],
        }
    document: dict[str, Any] = {
        "kind": "ramp" if isinstance(step, RampStep) else "steady"
    }
    _extent_to_json(step, document)
    if isinstance(step, RampStep):
        document["start_targets"] = targets_to_json(step.start_targets)
        document["end_targets"] = targets_to_json(step.end_targets)
    else:
        document["targets"] = targets_to_json(step.targets)
    document["role"] = step.role.value
    if step.name is not None:
        document["name"] = step.name
    return document


_STEADY_FIELDS = frozenset(
    {"kind", "duration_s", "distance_m", "targets", "role", "name"}
)
_RAMP_FIELDS = frozenset(
    {"kind", "duration_s", "distance_m", "start_targets", "end_targets", "role", "name"}
)
_REPEAT_FIELDS = frozenset({"kind", "times", "children"})


def step_from_json(document: Any, path: str, depth: int = 0) -> Step:
    """Deserialize one step of the tree, recursively.

    Args:
        document: The step, as decoded JSON.
        path: Where in the enclosing document this step sits.
        depth: How many repeat blocks enclose it. Checked **here** rather than
            only in :meth:`EnduranceWorkout.__post_init__`, because this
            function recurses on user-supplied JSON: a document nested a few
            hundred levels deep would otherwise exhaust the interpreter stack
            and raise `RecursionError` before there was a workout to validate.

    Raises:
        ValueError: When the document is not a legal step, or nests past
            :data:`MAX_NESTING_DEPTH`.
    """
    body = as_mapping(document, path)
    kind = as_str(field(body, "kind", path), f"{path}.kind")
    if kind == "repeat":
        no_extra_fields(body, _REPEAT_FIELDS, path)
        if depth >= MAX_NESTING_DEPTH:
            raise ValueError(
                f"{path}: repeat blocks may nest at most {MAX_NESTING_DEPTH} deep"
            )
        children = as_sequence(field(body, "children", path), f"{path}.children")
        times = as_int(field(body, "times", path), f"{path}.times")
        decoded = tuple(
            step_from_json(child, f"{path}.children[{index}]", depth + 1)
            for index, child in enumerate(children)
        )
        with located(path):
            return RepeatBlock(times=times, children=decoded)
    duration_s = optional(body, "duration_s")
    distance_m = optional(body, "distance_m")
    role = optional(body, "role")
    name = optional(body, "name")
    common: dict[str, Any] = {
        "duration_s": (
            None if duration_s is None else as_int(duration_s, f"{path}.duration_s")
        ),
        "distance_m": (
            None if distance_m is None else as_float(distance_m, f"{path}.distance_m")
        ),
        "role": (
            StepRole.WORK if role is None else as_enum(StepRole, role, f"{path}.role")
        ),
        "name": None if name is None else as_str(name, f"{path}.name"),
    }
    if kind == "steady":
        no_extra_fields(body, _STEADY_FIELDS, path)
        targets = optional(body, "targets")
        decoded_targets = (
            _NO_TARGETS
            if targets is None
            else targets_from_json(targets, f"{path}.targets")
        )
        with located(path):
            return SteadyStep(targets=decoded_targets, **common)
    if kind == "ramp":
        no_extra_fields(body, _RAMP_FIELDS, path)
        start_targets = targets_from_json(
            field(body, "start_targets", path), f"{path}.start_targets"
        )
        end_targets = targets_from_json(
            field(body, "end_targets", path), f"{path}.end_targets"
        )
        with located(path):
            return RampStep(
                start_targets=start_targets, end_targets=end_targets, **common
            )
    raise ValueError(f"{path}.kind: {kind!r} is not one of: steady, ramp, repeat")


def endurance_workout_to_json(workout: EnduranceWorkout) -> dict[str, Any]:
    """Serialize an endurance workout (without the discipline tag)."""
    return {"steps": [step_to_json(step) for step in workout.steps]}


def endurance_workout_from_json(document: Any, path: str = "") -> EnduranceWorkout:
    """Deserialize an endurance workout.

    Raises:
        ValueError: When the document is not a legal endurance workout.
    """
    body = as_mapping(document, path)
    where = path or "workout"
    no_extra_fields(body, frozenset({"steps"}), where)
    prefix = f"{path}.steps" if path else "steps"
    steps = as_sequence(field(body, "steps", path), prefix)
    decoded = tuple(
        step_from_json(step, f"{prefix}[{index}]") for index, step in enumerate(steps)
    )
    with located(where):
        return EnduranceWorkout(steps=decoded)


def workout_body_to_json(body: WorkoutBody) -> dict[str, Any]:
    """Serialize either discipline's prescription, tagged with its discipline."""
    discipline = discipline_of(body)
    if isinstance(body, StrengthWorkout):
        return {"discipline": discipline.value, **strength_workout_to_json(body)}
    return {"discipline": discipline.value, **endurance_workout_to_json(body)}


def workout_body_from_json(document: Any, path: str = "structure") -> WorkoutBody:
    """Deserialize either discipline's prescription.

    The ``discipline`` tag selects the shape; it is required, because a
    document with neither ``steps`` nor ``groups`` would otherwise produce an
    error about the wrong half of the model.

    Raises:
        ValueError: When the document is not a legal workout body.
    """
    envelope = as_mapping(document, path)
    discipline = as_enum(
        Discipline, field(envelope, "discipline", path), f"{path}.discipline"
    )
    inner = {name: value for name, value in envelope.items() if name != "discipline"}
    if discipline is Discipline.STRENGTH:
        return strength_workout_from_json(inner, path)
    return endurance_workout_from_json(inner, path)

"""Loading arithmetic: which weights a gym can actually put in your hands.

A prescription says "100 kg × 5"; the gym says what the bar, the plates, the
dumbbell rack and the pin stack can make. Everything that turns a computed
number into something the athlete can load goes through here — the session
packet, the progression suggestion, the step-down after a missed set, a swap
to a different implement — so the rule is stated once.

The phone computes the same numbers offline (`frontend/lib/strength`), and
`scripts/emit_loading_vectors.py` writes the cases both sides are tested
against. Change anything here that moves an output and the vectors test fails
until `just loading-vectors` is re-run, at which point the TypeScript suite
fails until its twin agrees.

**Arithmetic is in integer grams.** Each loadable weight is built from
integer grams and only divided by 1000 at the end, so ``20 + 2 × 1.25`` is
22.5 exactly rather than whatever three float additions round to, and the
TypeScript twin — which follows the same steps — produces the same double.
Targets stay floats: they are compared to loadable weights, never summed.

Percentages are fractions (``0.925`` is 92.5 %), as everywhere in the domain.
"""

import bisect
import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from typing import Final, NamedTuple

from app.domain.rules import STARTING_STEP_EASY, STARTING_STEP_GOOD

#: Kilograms in one international avoirdupois pound — exact by definition
#: (1959 agreement), so a kg↔lb round trip loses only float precision.
KG_PER_LB: Final = 0.45359237

#: Largest fraction `percent_of_set` accepts. A back-off set heavier than half
#: again the top set is not a back-off set; above this it is a typo (92.5
#: typed as a percentage rather than 0.925).
MAX_PERCENT_OF_SET: Final = 1.5

#: Most kinds of plate one gym may declare, and most pairs of one kind — a
#: bound on the plate search, which grows with both.
MAX_PLATE_KINDS: Final = 12
MAX_PAIRS_PER_KIND: Final = 20

#: Weights are compared in whole grams so float noise cannot split a tie.
GRAMS_PER_KG: Final = 1000

#: A plate pair puts one plate on each of the bar's two sides.
BAR_SIDES: Final = 2

#: How much heavier `starting_step` goes for each answer to "how did that
#: feel?". "hard" is not a percentage: it is one loadable step down, so it can
#: never land on the weight the athlete just found too hard. The percentages
#: are training rules and live in the registry (`app.domain.rules`).
STARTING_STEP_INCREASE: Final = {
    "easy": STARTING_STEP_EASY.value,
    "good": STARTING_STEP_GOOD.value,
}
STARTING_STEP_ANSWERS: Final = ("easy", "good", "hard")


class LoadingError(ValueError):
    """A loading question with no meaningful answer.

    A `ValueError`, as every domain rule violation is, so `domain_rules()`
    turns it into a 422 at a service boundary.
    """


class Equipment(StrEnum):
    """How a weight is made up, which decides what is loadable.

    A barbell is a bar plus pairs of plates; dumbbells come in fixed rack
    steps; a stack has a pin every step. Only the barbell has a plate list.
    """

    BARBELL = "barbell"
    DUMBBELL = "dumbbell"
    STACK = "stack"


def _grams(kg: float) -> int:
    return round(kg * GRAMS_PER_KG)


@dataclass(frozen=True, slots=True)
class Gym:
    """What one gym can load.

    Attributes:
        bar_kg: The empty barbell.
        plate_pairs: ``(plate_kg, pairs)`` for each plate kind; a pair is one
            plate for each side.
        dumbbell_step_kg: Rack spacing; the lightest dumbbell is one step.
        dumbbell_max_kg: The heaviest dumbbell.
        stack_step_kg: Pin spacing; the lightest pin is one step.
        stack_max_kg: The whole stack.
    """

    bar_kg: float
    plate_pairs: tuple[tuple[float, int], ...]
    dumbbell_step_kg: float
    dumbbell_max_kg: float
    stack_step_kg: float
    stack_max_kg: float

    def __post_init__(self) -> None:
        """Refuse a gym that cannot load anything sensible."""
        if not (math.isfinite(self.bar_kg) and self.bar_kg > 0):
            raise LoadingError(f"the bar must weigh more than 0 kg, got {self.bar_kg}")
        if len(self.plate_pairs) > MAX_PLATE_KINDS:
            raise LoadingError(f"a gym may declare at most {MAX_PLATE_KINDS} plates")
        for plate_kg, pairs in self.plate_pairs:
            if not (math.isfinite(plate_kg) and plate_kg > 0):
                raise LoadingError(f"a plate must weigh more than 0 kg, got {plate_kg}")
            if not 1 <= pairs <= MAX_PAIRS_PER_KIND:
                raise LoadingError(
                    f"a plate kind needs 1 to {MAX_PAIRS_PER_KIND} pairs, got {pairs}"
                )
        kinds = [_grams(kg) for kg, _ in self.plate_pairs]
        if len(set(kinds)) != len(kinds):
            raise LoadingError("each plate weight may be declared once")
        for name, step, top in (
            ("dumbbell", self.dumbbell_step_kg, self.dumbbell_max_kg),
            ("stack", self.stack_step_kg, self.stack_max_kg),
        ):
            if not (math.isfinite(step) and step > 0 and math.isfinite(top)):
                raise LoadingError(f"the {name} step must be more than 0 kg")
            if top < step:
                raise LoadingError(f"the heaviest {name} is lighter than one step")


DEFAULT_GYM: Final = Gym(
    bar_kg=20.0,
    plate_pairs=(
        (25.0, 4),
        (20.0, 2),
        (10.0, 2),
        (5.0, 2),
        (2.5, 2),
        (1.25, 2),
    ),
    dumbbell_step_kg=2.0,
    dumbbell_max_kg=50.0,
    stack_step_kg=5.0,
    stack_max_kg=150.0,
)
"""The gym every athlete trains in until places exist.

A constant, not a row: a ``places`` table (home, park, travel gym) arrives
with step 3 of the strength direction, and building it now would mean a
settings screen and a migration for a choice nobody can make yet. So this is
one typical commercial gym — a 20 kg bar, plates from 1.25 to 25 kg (no 15s,
which plenty of gyms lack), dumbbells in 2 kg steps to 50, a stack in 5 kg
steps to 150 — and every function takes a `Gym` so places slot in as an
argument rather than a rewrite.
"""


class Loading(NamedTuple):
    """The weight to load for a target, and how it is made up.

    Attributes:
        kg: The nearest loadable weight.
        capped: True when the target was heavier than anything the gym can
            load, so `kg` is the gym's maximum rather than the target.
        plates_per_side: For a barbell, the plates on each side, heaviest
            first; ``None`` for dumbbells and stacks.
    """

    kg: float
    capped: bool
    plates_per_side: tuple[float, ...] | None


def _better(one: tuple[int, ...], other: tuple[int, ...]) -> bool:
    """Whether plate list `one` beats `other`: fewer plates, then heavier first."""
    if len(one) != len(other):
        return len(one) < len(other)
    return one > other


@cache
def _barbell_sides(gym: Gym) -> dict[int, tuple[int, ...]]:
    """Every per-side load in grams, mapped to the best plates that make it.

    "Best" is fewest plates, ties going to the list with heavier plates first:
    fewer plates is less to carry and slide on, and between equals the heavier
    list is the one people actually load. Processing plate kinds heaviest
    first keeps that order a prefix property — for a given partial sum the
    best prefix stays best whatever lighter plates follow — so one pass is
    exact.
    """
    best: dict[int, tuple[int, ...]] = {0: ()}
    for plate, pairs in sorted(
        ((_grams(kg), pairs) for kg, pairs in gym.plate_pairs), reverse=True
    ):
        grown: dict[int, tuple[int, ...]] = {}
        for side, plates in best.items():
            for count in range(pairs + 1):
                total = side + plate * count
                candidate = plates + (plate,) * count
                current = grown.get(total)
                if current is None or _better(candidate, current):
                    grown[total] = candidate
        best = grown
    return best


@cache
def _loadable_grams(equipment: Equipment, gym: Gym) -> tuple[int, ...]:
    match equipment:
        case Equipment.BARBELL:
            bar = _grams(gym.bar_kg)
            return tuple(sorted(bar + BAR_SIDES * side for side in _barbell_sides(gym)))
        case Equipment.DUMBBELL:
            step, top = _grams(gym.dumbbell_step_kg), _grams(gym.dumbbell_max_kg)
        case Equipment.STACK:
            step, top = _grams(gym.stack_step_kg), _grams(gym.stack_max_kg)
    return tuple(step * k for k in range(1, top // step + 1))


def loadable_weights(
    equipment: Equipment = Equipment.BARBELL, gym: Gym = DEFAULT_GYM
) -> tuple[float, ...]:
    """Every weight `gym` can load on `equipment`, lightest first, in kg."""
    return tuple(grams / GRAMS_PER_KG for grams in _loadable_grams(equipment, gym))


def _check_target(target_kg: float) -> None:
    if not (math.isfinite(target_kg) and target_kg >= 0):
        raise LoadingError(
            f"a target must be a weight of 0 kg or more, got {target_kg}"
        )


def _nearest(target_kg: float, weights: Sequence[float]) -> tuple[float, bool]:
    if target_kg >= weights[-1]:
        return weights[-1], target_kg > weights[-1]
    above = bisect.bisect_right(weights, target_kg)
    if above == 0:
        return weights[0], False
    low, high = weights[above - 1], weights[above]
    if low == target_kg:
        return low, False
    return (high if high - target_kg < target_kg - low else low), False


def loading_for(
    target_kg: float,
    equipment: Equipment = Equipment.BARBELL,
    gym: Gym = DEFAULT_GYM,
) -> Loading:
    """The loadable weight nearest `target_kg`, whether it was capped, and how.

    Rounds as `nearest_loadable` does. A target below the lightest loadable
    weight (the empty bar, the lightest dumbbell, the first pin) gets that
    weight; one above the heaviest gets the heaviest with ``capped`` set, so a
    caller can say so instead of presenting the maximum as the answer.

    Raises:
        LoadingError: If `target_kg` is negative or not finite.
    """
    _check_target(target_kg)
    kg, capped = _nearest(target_kg, loadable_weights(equipment, gym))
    plates = None
    if equipment is Equipment.BARBELL:
        side = (_grams(kg) - _grams(gym.bar_kg)) // BAR_SIDES
        plates = tuple(grams / GRAMS_PER_KG for grams in _barbell_sides(gym)[side])
    return Loading(kg=kg, capped=capped, plates_per_side=plates)


def nearest_loadable(
    target_kg: float,
    equipment: Equipment = Equipment.BARBELL,
    gym: Gym = DEFAULT_GYM,
) -> float:
    """The loadable weight closest to `target_kg`.

    **Nearest wins; a tie rounds down.** A target exactly between
    two loadable weights gets the lighter one: every number this rounds is
    already a decision — a progression increase that a cap held back, a
    percentage of a top set — and rounding a tie up would silently undo the
    cap, adding weight the rule chose not to add. Rounding *always* down was
    the other option and is worse: 101.3 would become 100 when 102.5 is
    nearer. One consequence, kept on purpose: an increase small enough to
    round back to the current weight stays at the current weight rather than
    being forced up a step. Pinned by ``test_tie_rounds_down``.

    Raises:
        LoadingError: If `target_kg` is negative or not finite.
    """
    return loading_for(target_kg, equipment, gym).kg


def plates_per_side(
    kg: float, equipment: Equipment = Equipment.BARBELL, gym: Gym = DEFAULT_GYM
) -> list[float]:
    """The plates on each side of the bar for `kg`, heaviest first.

    `kg` is first rounded to the nearest loadable weight, so the list always
    describes a bar that can be built; an empty list is the empty bar.

    Raises:
        LoadingError: For dumbbells and stacks, which have no plates.
    """
    if equipment is not Equipment.BARBELL:
        raise LoadingError(f"a {equipment.value} has no plates")
    plates = loading_for(kg, equipment, gym).plates_per_side
    return list(plates or ())


def step_down(
    kg: float, equipment: Equipment = Equipment.BARBELL, gym: Gym = DEFAULT_GYM
) -> float:
    """One loadable weight lighter than `kg` (after rounding it), floored.

    At the lightest loadable weight there is nothing lighter, so it stays
    there: the empty bar steps down to the empty bar.
    """
    weights = loadable_weights(equipment, gym)
    current = nearest_loadable(kg, equipment, gym)
    index = weights.index(current)
    return weights[max(index - 1, 0)]


def starting_step(
    kg: float,
    answer: str,
    equipment: Equipment = Equipment.BARBELL,
    gym: Gym = DEFAULT_GYM,
) -> float:
    """The next weight to try while finding a starting weight.

    "easy" adds a fifth and "good" a tenth, each rounded to loadable; "hard"
    is one loadable step down (`step_down`).

    Raises:
        LoadingError: If `answer` is not one of `STARTING_STEP_ANSWERS`.
    """
    if answer == "hard":
        return step_down(kg, equipment, gym)
    increase = STARTING_STEP_INCREASE.get(answer)
    if increase is None:
        raise LoadingError(
            f"unknown starting-step answer {answer!r}; "
            f"expected one of {', '.join(STARTING_STEP_ANSWERS)}"
        )
    _check_target(kg)
    return nearest_loadable(kg * (1 + increase), equipment, gym)


def percent_of_set(
    kg: float,
    fraction: float,
    equipment: Equipment = Equipment.BARBELL,
    gym: Gym = DEFAULT_GYM,
) -> float:
    """`fraction` of a set's weight, rounded to loadable.

    Raises:
        LoadingError: If `fraction` is not in ``(0, MAX_PERCENT_OF_SET]``.
    """
    if not (math.isfinite(fraction) and 0 < fraction <= MAX_PERCENT_OF_SET):
        raise LoadingError(
            f"a percent of a set is a fraction above 0 and at most "
            f"{MAX_PERCENT_OF_SET}, got {fraction}"
        )
    _check_target(kg)
    return nearest_loadable(kg * fraction, equipment, gym)


def kg_to_lb(kg: float) -> float:
    """Kilograms in pounds."""
    return kg / KG_PER_LB


def lb_to_kg(lb: float) -> float:
    """Pounds in kilograms."""
    return lb * KG_PER_LB

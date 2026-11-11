"""Swaps: which library movements stand in for another, in what order.

Pure, and shared by the session packet (three swaps per line, so the phone
can swap with no signal), the pain card's offer (`app.services.safety`) and
the coach's `suggest_substitutes` tool — one ranking, so the three never
disagree. Pinned by `tests/unit/test_domain_swaps.py`, golden over the
bundled catalogue.

**Valid first, then ranked.** A candidate is *valid* (:func:`is_valid_substitute`)
when it is not the exercise itself, shares at least one main muscle group
with it, does the same movement or one of its :data:`PATTERN_FAMILIES`, needs
nothing the gym lacks, makes no demand among the athlete's restrictions, and
is not on the avoid list. An invalid movement is never offered to fill the
three slots: an exercise with fewer than three valid alternatives offers
fewer. Topping the list up with "something for the legs" was the alternative
and was turned down — a swap the athlete has to reject is a fourth tap and a
reason to stop trusting the other three.

**The ranking is lexicographic** (:func:`_rank_key`), so each key only breaks
the previous one's ties and the order can be explained in one sentence per
key: the remembered pick first; then "less often" sinks below everything
else; then more shared main groups; the same pattern before a related one;
the same mechanic; fewer *new* demands (restriction tags the original does
not already make); an exercise the athlete has logged before; the most
shared muscle weight; and finally the slug, so the order never depends on
the catalogue's order. A weighted score was the alternative: it would rank
just as well and explain nothing, and "why is this first?" is a question the
athlete and the coach both ask.

**Ease back in** (:func:`ease_back_ladder`) is a short list of easier
variations, easiest first, that the athlete climbs one tap at a time —
never on a timer and never on what was logged, because only the athlete
knows whether the last step felt fine.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from app.domain.muscles import main_groups
from app.domain.safety import joint_of, plural
from app.domain.strength import (
    Exercise,
    ExerciseEquipment,
    LoadType,
    MovementPattern,
    Restriction,
)

#: Swaps carried per packet line and offered by the coach's tool.
SWAP_COUNT: Final = 3

#: Steps an *Ease back in* ladder holds below the exercise itself.
MAX_EASE_BACK_STEPS: Final = 3

#: What the one built-in gym has (`app.domain.loading.DEFAULT_GYM`): all of
#: it, until places arrive with step 3. Every function takes the set, so a
#: taken rack is ``DEFAULT_GYM_EQUIPMENT - {RACK}``.
DEFAULT_GYM_EQUIPMENT: Final = frozenset(ExerciseEquipment)

#: Patterns a swap may cross between. Anything not listed only swaps within
#: its own pattern. Squat and lunge are both knee-dominant leg work; a
#: vertical and a horizontal pull (or push) train the same main groups from a
#: different angle; the three anti-movement core patterns are interchangeable
#: bracing work. Hinge stays alone on purpose: a deadlift for a squat moves
#: the work off the quads, which shared main groups alone would not catch.
PATTERN_FAMILIES: Final[tuple[frozenset[MovementPattern], ...]] = (
    frozenset({MovementPattern.SQUAT, MovementPattern.LUNGE}),
    frozenset({MovementPattern.HORIZONTAL_PULL, MovementPattern.VERTICAL_PULL}),
    frozenset({MovementPattern.HORIZONTAL_PUSH, MovementPattern.VERTICAL_PUSH}),
    frozenset(
        {
            MovementPattern.ANTI_EXTENSION,
            MovementPattern.ANTI_ROTATION,
            MovementPattern.ANTI_LATERAL_FLEXION,
        }
    ),
)

#: How much a load type asks of the athlete, for *Ease back in*: moving your
#: own body is easier to come back to than a loaded bar.
LOAD_DEMAND: Final[Mapping[LoadType, int]] = {
    LoadType.UNLOADED: 0,
    LoadType.BODYWEIGHT: 1,
    LoadType.BAND: 1,
    LoadType.DUMBBELL: 2,
    LoadType.KETTLEBELL: 2,
    LoadType.STACK: 2,
    LoadType.PLATE_LOADED: 3,
    LoadType.BARBELL: 4,
}


#: The card kind a "rest of block" swap queues.
SWAP_REST_OF_BLOCK: Final = "swap_rest_of_block"


class SwapScope(StrEnum):
    """How long a swap made in the gym is meant to hold.

    ``session`` is the default — the rack is taken *today* — and changes
    nothing beyond remembering the pick. ``rest_of_block`` is a wish to
    change the plan, which the gym floor does not do on its own: it is
    stored as a pending card (:data:`SWAP_REST_OF_BLOCK`) for the athlete to
    confirm off the gym floor and the coach to read, never applied to the
    planned sessions directly. Editing every remaining session from a tap
    between two sets was the alternative; a plan change has its own reviewed
    path (proposals), and a swap picked in a hurry should not take it.
    """

    SESSION = "session"
    REST_OF_BLOCK = "rest_of_block"


def rest_of_block_card(original: str, swap: str) -> tuple[str, str]:
    """The pending card's headline and "because" sentence for a block-long swap."""
    return (
        f"Keep {plural(swap)} instead of {plural(original)} for the rest of the block?",
        (
            "You swapped them in the gym and asked to keep the swap. Accepting "
            f"offers {plural(swap)} first whenever {plural(original)} come up; "
            "your coach sees the request to change the plan."
        ),
    )


@dataclass(frozen=True, slots=True)
class SwapContext:
    """Everything about the athlete and the room a ranking reads.

    Args:
        equipment: What the gym has today.
        restrictions: Demands the athlete may not make; a movement with any
            of them is never offered.
        avoided: Slugs the athlete never wants offered.
        less_often: Slugs offered only after every other valid swap.
        known: Slugs the athlete has logged before.
        remembered: ``{original slug: picked slug}``, the athlete's last pick
            for each exercise; it ranks first while it stays valid.
    """

    equipment: frozenset[ExerciseEquipment] = DEFAULT_GYM_EQUIPMENT
    restrictions: frozenset[Restriction] = frozenset()
    avoided: frozenset[str] = frozenset()
    less_often: frozenset[str] = frozenset()
    known: frozenset[str] = frozenset()
    remembered: Mapping[str, str] = field(default_factory=dict)


def _family(pattern: MovementPattern) -> frozenset[MovementPattern]:
    for family in PATTERN_FAMILIES:
        if pattern in family:
            return family
    return frozenset({pattern})


def is_valid_substitute(
    original: Exercise, candidate: Exercise, context: SwapContext
) -> bool:
    """Whether ``candidate`` may be offered for ``original`` at all."""
    if candidate.id == original.id or candidate.id in context.avoided:
        return False
    if candidate.pattern not in _family(original.pattern):
        return False
    if not main_groups(candidate.muscles) & main_groups(original.muscles):
        return False
    needs = candidate.equipment - {ExerciseEquipment.BODYWEIGHT}
    if not needs <= context.equipment:
        return False
    return not candidate.restrictions & context.restrictions


def _overlap(original: Exercise, candidate: Exercise) -> float:
    return sum(
        min(weight, candidate.muscles.get(region, 0.0))
        for region, weight in original.muscles.items()
    )


def _rank_key(
    original: Exercise, candidate: Exercise, context: SwapContext
) -> tuple[int, int, int, int, int, int, int, float, str]:
    """The ranking's keys, in order (see the module docstring)."""
    return (
        0 if context.remembered.get(original.id) == candidate.id else 1,
        1 if candidate.id in context.less_often else 0,
        -len(main_groups(candidate.muscles) & main_groups(original.muscles)),
        0 if candidate.pattern is original.pattern else 1,
        0 if candidate.mechanic is original.mechanic else 1,
        len(candidate.restrictions - original.restrictions),
        0 if candidate.id in context.known else 1,
        -_overlap(original, candidate),
        candidate.id,
    )


def substitutes(
    original: Exercise,
    catalogue: Iterable[Exercise],
    context: SwapContext | None = None,
    *,
    limit: int | None = SWAP_COUNT,
) -> tuple[Exercise, ...]:
    """The valid swaps for ``original``, best first; ``limit=None`` for all."""
    context = context or SwapContext()
    valid = [c for c in catalogue if is_valid_substitute(original, c, context)]
    ranked = sorted(valid, key=lambda c: _rank_key(original, c, context))
    return tuple(ranked if limit is None else ranked[:limit])


def restrictions_from_pain(painful: Iterable[Exercise]) -> frozenset[Restriction]:
    """The demands to rule out after sharp pain on each of ``painful``.

    The joint each movement loads most plainly (`app.domain.safety.joint_of`)
    and every tag that loads it — the same reading the pain card's headline
    makes, so "knee pain on lunges" rules out deep knee flexion everywhere
    until a session of the painful movement passes without sharp pain.
    """
    ruled_out: set[Restriction] = set()
    for exercise in painful:
        joint = joint_of(exercise)
        if joint is not None:
            ruled_out |= joint[1]
    return frozenset(ruled_out)


def pain_swap(
    exercise: Exercise,
    catalogue: Iterable[Exercise],
    context: SwapContext | None = None,
) -> Exercise | None:
    """The one swap the pain card offers after sharp pain on ``exercise``.

    The ranking's first choice once the painful joint's tags are ruled out
    too; ``None`` when nothing valid spares the joint.
    """
    context = context or SwapContext()
    spared = SwapContext(
        equipment=context.equipment,
        restrictions=context.restrictions | restrictions_from_pain([exercise]),
        avoided=context.avoided,
        less_often=context.less_often,
        known=context.known,
        remembered=context.remembered,
    )
    found = substitutes(exercise, catalogue, spared, limit=1)
    return found[0] if found else None


def _demand(exercise: Exercise) -> tuple[int, int]:
    return len(exercise.restrictions), LOAD_DEMAND[exercise.load_type]


def ease_back_ladder(
    original: Exercise,
    catalogue: Iterable[Exercise],
    context: SwapContext | None = None,
) -> tuple[Exercise, ...]:
    """Easier variations of ``original``, easiest first; empty if none is easier.

    A step is a valid swap (so never avoided, restricted or out of the gym)
    that makes **no demand the original does not**, uses no heavier kind of
    load, and asks less overall — fewer restriction tags, then a lighter kind
    of load. (Fewer tags alone would call a hack squat an easier air squat.) The best-ranked
    :data:`MAX_EASE_BACK_STEPS` of those are kept, then ordered easiest
    first; the athlete's remembered pick plays no part, since a swap for a
    taken rack says nothing about what is easier on a sore knee.
    """
    base = context or SwapContext()
    neutral = SwapContext(
        equipment=base.equipment,
        restrictions=base.restrictions,
        avoided=base.avoided,
        known=base.known,
    )
    easier = [
        c
        for c in substitutes(original, catalogue, neutral, limit=None)
        if c.restrictions <= original.restrictions
        and LOAD_DEMAND[c.load_type] <= LOAD_DEMAND[original.load_type]
        and _demand(c) < _demand(original)
    ]
    kept = easier[:MAX_EASE_BACK_STEPS]
    return tuple(sorted(kept, key=lambda c: (_demand(c), c.id)))

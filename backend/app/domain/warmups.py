"""Warm-up ramps: the sets before the first working set, and which lines get them.

**Which lines.** A line ramps when its ``warmup`` says ``auto``. A line that
says nothing defaults to ``auto`` only if it is the **first main lift of its
pattern** (:func:`app.domain.strength.resolve_main_lines`, patterns as
:data:`~app.domain.strength.MAIN_LIFT_PATTERNS` groups them), and to ``none``
otherwise; ``none`` always wins. This displaces warm-ups on every line: by the
second squat variation, or the leg extension after the squats, the muscle is
already warm, and a ramp there is five sets of junk volume and five more taps
the athlete learns to skip. Explicit ``auto`` is honoured on any line, because
it is the coach saying this one needs it.

**What the ramp is.** For a barbell: the empty bar for
:data:`~app.domain.rules.WARMUP_BAR_REPS` reps, then the steps of
:data:`~app.domain.rules.WARMUP_RAMP` (40 / 60 / 80 % of the working weight).
For dumbbells and a stack there is no bar, so the ramp is the steps alone,
on the rack or the pin. Three rules shape it:

* **A step rounds down to a loadable weight, never to the nearest.** A
  warm-up exists to prepare; erring light costs nothing, erring heavy eats
  into the working sets. (`app.domain.loading.nearest_loadable` rounds to the
  nearest because its inputs are decisions; a warm-up percentage is not one.)
* **A light step is skipped**: one at or below the bar, and one less than two
  loadable steps above the previous warm-up — a single change-plate jump is
  not a warm-up set, and the same weight twice is not a ramp. The rule counts
  in the gym's own steps, so it needs no kilogram constant.
* **Reps follow a step's place in the ramp, not its percentage.** The first
  step after the bar is the 5-rep step whatever percentage survived to fill
  it: a 40 kg lift whose 40 % and 60 % steps vanished still wants a primer of
  five before the work, not a lone double.

So 100 kg ramps 20 × 10, 40 × 5, 60 × 3, 80 × 2 and 40 kg ramps 20 × 10,
30 × 5 (AC-45); a working weight at the bar, or at the lightest dumbbell or
pin, has no warm-ups.

Pure; every training number comes from :mod:`app.domain.rules`.
"""

import bisect
from collections.abc import Mapping
from typing import NamedTuple

from app.domain.loading import (
    DEFAULT_GYM,
    Equipment,
    Gym,
    loadable_weights,
    nearest_loadable,
)
from app.domain.rules import WARMUP_BAR_REPS, WARMUP_RAMP
from app.domain.strength import (
    MAIN_LIFT_PATTERNS,
    Exercise,
    StrengthWorkout,
    WarmupMode,
    resolve_main_lines,
)


class WarmupSet(NamedTuple):
    """One warm-up set: a loadable weight and the reps to do with it."""

    kg: float
    reps: int


def warmup_sets(
    working_kg: float,
    equipment: Equipment = Equipment.BARBELL,
    gym: Gym = DEFAULT_GYM,
) -> tuple[WarmupSet, ...]:
    """The warm-up ramp before a first working set of `working_kg`.

    `working_kg` is rounded to loadable first, so the ramp leads to a weight
    the gym can build. See the module docstring for the rules.

    Raises:
        LoadingError: If `working_kg` is negative or not finite.
    """
    weights = loadable_weights(equipment, gym)
    work = nearest_loadable(working_kg, equipment, gym)
    if work <= weights[0]:
        return ()
    ramp: list[WarmupSet] = []
    previous: int | None = None
    if equipment is Equipment.BARBELL:
        ramp.append(WarmupSet(kg=weights[0], reps=WARMUP_BAR_REPS.value))
        previous = 0
    step_reps = [reps for _, reps in WARMUP_RAMP.value]
    steps = 0
    for fraction, _ in WARMUP_RAMP.value:
        index = bisect.bisect_right(weights, work * fraction) - 1
        if index < 0 or (previous is not None and index - previous <= 1):
            continue
        ramp.append(WarmupSet(kg=weights[index], reps=step_reps[steps]))
        steps += 1
        previous = index
    return tuple(ramp)


def warmup_lines(
    workout: StrengthWorkout, catalogue: Mapping[str, Exercise]
) -> tuple[bool, ...]:
    """Which prescription lines get a warm-up ramp, aligned to ``prescriptions``.

    Explicit ``auto`` and ``none`` win; an unset line ramps only as the first
    main lift of its pattern (module docstring).
    """
    mains = resolve_main_lines(workout, catalogue)
    ramped_patterns: set[object] = set()
    out: list[bool] = []
    for item, main in zip(workout.prescriptions, mains, strict=True):
        entry = catalogue.get(item.exercise_id)
        pattern: object = (
            None
            if entry is None
            else MAIN_LIFT_PATTERNS.get(entry.pattern, entry.pattern)
        )
        first_main = main and pattern not in ramped_patterns
        if main:
            ramped_patterns.add(pattern)
        if item.warmup is WarmupMode.AUTO:
            out.append(True)
        elif item.warmup is WarmupMode.NONE:
            out.append(False)
        else:
            out.append(first_main)
    return tuple(out)

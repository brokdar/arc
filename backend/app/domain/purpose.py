"""The purpose vocabulary: what a session is *for*.

Purpose is the hinge of the whole scoring loop. It is what a planned session
declares, what the purpose template turns into success criteria and applicable
scoring axes (`app.domain.templates`), and what WP-7's verdict rules read. So
it is a closed vocabulary, fixed by the build plan (WP-2.3), not free text.

Each purpose belongs to exactly one discipline. That pairing is what stops a
cycling session being planned with `hypertrophy` intent, and it is checked
rather than trusted for the same reason `app.domain.zones` checks the zone
model against the anchor type: the mismatch is silent otherwise.
"""

from enum import StrEnum

from app.domain.athlete import Discipline


class Purpose(StrEnum):
    """Why a session exists. One vocabulary, two disciplines."""

    # --- endurance (cycling) --------------------------------------------
    RECOVERY = "recovery"
    ENDURANCE = "endurance"
    TEMPO = "tempo"
    SWEET_SPOT = "sweet_spot"
    THRESHOLD = "threshold"
    VO2MAX = "vo2max"
    ANAEROBIC = "anaerobic"
    NEUROMUSCULAR = "neuromuscular"
    UNSTRUCTURED = "unstructured"
    TECHNIQUE = "technique"
    TEST = "test"
    # --- strength -------------------------------------------------------
    MAX_STRENGTH = "max_strength"
    STRENGTH_ENDURANCE = "strength_endurance"
    HYPERTROPHY = "hypertrophy"
    POWER = "power"
    CORE = "core"
    MOBILITY = "mobility"
    CONDITIONING = "conditioning"


#: The endurance half of the vocabulary, in the build plan's order.
ENDURANCE_PURPOSES: tuple[Purpose, ...] = (
    Purpose.RECOVERY,
    Purpose.ENDURANCE,
    Purpose.TEMPO,
    Purpose.SWEET_SPOT,
    Purpose.THRESHOLD,
    Purpose.VO2MAX,
    Purpose.ANAEROBIC,
    Purpose.NEUROMUSCULAR,
    Purpose.UNSTRUCTURED,
    Purpose.TECHNIQUE,
    Purpose.TEST,
)

#: The strength half of the vocabulary, in the build plan's order.
STRENGTH_PURPOSES: tuple[Purpose, ...] = (
    Purpose.MAX_STRENGTH,
    Purpose.STRENGTH_ENDURANCE,
    Purpose.HYPERTROPHY,
    Purpose.POWER,
    Purpose.CORE,
    Purpose.MOBILITY,
    Purpose.CONDITIONING,
)

#: The discipline each purpose belongs to. Total over :class:`Purpose` — a
#: purpose with no discipline could not be scored, so the mapping is exhaustive
#: and `test_every_purpose_has_a_discipline` keeps it that way.
PURPOSE_DISCIPLINE: dict[Purpose, Discipline] = {
    **{purpose: Discipline.CYCLING for purpose in ENDURANCE_PURPOSES},
    **{purpose: Discipline.STRENGTH for purpose in STRENGTH_PURPOSES},
}


def discipline_of(purpose: Purpose) -> Discipline:
    """Return the discipline ``purpose`` belongs to."""
    return PURPOSE_DISCIPLINE[purpose]


def purposes_for(discipline: Discipline) -> tuple[Purpose, ...]:
    """Return the purposes available to one discipline, in vocabulary order."""
    return tuple(
        purpose for purpose, owner in PURPOSE_DISCIPLINE.items() if owner is discipline
    )

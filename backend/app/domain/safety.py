"""Safety on the gym floor: pain, red flags, and which of them wins.

Shared by the session packet, progression and swaps, so it is stated once,
pure, and pinned by `tests/unit/test_safety.py`.

**Pain comes in two kinds** (`app.domain.activity.PainKind`), each with a band
(0–3 / 4–5 / 6+). *Muscle ache or burn* is noted and changes nothing — it is
what training feels like. *Sharp, or in a joint* **stops that exercise for
today** (:func:`sets_left_today` is zero, whatever was prescribed), **blocks
its weight from going up** until a session of it passes without sharp pain
(:func:`blocks_increase`), and queues a priority-1 card offering a swap
(:func:`pain_card`).

**Sharp pain never edits a follow-exactly plan line; it only stops today's
sets.** A follow-exactly plan is someone else's prescription — a physio's, a
coach's — and the athlete agreed to follow it as written. Removing or
rewriting the line after a painful set was the alternative and was turned
down: arc does not know why the line is there, and the person who wrote it is
the one to change it. So the line stays as it is, today's remaining sets of it
are not done, and the card says to ask a physio or doctor if the pain
continues. Pinned by `test_safety.py::test_sharp_pain_leaves_a_follow_exactly_line_unchanged`.

**A red flag blocks all cards and suggestions until it is resolved.** A
symptom from *Stop — something's wrong* ends the session and raises the
athlete's red flag; until it is lowered the only card arc shows is the red
flag's own follow-up (:func:`card_allowed`, applied by `app.services.cards`),
and the coaching agent may not add or intensify training
(`app.services.proposals._check_red_flag`). A warning shown beside business as
usual was the alternative: it is one more thing to dismiss, and a "try heavier
squats" card next to "get medical advice today" undoes the advice.

**Precedence** (:func:`precedence`): red flag > sharp pain today >
follow-exactly > normal. Each is a stronger statement about today than the
next: a red flag is about the athlete, sharp pain about one exercise, and
follow-exactly about how numbers are chosen once training goes ahead.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Final

from app.domain.activity import PainKind
from app.domain.athlete import RedFlagKind, RedFlagSeverity
from app.domain.strength import Exercise, Restriction

#: The card kind sharp pain queues.
SWAP_OFFER: Final = "swap_offer"
#: The card kind a red flag queues; the only card shown while the flag is up.
RED_FLAG_FOLLOW_UP: Final = "red_flag_follow_up"

#: The sentence every pain card ends with.
PHYSIO_LINE: Final = "If pain continues, ask a physio or doctor."


class SafetyAction(StrEnum):
    """What today's training does, strongest first."""

    STOP_SESSION = "stop_session"
    STOP_EXERCISE = "stop_exercise"
    FOLLOW_PLAN = "follow_plan"
    NORMAL = "normal"


def precedence(
    *, red_flag: bool, sharp_pain_today: bool, follow_exactly: bool
) -> SafetyAction:
    """The one rule that applies to an exercise today (see the module docstring)."""
    if red_flag:
        return SafetyAction.STOP_SESSION
    if sharp_pain_today:
        return SafetyAction.STOP_EXERCISE
    if follow_exactly:
        return SafetyAction.FOLLOW_PLAN
    return SafetyAction.NORMAL


def stops_exercise(kind: PainKind | None) -> bool:
    """Whether a pain note of this kind ends the exercise for today."""
    return kind is PainKind.SHARP


def sets_left_today(prescribed: int, logged_pain: Sequence[PainKind | None]) -> int:
    """Working sets still to do: none once any logged set had sharp pain.

    ``logged_pain`` holds one entry per working set logged today on the line.
    Undoing the pain note (its kind back to ``None``) gives the sets back.
    """
    if any(stops_exercise(kind) for kind in logged_pain):
        return 0
    return max(prescribed - len(logged_pain), 0)


def blocks_increase(latest_session_pain: Iterable[PainKind | None]) -> bool:
    """Whether an exercise's weight may not go up next time.

    Read over the working sets of the latest session that logged the
    exercise: sharp pain on any of them blocks an increase, and the next
    session of it without sharp pain lifts the block. No timer — the
    athlete's next pain-free session is the evidence, not a date.
    """
    return any(stops_exercise(kind) for kind in latest_session_pain)


# --- red flags -------------------------------------------------------------------


class RedFlagSymptom(StrEnum):
    """What *Stop — something's wrong* offers, in its two groups."""

    # Call emergency services now.
    CHEST_PAIN = "chest_pain"
    CANNOT_BREATHE = "cannot_breathe"
    FAINTED = "fainted"
    # Stop training and get medical advice today.
    NUMBNESS = "numbness"
    DIZZINESS = "dizziness"
    SUDDEN_SWELLING = "sudden_swelling"


RED_FLAG_GROUP: Final[Mapping[RedFlagSymptom, RedFlagKind]] = {
    RedFlagSymptom.CHEST_PAIN: RedFlagKind.EMERGENCY,
    RedFlagSymptom.CANNOT_BREATHE: RedFlagKind.EMERGENCY,
    RedFlagSymptom.FAINTED: RedFlagKind.EMERGENCY,
    RedFlagSymptom.NUMBNESS: RedFlagKind.ADVICE,
    RedFlagSymptom.DIZZINESS: RedFlagKind.ADVICE,
    RedFlagSymptom.SUDDEN_SWELLING: RedFlagKind.ADVICE,
}

#: What the screen says for each group, word for word.
RED_FLAG_TEXT: Final[Mapping[RedFlagKind, str]] = {
    RedFlagKind.EMERGENCY: "Stop. Call emergency services now.",
    RedFlagKind.ADVICE: "Stop training and get medical advice today.",
}

#: The grade a gym red flag is stored with: an emergency is severe, anything
#: that sends the athlete for advice is moderate until a person says otherwise.
RED_FLAG_SEVERITY: Final[Mapping[RedFlagKind, RedFlagSeverity]] = {
    RedFlagKind.EMERGENCY: RedFlagSeverity.SEVERE,
    RedFlagKind.ADVICE: RedFlagSeverity.MODERATE,
}

#: How each symptom reads in the note and on the follow-up card.
SYMPTOM_TEXT: Final[Mapping[RedFlagSymptom, str]] = {
    RedFlagSymptom.CHEST_PAIN: "chest pain or pressure",
    RedFlagSymptom.CANNOT_BREATHE: "trouble breathing",
    RedFlagSymptom.FAINTED: "fainting or nearly fainting",
    RedFlagSymptom.NUMBNESS: "numbness or tingling",
    RedFlagSymptom.DIZZINESS: "dizziness",
    RedFlagSymptom.SUDDEN_SWELLING: "sudden swelling",
}


def card_allowed(kind: str, *, red_flag_active: bool) -> bool:
    """Whether a card of ``kind`` may be shown under the red flag's state.

    While the flag is up only its follow-up shows; once it is down the
    follow-up is spent and everything else shows again.
    """
    if red_flag_active:
        return kind == RED_FLAG_FOLLOW_UP
    return kind != RED_FLAG_FOLLOW_UP


def follow_up_card(symptom: RedFlagSymptom) -> tuple[str, str]:
    """The red-flag follow-up card's headline and "because" sentence."""
    kind = RED_FLAG_GROUP[symptom]
    return (
        f"You stopped training with {SYMPTOM_TEXT[symptom]}. How are you now?",
        (
            f"{RED_FLAG_TEXT[kind]} Suggestions stay paused until you say you "
            "are well again — accept this card when you are."
        ),
    )


# --- the swap offer --------------------------------------------------------------

#: Which joint a restriction tag puts under load, in the order a joint is
#: named when a movement carries several.
JOINT_RESTRICTIONS: Final[tuple[tuple[str, frozenset[Restriction]], ...]] = (
    ("knee", frozenset({Restriction.DEEP_KNEE_FLEXION})),
    (
        "hip",
        frozenset({Restriction.DEEP_HIP_FLEXION, Restriction.HIP_ROTATION_LOADED}),
    ),
    ("back", frozenset({Restriction.LOADED_SPINAL_FLEXION})),
    ("shoulder", frozenset({Restriction.OVERHEAD})),
)


def joint_of(exercise: Exercise) -> tuple[str, frozenset[Restriction]] | None:
    """The joint a movement loads most plainly, with the tags that say so."""
    for joint, tags in JOINT_RESTRICTIONS:
        if exercise.restrictions & tags:
            return joint, tags
    return None


def swap_for(exercise: Exercise, catalogue: Iterable[Exercise]) -> Exercise | None:
    """The movement to offer instead of ``exercise`` after sharp pain.

    Same movement pattern, none of the tags that load the painful joint, then
    the most shared muscle weight; ties by slug, so the offer is stable.
    ``None`` when nothing in the pattern spares the joint.
    """
    joint = joint_of(exercise)
    avoid = joint[1] if joint is not None else frozenset()
    candidates = [
        other
        for other in catalogue
        if other.id != exercise.id
        and other.pattern is exercise.pattern
        and not (other.restrictions & avoid)
    ]
    if not candidates:
        return None

    def overlap(other: Exercise) -> float:
        return sum(
            min(weight, other.muscles.get(region, 0.0))
            for region, weight in exercise.muscles.items()
        )

    return min(candidates, key=lambda other: (-overlap(other), other.id))


def plural(phrase: str) -> str:
    """``"Step-Up"`` → ``"step-ups"``: a movement name as the card says it."""
    lowered = phrase.lower()
    if re.search(r"(s|x|sh|ch)$", lowered):
        return lowered + "es"
    if re.search(r"[^aeiou]y$", lowered):
        return lowered[:-1] + "ies"
    return lowered + "s"


def movement_word(name: str) -> str:
    """The family a movement belongs to, plural: ``"Forward Lunge"`` → ``"lunges"``.

    The last word of the name — short enough for a glance, and the word the
    athlete uses ("my knee hurts on lunges").
    """
    return plural(name.split()[-1]) if name.split() else plural(name)


def pain_card(exercise: Exercise, swap: Exercise | None) -> tuple[str, str]:
    """The swap-offer card's headline and "because" sentence after sharp pain.

    The headline names the joint and offers the swap; the sentence says what
    happened to today and next time, and always ends with :data:`PHYSIO_LINE`.
    """
    joint = joint_of(exercise)
    return pain_card_text(
        exercise.name,
        joint=None if joint is None else joint[0],
        swap_name=None if swap is None else swap.name,
    )


def pain_card_text(
    name: str, *, joint: str | None = None, swap_name: str | None = None
) -> tuple[str, str]:
    """:func:`pain_card` from names alone — also for a free-text exercise."""
    where = f"{joint.capitalize()} pain" if joint is not None else "Pain"
    family = movement_word(name)
    offer = (
        f"try {plural(swap_name)} next time?"
        if swap_name is not None
        else "pick a different exercise next time?"
    )
    because = (
        f"You reported sharp pain on {family}, so they stopped for today and "
        f"their weight will not go up next time. {PHYSIO_LINE}"
    )
    return f"{where} on {family}: {offer}", because

"""Who the athlete is in the gym, and how much arc asks and shows them.

These are the athlete's **training settings**: one record, read by every gym
surface, that decides what a set row asks (*Easy / Good / Hard* or reps left),
whether arc suggests anything at all, and how a number is written.

**The mode comes from a question about the athlete, not about arc.** Nobody is
asked to choose between *Simple*, *Standard* and *Full control* on day one:
those are names for how arc behaves, and a newcomer cannot know which they
want. They are asked "Have you lifted before?" instead, and
:func:`mode_for_answer` turns the answer into a mode — *never*, or skipping
the question, gives Simple; *a little* or *regularly* gives Standard. Full
control is never inferred from anything; it is only ever chosen in Settings.
That displaces asking for a mode by name. Pinned by
`tests/unit/test_domain_training_mode.py`.

**The time zone is not a training setting this milestone.** The athlete's zone
stays `MATCHING__TIMEZONE`, read by `app.core.clock`: that clock is synchronous
and shared by the API and the MCP process, and a per-athlete override stored
here would give the two processes two answers to "what day is it" whenever
one had read the row and the other had not. Sets carry their device offset,
so a later per-athlete zone loses nothing by waiting. That displaces an
athlete zone override of the shared clock.

Pure: no I/O. The service loads the stored record (or these defaults when
there is none), applies a change with :func:`with_changes`, and stores the
result.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from enum import StrEnum
from typing import Any


class TrainingMode(StrEnum):
    """How much arc asks and decides during a session."""

    #: Easy / Good / Hard after a set; arc picks the next weight.
    SIMPLE = "simple"
    #: Reps left in reserve after a set; arc suggests, the athlete adjusts.
    STANDARD = "standard"
    #: Every control shown. Only ever chosen, never inferred.
    FULL = "full"


class LiftedBefore(StrEnum):
    """The answer to the first-run question "Have you lifted before?"."""

    NEVER = "never"
    A_LITTLE = "a_little"
    REGULARLY = "regularly"


class AskFrequency(StrEnum):
    """How often the gym screen asks how hard a set felt.

    ``last_set`` asks once per exercise, after its final working set — the
    rating progression reads, without a question on every set.
    """

    ALWAYS = "always"
    LAST_SET = "last_set"
    NEVER = "never"


class Units(StrEnum):
    """The unit a load is shown and entered in. Stored loads stay kilograms."""

    KG = "kg"
    LB = "lb"


class Locale(StrEnum):
    """The language and number format of the interface."""

    EN = "en"
    DE = "de"


def mode_for_answer(answer: LiftedBefore | None) -> TrainingMode:
    """The mode a first-run answer sets; ``None`` is a skipped question.

    "Never" and skipping both give Simple: an athlete who would not say is
    safest treated as new, and Simple is the mode that asks the least. Any
    lifting at all gives Standard. Full control is deliberately absent — it
    is a choice, never a guess (see the module docstring).
    """
    if answer is None or answer is LiftedBefore.NEVER:
        return TrainingMode.SIMPLE
    return TrainingMode.STANDARD


@dataclass(frozen=True, slots=True)
class TrainingSettings:
    """The athlete's training settings. The defaults are a fresh install's."""

    mode: TrainingMode = TrainingMode.SIMPLE
    #: Log what was lifted and nothing more: no suggestions, no progression.
    #: For an athlete following someone else's plan, a physio's say.
    plain_logbook: bool = False
    ask_frequency: AskFrequency = AskFrequency.ALWAYS
    units: Units = Units.KG
    locale: Locale = Locale.EN
    high_contrast: bool = False
    #: The once-only safety screen has been dismissed.
    before_you_start_seen: bool = False
    #: The first-run question has been answered or skipped. Separate from
    #: :attr:`lifted_before` because skipping is an answer to "ask me again?"
    #: but not to "have you lifted before?".
    first_run_seen: bool = False
    lifted_before: LiftedBefore | None = None


#: Every field a change may name.
FIELDS: frozenset[str] = frozenset(field.name for field in fields(TrainingSettings))


def with_changes(
    current: TrainingSettings, changes: Mapping[str, Any]
) -> TrainingSettings:
    """``current`` with ``changes`` applied, deriving what an answer implies.

    An answer to the first-run question marks the question seen, and — when
    it is a *new* answer — sets the mode through :func:`mode_for_answer`,
    with two exceptions: a mode named in the same change wins (it is the
    athlete saying so), and Full control is never taken away by an answer,
    because it was only ever reached by choosing it. Repeating the answer
    already stored changes nothing, so re-saving a Settings form cannot undo
    a mode chosen since.

    Raises:
        ValueError: When a field is unknown, or is given ``None`` — no
            training setting can be cleared, and the message names the field.
    """
    unknown = set(changes) - FIELDS
    if unknown:
        raise ValueError(f"Unknown training settings: {', '.join(sorted(unknown))}")
    nulled = sorted(name for name, value in changes.items() if value is None)
    if nulled:
        raise ValueError(
            f"Training settings cannot be cleared: {', '.join(nulled)} needs a value"
        )

    derived: dict[str, Any] = {}
    answer = changes.get("lifted_before")
    if answer is not None:
        derived["first_run_seen"] = True
        if (
            answer != current.lifted_before
            and "mode" not in changes
            and current.mode is not TrainingMode.FULL
        ):
            derived["mode"] = mode_for_answer(LiftedBefore(answer))
    return replace(current, **{**derived, **changes})

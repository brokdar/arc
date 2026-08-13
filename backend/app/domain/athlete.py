"""The athlete: profile and per-discipline capability stubs.

Single-athlete application — there is exactly one of these and no user table
— a single-user application. Every field is optional: the profile is
bootstrapped empty on first access and filled in from the UI, so nothing here
may assume a value is present.

Capabilities are deliberately unmodelled for the MVP. Disciplines differ in
what "capability" even means (cycling: power anchors; strength: 1RMs per
exercise), and the build plan defers that model, so the MVP carries a
free-form per-discipline mapping the later work packages can narrow.
"""

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from app.domain.plan import PlanState


class Sex(StrEnum):
    """Biological sex, as it affects physiological reference values.

    ``UNSPECIFIED`` is a real member rather than ``None`` so the column is
    never null and reads state "not answered" instead of "unknown why".
    """

    FEMALE = "female"
    MALE = "male"
    UNSPECIFIED = "unspecified"


class Discipline(StrEnum):
    """The two disciplines the MVP trains."""

    CYCLING = "cycling"
    STRENGTH = "strength"


class RedFlagSeverity(StrEnum):
    """How bad the illness or injury is (WP-8.4).

    Three grades and no numbers: the athlete is answering this on a phone
    while feeling unwell, and a scale finer than "mild / moderate / severe"
    would be answered inconsistently and read as if it were not.

    The MVP's safety rule is deterministic and reads the *boolean* only — a
    flag of any grade refuses every proposal that adds or intensifies work
    (`app.domain.proposals.intensifies`). The grade is carried so the coaching
    agent can say something useful about it, and so a later autonomy tier has
    something to graduate on; it is never the difference between allowed and
    refused.
    """

    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"


#: Longest an illness/injury note may be.
MAX_RED_FLAG_NOTE_CHARS = 1_000

#: Height bounds. Not medicine — a typo guard, so "17" or "1750" is rejected
#: at the boundary instead of silently distorting every derived value later.
MIN_HEIGHT_CM = 100.0
MAX_HEIGHT_CM = 250.0

#: The earliest date of birth accepted; anything older is a data-entry error.
EARLIEST_BIRTH_YEAR = 1900


@dataclass(frozen=True, slots=True)
class AthleteProfile:
    """The athlete's profile and discipline capability stubs.

    Args:
        name: Display name, or ``None`` before the profile is filled in.
        date_of_birth: Used for age-based reference values.
        sex: See :class:`Sex`.
        height_cm: Standing height in centimetres.
        capabilities: Free-form per-discipline capability stub, keyed by
            :class:`Discipline` value. Opaque to the MVP: stored, returned,
            never interpreted.
        plan_state: Whether the training plan is being enforced
            (`app.domain.plan.PlanState`). A property of the plan rather than
            of the person, but there is one athlete and one plan, so it lives
            on the profile instead of on a table with a single row of its own.
        red_flag_active: Whether the athlete is currently ill or injured
            (WP-8.4). While set, the coaching agent may not propose a plan
            change that adds or intensifies work.
        red_flag_note: Free text about the flag — what hurts, what a doctor
            said. Optional, and never parsed: the safety rule is deterministic
            and does not read prose.
        red_flag_severity: How bad it is. **Required while the flag is
            active** and absent otherwise (see :meth:`__post_init__`).
    """

    name: str | None = None
    date_of_birth: dt.date | None = None
    sex: Sex = Sex.UNSPECIFIED
    height_cm: float | None = None
    capabilities: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    plan_state: PlanState = PlanState.ACTIVE
    red_flag_active: bool = False
    red_flag_note: str | None = None
    red_flag_severity: RedFlagSeverity | None = None

    def __post_init__(self) -> None:
        """Reject values that are typos rather than measurements."""
        if self.name is not None and not self.name.strip():
            raise ValueError("name must not be blank; omit it instead")
        # The flag is a statement, and a statement has a grade. "Ill, severity
        # unknown" is a state the coaching agent would have to guess about and
        # the athlete would have to remember they left behind, so it is not a
        # state the profile can be in. The mirror rule — a grade with no active
        # flag — is what stops last month's `severe` reading as current; the
        # service clears both when the flag is lowered, so an athlete who only
        # says `red_flag_active: false` is not asked to tidy up after it.
        if self.red_flag_active and self.red_flag_severity is None:
            raise ValueError(
                "red_flag_severity is required while red_flag_active is set"
            )
        if not self.red_flag_active and (
            self.red_flag_severity is not None or self.red_flag_note is not None
        ):
            raise ValueError(
                "red_flag_note and red_flag_severity may only be set while "
                "red_flag_active is set"
            )
        if self.red_flag_note is not None and not self.red_flag_note.strip():
            raise ValueError("red_flag_note must not be blank; omit it instead")
        if (
            self.red_flag_note is not None
            and len(self.red_flag_note) > MAX_RED_FLAG_NOTE_CHARS
        ):
            raise ValueError(
                f"red_flag_note must be at most {MAX_RED_FLAG_NOTE_CHARS} characters"
            )
        if self.height_cm is not None and not (
            MIN_HEIGHT_CM <= self.height_cm <= MAX_HEIGHT_CM
        ):
            raise ValueError(
                f"height_cm must be between {MIN_HEIGHT_CM} and "
                f"{MAX_HEIGHT_CM}, got {self.height_cm}"
            )
        if (
            self.date_of_birth is not None
            and self.date_of_birth.year < EARLIEST_BIRTH_YEAR
        ):
            raise ValueError(
                f"date_of_birth must be in or after {EARLIEST_BIRTH_YEAR}, got "
                f"{self.date_of_birth.isoformat()}"
            )

    def age_on(self, day: dt.date) -> int | None:
        """Age in whole years on ``day``, or ``None`` without a birth date.

        Raises:
            ValueError: When ``day`` precedes the date of birth.
        """
        if self.date_of_birth is None:
            return None
        if day < self.date_of_birth:
            raise ValueError(
                f"{day.isoformat()} precedes the date of birth "
                f"{self.date_of_birth.isoformat()}"
            )
        birth = self.date_of_birth
        had_birthday = (day.month, day.day) >= (birth.month, birth.day)
        return day.year - birth.year - (0 if had_birthday else 1)

    def capability(self, discipline: Discipline) -> Mapping[str, Any]:
        """Return the capability stub for one discipline (empty when unset)."""
        value = self.capabilities.get(discipline.value, {})
        return value if isinstance(value, Mapping) else {}

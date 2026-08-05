"""The athlete: profile and per-discipline capability stubs.

Single-athlete application — there is exactly one of these and no user table
(see `docs/decisions.md` D6). Every field is optional: the profile is
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
    """

    name: str | None = None
    date_of_birth: dt.date | None = None
    sex: Sex = Sex.UNSPECIFIED
    height_cm: float | None = None
    capabilities: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        """Reject values that are typos rather than measurements."""
        if self.name is not None and not self.name.strip():
            raise ValueError("name must not be blank; omit it instead")
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

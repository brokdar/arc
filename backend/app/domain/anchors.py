"""Physiological anchors and their append-only history.

Invariant 3 of the build plan: anchor history is never edited, only appended.
An anchor value is not a fact about the athlete, it is a *measurement* — so
every version carries where it came from (:class:`Provenance`), how
(``protocol``), from when it applies (``effective_date``), and how sure we are
(``ci_low``/``ci_high``). Derived values record the anchor version they used,
which is only meaningful because versions are immutable.

Nothing here is a repository: these are pure functions over a list of versions
the caller has already loaded. The "current" anchor is a *derived* answer, not
a stored flag, so it is computed the same way everywhere.
"""

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum


class AnchorType(StrEnum):
    """Anchor quantities the application knows about.

    ``CP`` and ``W_PRIME`` are reserved by the build plan: the critical-power
    model is out of MVP scope, but the enum member exists so adding it later
    is not a migration of stored values. Nothing produces or consumes them
    yet, and :func:`app.domain.zones.zones_for` rejects them.
    """

    FTP = "ftp"
    LTHR = "lthr"
    MAX_HR = "max_hr"
    #: Resting heart rate. An anchor and not a profile field: HRSS
    #: reads it as the floor of the heart-rate reserve, so it needs the same
    #: provenance, effective date and append-only history every other input to
    #: a derived number has. No zone model derives from it.
    RESTING_HR = "resting_hr"
    #: Reserved (WP-5+): critical power.
    CP = "cp"
    #: Reserved (WP-5+): the W′ work capacity above CP.
    W_PRIME = "w_prime"


class AnchorUnit(StrEnum):
    """Units an anchor value can be expressed in."""

    WATT = "W"
    BPM = "bpm"
    JOULE = "J"


class Provenance(StrEnum):
    """Where an anchor value came from — ordered weakest to strongest.

    The order matters: it is what later work packages compare when deciding
    whether a new value should displace the one in use.
    """

    ASSUMED = "assumed"
    ESTIMATED = "estimated"
    ATHLETE_REPORTED = "athlete_reported"
    TESTED = "tested"


class AnchorSource(StrEnum):
    """Who appended the version. Distinct from :class:`Provenance`.

    Provenance is about the *value*; source is about the *writer*, and the
    agent may never disguise itself as the athlete (build plan §0.7).
    """

    ATHLETE = "athlete"
    AGENT = "agent"


class StalenessState(StrEnum):
    """How much an anchor version is still to be trusted.

    The staleness *model* is deferred past the MVP, but the field is not:
    every version stores one, hardcoded to ``FRESH`` today (see
    :data:`MVP_STALENESS_STATE`). ``AGING`` and ``STALE`` are reserved so that
    turning the model on later is code, not a data migration.
    """

    FRESH = "fresh"
    #: Reserved: past its expected validity but still the best available.
    AGING = "aging"
    #: Reserved: old enough that derived values should be flagged.
    STALE = "stale"


#: The only staleness state the MVP ever writes.
MVP_STALENESS_STATE = StalenessState.FRESH

#: Anchor types that exist as vocabulary but may not be written yet. The
#: critical-power model arrives in WP-5 with its own protocols and semantics;
#: accepting CP/W′ values before anything can derive from or validate them
#: would seed the history the model later builds on with unvetted rows.
RESERVED_ANCHOR_TYPES: frozenset[AnchorType] = frozenset(
    {AnchorType.CP, AnchorType.W_PRIME}
)

#: The unit each anchor type is measured in. One unit per type: allowing a
#: choice would mean every consumer converting before comparing.
ANCHOR_UNITS: dict[AnchorType, AnchorUnit] = {
    AnchorType.FTP: AnchorUnit.WATT,
    AnchorType.LTHR: AnchorUnit.BPM,
    AnchorType.MAX_HR: AnchorUnit.BPM,
    AnchorType.RESTING_HR: AnchorUnit.BPM,
    AnchorType.CP: AnchorUnit.WATT,
    AnchorType.W_PRIME: AnchorUnit.JOULE,
}

#: Longest a ``protocol`` may be. A **domain** rule, not just a column width:
#: the MCP tool does not pass through the API schema, so a bound that lived
#: only there let a dry run validate what the write then failed at the INSERT
#: (issue #17). The API schema and the persistence column both reference this
#: constant, so the three layers cannot drift.
MAX_PROTOCOL_CHARS = 200

#: Plausibility bounds per anchor type. A typo guard at the boundary, not a
#: judgement about the athlete: an FTP of 25000 W would otherwise poison every
#: zone, target and score derived from it.
ANCHOR_BOUNDS: dict[AnchorType, tuple[float, float]] = {
    AnchorType.FTP: (30.0, 700.0),
    AnchorType.LTHR: (60.0, 220.0),
    AnchorType.MAX_HR: (80.0, 240.0),
    #: A trained endurance athlete's resting HR reaches the low 30s and a
    #: sedentary one the low 100s; outside 25-120 bpm the number is a typo or
    #: a reading taken during something other than rest, and HRSS divides by
    #: the reserve it defines.
    AnchorType.RESTING_HR: (25.0, 120.0),
    AnchorType.CP: (30.0, 700.0),
    AnchorType.W_PRIME: (1_000.0, 60_000.0),
}


@dataclass(frozen=True, slots=True)
class AnchorVersion:
    """One immutable entry in an anchor's append-only history.

    Args:
        anchor_type: Which quantity this measures.
        value: The measurement, in the anchor type's unit (:data:`ANCHOR_UNITS`).
        unit: Must be that unit — carried explicitly so stored rows are
            self-describing rather than depending on this table.
        provenance: How the value was arrived at.
        protocol: How it was measured (``"20min x0.95"``, ``"ramp test"``).
            Required for :attr:`Provenance.TESTED`: a tested value whose
            protocol is unknown cannot be compared with the next test.
        effective_date: The date the value describes the athlete from. Not the
            creation time: a test can be entered days late, and a correction
            can be back-dated.
        ci_low: Lower bound of the confidence interval, if known.
        ci_high: Upper bound of the confidence interval, if known.
        created_at: When the row was appended (aware UTC).
        source: Who appended it.
        staleness_state: Always :data:`MVP_STALENESS_STATE` in the MVP.
    """

    anchor_type: AnchorType
    value: float
    unit: AnchorUnit
    provenance: Provenance
    effective_date: dt.date
    created_at: dt.datetime
    source: AnchorSource
    protocol: str | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    staleness_state: StalenessState = MVP_STALENESS_STATE

    def __post_init__(self) -> None:
        """Enforce the rules that make a version comparable with the next."""
        low, high = ANCHOR_BOUNDS[self.anchor_type]
        if not low <= self.value <= high:
            raise ValueError(
                f"{self.anchor_type.value} value must be between {low} and "
                f"{high} {self.unit.value}, got {self.value}"
            )
        expected_unit = ANCHOR_UNITS[self.anchor_type]
        if self.unit is not expected_unit:
            raise ValueError(
                f"{self.anchor_type.value} is measured in "
                f"{expected_unit.value}, not {self.unit.value}"
            )
        if self.protocol is not None and len(self.protocol) > MAX_PROTOCOL_CHARS:
            raise ValueError(
                f"protocol must be at most {MAX_PROTOCOL_CHARS} characters, "
                f"got {len(self.protocol)}"
            )
        if self.provenance is Provenance.TESTED and not (self.protocol or "").strip():
            raise ValueError(
                "a tested anchor must state its protocol; without it the "
                "result cannot be compared with the next test"
            )
        if self.ci_low is not None and self.ci_low > self.value:
            raise ValueError(f"ci_low {self.ci_low} is above the value {self.value}")
        if self.ci_high is not None and self.ci_high < self.value:
            raise ValueError(f"ci_high {self.ci_high} is below the value {self.value}")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware UTC")


def describe_anchor(version: AnchorVersion) -> str:
    """Render an anchor version the way an explanation names its inputs.

    The version's own value, provenance and effective date — never "the
    current FTP": a number computed against a frozen version must keep
    explaining itself after the athlete's FTP moves. Lives here rather than
    beside any one consumer because the planned load (`app.domain.prediction`)
    and every recorded metric (`app.domain.metrics`) have to name an anchor
    the same way, or the same version reads as two different inputs on one
    screen.
    """
    provenance = version.provenance.value.replace("_", " ")
    return (
        f"{version.value:g} {version.unit.value} "
        f"({provenance}, effective {version.effective_date.isoformat()})"
    )


def _ordering_key(version: AnchorVersion) -> tuple[dt.date, dt.datetime]:
    """Sort key making the history total and stable.

    ``effective_date`` first, because that is the axis the athlete reasons on;
    ``created_at`` breaks ties, so appending a correction with the same
    effective date wins over the value it corrects.

    The tie-break is only as truthful as the stamps: "appended later" must
    mean "greater ``created_at``", which a wall clock alone does not promise.
    `AnchorService.append` guarantees it by clamping each new stamp strictly
    above the newest one already in that type's history — this pure layer
    just trusts the ordering it is handed.
    """
    return (version.effective_date, version.created_at)


def sorted_history(versions: Iterable[AnchorVersion]) -> list[AnchorVersion]:
    """Return the versions oldest-first in the canonical order."""
    return sorted(versions, key=_ordering_key)


def anchor_as_of(
    versions: Iterable[AnchorVersion], moment: dt.datetime, day: dt.date
) -> AnchorVersion | None:
    """Return the version in force at ``moment``, on the athlete's ``day``.

    "In force" means: effective on or before ``day``, and appended on or
    before ``moment``. The second half is what makes this reproducible — a
    value entered today cannot retroactively become what a score computed last
    week was looking at, however it is back-dated.

    **The two arguments are not redundant** and the day cannot be derived from
    the instant here. ``effective_date`` is an athlete-local calendar date;
    ``moment`` is an instant. This function used to read the day off the
    instant in UTC, which put an anchor effective "from the 20th" out of force
    for the first twelve hours of an Auckland athlete's 20th and in force two
    hours early for a Honolulu one (issue #62). Which calendar the athlete
    lives in is ambient state, and this layer deliberately has none — so the
    caller passes it (`app.core.clock.athlete_today`), the same way
    :func:`anchor_effective_on` already takes its day.

    **Nothing calls this yet, and that is not an oversight.** "Which version is
    in force *now*" is :func:`anchor_effective_on` — see
    `app.services.anchors.AnchorService.current` for why asking it with
    ``moment=now`` is actively wrong. This function is the rule for
    *reproducing* a read that already happened: what a stored score or verdict
    was looking at when it was computed. It is kept and tested for the caller
    that replays one, not for a current one.

    Raises:
        ValueError: When ``moment`` is naive.
    """
    if moment.tzinfo is None:
        raise ValueError("moment must be timezone-aware UTC")
    in_force = [
        version
        for version in versions
        if version.effective_date <= day and version.created_at <= moment
    ]
    if not in_force:
        return None
    return max(in_force, key=_ordering_key)


def anchor_effective_on(
    versions: Iterable[AnchorVersion], day: dt.date
) -> AnchorVersion | None:
    """Return the version that governs ``day``, as the history stands now.

    The prescriptive cousin of :func:`anchor_as_of`, with the ``created_at``
    half deliberately absent: that function answers "what was knowable at that
    instant" (which is what makes stored scores reproducible), while this one
    answers "which measurement does the *current* history assign to that day" —
    the question repricing asks after an append, when the newly created version
    must count for the past days it is effective from, precisely because its
    ``created_at`` is now.
    """
    in_force = [version for version in versions if version.effective_date <= day]
    if not in_force:
        return None
    return max(in_force, key=_ordering_key)


def history_by_type(
    versions: Iterable[AnchorVersion],
) -> dict[AnchorType, Sequence[AnchorVersion]]:
    """Group a mixed list of versions into one sorted history per type."""
    grouped: dict[AnchorType, list[AnchorVersion]] = {}
    for version in sorted_history(versions):
        grouped.setdefault(version.anchor_type, []).append(version)
    return dict(grouped)

"""Success criteria: the machine-checkable half of a session's intent.

Build plan WP-2.7 fixes the MVP set at five, and each one is a question a
recording can answer with no LLM in the loop (invariant 7):

``time_in_band``
    Of the steps this selector picks, what fraction of their time was spent
    inside the band? The band is stated as fractions of *the step's own
    prescribed target*, so one criterion covers a workout whose steps are all
    at different intensities.
``duration_floor``
    Did the session last at least this long?
``ceiling``
    Did any channel spend more than this long above this limit? The limit may
    be absolute or a percentage of an anchor, because a template written for
    every athlete can only express the second.
``sets_completed``
    Strength: what fraction of the prescribed sets were performed?
``load_within``
    Strength: were the loads used within this relative tolerance of the
    prescribed load?

**Evaluation is WP-7.** What lives here is the vocabulary, the invariants, and
the tagged-union JSON form the criteria are stored and transported in — that
form is a data contract from the moment the first planned session is written,
so it is fixed now rather than discovered later.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.domain.anchors import AnchorType
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
from app.domain.workout import (
    CHANNEL_ANCHORS,
    CHANNEL_BOUNDS,
    CHANNEL_UNITS,
    MAX_TARGET_FRACTION,
    Channel,
    ChannelUnit,
    FlatStep,
    StepRole,
)


class CriterionKind(StrEnum):
    """The five MVP criterion types, as they appear in the ``kind`` tag."""

    TIME_IN_BAND = "time_in_band"
    DURATION_FLOOR = "duration_floor"
    CEILING = "ceiling"
    SETS_COMPLETED = "sets_completed"
    LOAD_WITHIN = "load_within"


class StepSelectorKind(StrEnum):
    """How a criterion picks the steps it applies to."""

    #: Every step of the flattened workout.
    ALL = "all"
    #: Every step with one role — ``work``, typically.
    ROLE = "role"
    #: One step, by its position in the flattened sequence.
    INDEX = "index"


@dataclass(frozen=True, slots=True)
class StepSelector:
    """Which flattened steps a criterion applies to.

    Deliberately small. A richer selector language (by name, by target range,
    by repeat block) is exactly the kind of thing that gets built before
    anything needs it; these three cover every template the MVP ships, and a
    fourth kind is an enum member plus a branch in :meth:`select`.
    """

    kind: StepSelectorKind
    role: StepRole | None = None
    index: int | None = None

    def __post_init__(self) -> None:
        """Reject selectors carrying the wrong argument for their kind."""
        if self.kind is StepSelectorKind.ROLE:
            if self.role is None:
                raise ValueError("a role selector needs a role")
            if self.index is not None:
                raise ValueError("a role selector carries no index")
        elif self.kind is StepSelectorKind.INDEX:
            if self.index is None:
                raise ValueError("an index selector needs an index")
            if self.index < 0:
                raise ValueError(f"index must not be negative, got {self.index}")
            if self.role is not None:
                raise ValueError("an index selector carries no role")
        elif self.role is not None or self.index is not None:
            raise ValueError("an 'all' selector carries no role and no index")

    @classmethod
    def all_steps(cls) -> StepSelector:
        """Select every step."""
        return cls(StepSelectorKind.ALL)

    @classmethod
    def of_role(cls, role: StepRole) -> StepSelector:
        """Select every step with one role."""
        return cls(StepSelectorKind.ROLE, role=role)

    @classmethod
    def at_index(cls, index: int) -> StepSelector:
        """Select the step at one position of the flattened sequence."""
        return cls(StepSelectorKind.INDEX, index=index)

    def select(self, steps: Sequence[FlatStep]) -> list[FlatStep]:
        """Return the steps this selector picks out of a flattened workout.

        An index past the end selects nothing rather than raising: a criterion
        can outlive an edit that shortened the workout, and WP-7 reports that
        as an unevaluable criterion, not as a crash.
        """
        if self.kind is StepSelectorKind.ALL:
            return list(steps)
        if self.kind is StepSelectorKind.ROLE:
            return [step for step in steps if step.role is self.role]
        return [step for step in steps if step.index == self.index]


@dataclass(frozen=True, slots=True)
class Band:
    """An acceptable range around a step's prescribed target.

    Bounds are **fractions of the target the step itself prescribes** for
    ``channel``: ``low=0.95, high=1.05`` is "within ±5 % of what was asked
    for". Relative rather than absolute so one criterion is meaningful across
    a workout whose steps sit at different intensities — and so a purpose
    template, which knows nothing about a particular session, can state one at
    all.

    Args:
        channel: Which channel is being judged.
        low: Lower bound as a fraction of the prescribed target.
        high: Upper bound as a fraction.
    """

    channel: Channel
    low: float
    high: float

    def __post_init__(self) -> None:
        """Reject bands that are inverted, negative or absurdly wide."""
        if self.low <= 0:
            raise ValueError(f"band low must be above 0, got {self.low}")
        if self.high < self.low:
            raise ValueError(f"band high {self.high} is below low {self.low}")
        if self.high > MAX_TARGET_FRACTION:
            raise ValueError(
                f"band high must be at most {MAX_TARGET_FRACTION}, got {self.high}"
            )


@dataclass(frozen=True, slots=True)
class PercentLimit:
    """A ceiling expressed as a fraction of an anchor (``0.75`` = 75 % FTP)."""

    anchor_type: AnchorType
    pct: float

    def __post_init__(self) -> None:
        """Reject percentages outside the range a target may take."""
        if not 0 < self.pct <= MAX_TARGET_FRACTION:
            raise ValueError(
                f"pct must be between 0 and {MAX_TARGET_FRACTION}, got {self.pct}"
            )


@dataclass(frozen=True, slots=True)
class AbsoluteLimit:
    """A ceiling expressed in the channel's own unit."""

    value: float
    unit: ChannelUnit

    def __post_init__(self) -> None:
        """Reject negative limits."""
        if self.value < 0:
            raise ValueError(f"limit value must not be negative, got {self.value}")


#: The bound of a :class:`Ceiling`.
type Limit = PercentLimit | AbsoluteLimit


@dataclass(frozen=True, slots=True)
class TimeInBand:
    """At least ``min_fraction`` of the selected steps' time inside ``band``."""

    selector: StepSelector
    band: Band
    min_fraction: float

    def __post_init__(self) -> None:
        """Reject fractions outside [0, 1]."""
        _check_fraction(self.min_fraction, "min_fraction")


@dataclass(frozen=True, slots=True)
class DurationFloor:
    """The session must last at least ``min_seconds``."""

    min_seconds: int

    def __post_init__(self) -> None:
        """Reject non-positive floors."""
        if self.min_seconds <= 0:
            raise ValueError(f"min_seconds must be above 0, got {self.min_seconds}")


@dataclass(frozen=True, slots=True)
class Ceiling:
    """No more than ``max_seconds_above`` spent above ``limit`` on ``channel``.

    ``max_seconds_above`` of 0 means a hard cap — the recovery-ride rule.
    """

    channel: Channel
    limit: Limit
    max_seconds_above: int

    def __post_init__(self) -> None:
        """Reject limits the channel cannot carry.

        The channel is what makes an absolute limit checkable at all: only
        here is it known that ``value`` is watts rather than beats, so this is
        also where a cap of 1e300 W is caught. :class:`AbsoluteLimit` itself
        cannot do it — it does not know what it is limiting — and a ceiling no
        recording could ever exceed silently scores every session a pass.
        """
        if self.max_seconds_above < 0:
            raise ValueError(
                f"max_seconds_above must not be negative, got {self.max_seconds_above}"
            )
        if isinstance(self.limit, PercentLimit):
            allowed = CHANNEL_ANCHORS[self.channel]
            if self.limit.anchor_type not in allowed:
                raise ValueError(
                    f"{self.channel.value} cannot be capped as a percentage of "
                    f"{self.limit.anchor_type.value}"
                )
            return
        if self.limit.unit is not CHANNEL_UNITS[self.channel]:
            raise ValueError(
                f"{self.channel.value} is measured in "
                f"{CHANNEL_UNITS[self.channel].value}, not {self.limit.unit.value}"
            )
        low, high = CHANNEL_BOUNDS[self.channel]
        if not low <= self.limit.value <= high:
            raise ValueError(
                f"a {self.channel.value} ceiling must lie between {low} and "
                f"{high} {CHANNEL_UNITS[self.channel].value}, got {self.limit.value}"
            )


@dataclass(frozen=True, slots=True)
class SetsCompleted:
    """At least ``min_fraction`` of the prescribed strength sets performed."""

    min_fraction: float

    def __post_init__(self) -> None:
        """Reject fractions outside [0, 1]."""
        _check_fraction(self.min_fraction, "min_fraction")


@dataclass(frozen=True, slots=True)
class LoadWithin:
    """Loads used within ``pct_tolerance`` (a fraction) of what was prescribed."""

    pct_tolerance: float

    def __post_init__(self) -> None:
        """Reject tolerances outside (0, 1]."""
        if not 0 < self.pct_tolerance <= 1:
            raise ValueError(
                f"pct_tolerance must be between 0 and 1, got {self.pct_tolerance}"
            )


#: Any MVP success criterion.
type SuccessCriterion = (
    TimeInBand | DurationFloor | Ceiling | SetsCompleted | LoadWithin
)

#: Criteria that only mean something for a strength session, and the ones that
#: only mean something for an endurance session. Checked when a planned
#: session's intent is assembled: a `sets_completed` criterion on a bike ride
#: is not a strict rule anywhere else, and would silently never evaluate.
STRENGTH_ONLY_KINDS: frozenset[CriterionKind] = frozenset(
    {CriterionKind.SETS_COMPLETED, CriterionKind.LOAD_WITHIN}
)
ENDURANCE_ONLY_KINDS: frozenset[CriterionKind] = frozenset(
    {CriterionKind.TIME_IN_BAND, CriterionKind.CEILING}
)


def _check_fraction(value: float, name: str) -> None:
    """Reject a fraction outside [0, 1]."""
    if not 0 <= value <= 1:
        raise ValueError(f"{name} must be between 0 and 1, got {value}")


def kind_of(criterion: SuccessCriterion) -> CriterionKind:
    """Return the tag of a criterion."""
    match criterion:
        case TimeInBand():
            return CriterionKind.TIME_IN_BAND
        case DurationFloor():
            return CriterionKind.DURATION_FLOOR
        case Ceiling():
            return CriterionKind.CEILING
        case SetsCompleted():
            return CriterionKind.SETS_COMPLETED
        case LoadWithin():
            return CriterionKind.LOAD_WITHIN


def referenced_anchor_types(
    criteria: Sequence[SuccessCriterion],
) -> frozenset[AnchorType]:
    """Return every anchor type the criteria are expressed as a percentage of.

    Pinned alongside the workout's own anchors: a ceiling of "75 % FTP" is as
    much a part of the frozen prescription as a target is.
    """
    return frozenset(
        criterion.limit.anchor_type
        for criterion in criteria
        if isinstance(criterion, Ceiling) and isinstance(criterion.limit, PercentLimit)
    )


# --- serialization ------------------------------------------------------------


def selector_to_json(selector: StepSelector) -> dict[str, Any]:
    """Serialize a step selector."""
    document: dict[str, Any] = {"kind": selector.kind.value}
    if selector.role is not None:
        document["role"] = selector.role.value
    if selector.index is not None:
        document["index"] = selector.index
    return document


_SELECTOR_FIELDS = frozenset({"kind", "role", "index"})


def selector_from_json(document: Any, path: str) -> StepSelector:
    """Deserialize a step selector.

    Raises:
        ValueError: When the document is not a legal selector.
    """
    body = as_mapping(document, path)
    no_extra_fields(body, _SELECTOR_FIELDS, path)
    role = optional(body, "role")
    index = optional(body, "index")
    kind = as_enum(StepSelectorKind, field(body, "kind", path), f"{path}.kind")
    with located(path):
        return StepSelector(
            kind=kind,
            role=None if role is None else as_enum(StepRole, role, f"{path}.role"),
            index=None if index is None else as_int(index, f"{path}.index"),
        )


def band_to_json(band: Band) -> dict[str, Any]:
    """Serialize a band."""
    return {"channel": band.channel.value, "low": band.low, "high": band.high}


_BAND_FIELDS = frozenset({"channel", "low", "high"})


def band_from_json(document: Any, path: str) -> Band:
    """Deserialize a band.

    Raises:
        ValueError: When the document is not a legal band.
    """
    body = as_mapping(document, path)
    no_extra_fields(body, _BAND_FIELDS, path)
    channel = as_enum(Channel, field(body, "channel", path), f"{path}.channel")
    low = as_float(field(body, "low", path), f"{path}.low")
    high = as_float(field(body, "high", path), f"{path}.high")
    with located(path):
        return Band(channel=channel, low=low, high=high)


def limit_to_json(limit: Limit) -> dict[str, Any]:
    """Serialize a ceiling limit."""
    if isinstance(limit, PercentLimit):
        return {
            "kind": "percent_of_anchor",
            "anchor_type": limit.anchor_type.value,
            "pct": limit.pct,
        }
    return {"kind": "absolute", "value": limit.value, "unit": limit.unit.value}


_PERCENT_LIMIT_FIELDS = frozenset({"kind", "anchor_type", "pct"})
_ABSOLUTE_LIMIT_FIELDS = frozenset({"kind", "value", "unit"})


def limit_from_json(document: Any, path: str) -> Limit:
    """Deserialize a ceiling limit.

    Raises:
        ValueError: When the document is not a legal limit.
    """
    body = as_mapping(document, path)
    kind = as_str(field(body, "kind", path), f"{path}.kind")
    if kind == "percent_of_anchor":
        no_extra_fields(body, _PERCENT_LIMIT_FIELDS, path)
        anchor_type = as_enum(
            AnchorType, field(body, "anchor_type", path), f"{path}.anchor_type"
        )
        pct = as_float(field(body, "pct", path), f"{path}.pct")
        with located(path):
            return PercentLimit(anchor_type=anchor_type, pct=pct)
    if kind == "absolute":
        no_extra_fields(body, _ABSOLUTE_LIMIT_FIELDS, path)
        value = as_float(field(body, "value", path), f"{path}.value")
        unit = as_enum(ChannelUnit, field(body, "unit", path), f"{path}.unit")
        with located(path):
            return AbsoluteLimit(value=value, unit=unit)
    raise ValueError(
        f"{path}.kind: {kind!r} is not one of: percent_of_anchor, absolute"
    )


def criterion_to_json(criterion: SuccessCriterion) -> dict[str, Any]:
    """Serialize one success criterion as a tagged union member."""
    kind = kind_of(criterion)
    match criterion:
        case TimeInBand():
            return {
                "kind": kind.value,
                "selector": selector_to_json(criterion.selector),
                "band": band_to_json(criterion.band),
                "min_fraction": criterion.min_fraction,
            }
        case DurationFloor():
            return {"kind": kind.value, "min_seconds": criterion.min_seconds}
        case Ceiling():
            return {
                "kind": kind.value,
                "channel": criterion.channel.value,
                "limit": limit_to_json(criterion.limit),
                "max_seconds_above": criterion.max_seconds_above,
            }
        case SetsCompleted():
            return {"kind": kind.value, "min_fraction": criterion.min_fraction}
        case LoadWithin():
            return {"kind": kind.value, "pct_tolerance": criterion.pct_tolerance}


_CRITERION_FIELDS: dict[CriterionKind, frozenset[str]] = {
    CriterionKind.TIME_IN_BAND: frozenset({"kind", "selector", "band", "min_fraction"}),
    CriterionKind.DURATION_FLOOR: frozenset({"kind", "min_seconds"}),
    CriterionKind.CEILING: frozenset({"kind", "channel", "limit", "max_seconds_above"}),
    CriterionKind.SETS_COMPLETED: frozenset({"kind", "min_fraction"}),
    CriterionKind.LOAD_WITHIN: frozenset({"kind", "pct_tolerance"}),
}


def criterion_from_json(document: Any, path: str = "criterion") -> SuccessCriterion:
    """Deserialize one success criterion.

    Raises:
        ValueError: When the document is not a legal criterion.
    """
    body = as_mapping(document, path)
    kind = as_enum(CriterionKind, field(body, "kind", path), f"{path}.kind")
    no_extra_fields(body, _CRITERION_FIELDS[kind], path)
    match kind:
        case CriterionKind.TIME_IN_BAND:
            selector = selector_from_json(
                field(body, "selector", path), f"{path}.selector"
            )
            band = band_from_json(field(body, "band", path), f"{path}.band")
            min_fraction = as_float(
                field(body, "min_fraction", path), f"{path}.min_fraction"
            )
            with located(path):
                return TimeInBand(
                    selector=selector, band=band, min_fraction=min_fraction
                )
        case CriterionKind.DURATION_FLOOR:
            min_seconds = as_int(
                field(body, "min_seconds", path), f"{path}.min_seconds"
            )
            with located(path):
                return DurationFloor(min_seconds=min_seconds)
        case CriterionKind.CEILING:
            channel = as_enum(Channel, field(body, "channel", path), f"{path}.channel")
            limit = limit_from_json(field(body, "limit", path), f"{path}.limit")
            max_seconds_above = as_int(
                field(body, "max_seconds_above", path), f"{path}.max_seconds_above"
            )
            with located(path):
                return Ceiling(
                    channel=channel,
                    limit=limit,
                    max_seconds_above=max_seconds_above,
                )
        case CriterionKind.SETS_COMPLETED:
            completed = as_float(
                field(body, "min_fraction", path), f"{path}.min_fraction"
            )
            with located(path):
                return SetsCompleted(min_fraction=completed)
        case CriterionKind.LOAD_WITHIN:
            tolerance = as_float(
                field(body, "pct_tolerance", path), f"{path}.pct_tolerance"
            )
            with located(path):
                return LoadWithin(pct_tolerance=tolerance)


def criteria_to_json(
    criteria: Sequence[SuccessCriterion],
) -> list[dict[str, Any]]:
    """Serialize a list of success criteria."""
    return [criterion_to_json(criterion) for criterion in criteria]


def criteria_from_json(
    document: Any, path: str = "success_criteria"
) -> tuple[SuccessCriterion, ...]:
    """Deserialize a list of success criteria.

    Raises:
        ValueError: When any member is not a legal criterion.
    """
    entries = as_sequence(document, path)
    return tuple(
        criterion_from_json(entry, f"{path}[{index}]")
        for index, entry in enumerate(entries)
    )

"""Plans: the container planned sessions belong to, and what it says about them.

A plan carries three facts the loose rows of `planned_sessions` had no home
for: a **phase** (base, build, peak, ...), whether its sessions must be
**followed exactly** (a physio's programme), and which of its weeks are tagged
(a `recovery` week). It also covers a span of dates, and the rules about that
span are pure, so they live here.

**Follow-exactly is stored, not enforced, here.** The flag lands with this
module so the rules that must respect it have a value to read; each such rule
enforces it where it is written — progression switches off for a
follow-exactly plan with the suggested-weights rule (AC-43), and swaps refuse
with the swap rule (AC-63). Enforcing it here would mean guessing those rules'
shape before they exist, and a check with nothing to check is a branch no test
can make fail.

**Dates are half-open, `[starts_on, ends_before)`**, like every range in arc
(`.claude/rules/backend-domain-units.md`): a plan ending before the 4th and one
starting on the 4th share a boundary and no day. Either end may be absent,
meaning unbounded on that side.

**The default plan.** One plan is the default: undated, always active, the
plan of every session no dated plan covers. Migration `0028` creates it and
moves every pre-existing session into it, and it is exempt from the overlap
rule — a dated plan *carves out of* the default rather than colliding with it,
which is what lets the athlete add a peak block without first having to
shrink a plan they never made.
"""

import datetime as dt
import re
from collections.abc import Iterable
from enum import StrEnum

#: Longest a plan's name may be.
MAX_PLAN_NAME_CHARS = 120

#: The name migration `0028` and a fresh database give the default plan.
DEFAULT_PLAN_NAME = "My plan"

#: ``HH:MM`` on a 24-hour clock — the one spelling a time of day has on the wire.
_TIME_OF_DAY = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

#: The same pattern as a string, for a JSON schema to publish.
TIME_OF_DAY_PATTERN = _TIME_OF_DAY.pattern


class PlanPhase(StrEnum):
    """Where in the season a plan sits."""

    BASE = "base"
    BUILD = "build"
    PEAK = "peak"
    TAPER = "taper"
    MAINTENANCE = "maintenance"
    TRANSITION = "transition"


class PlanWeekTag(StrEnum):
    """A label on one week of one plan."""

    RECOVERY = "recovery"


def parse_time_of_day(value: str) -> dt.time:
    """Read ``HH:MM`` as a time of day.

    Raises:
        ValueError: When ``value`` is not a 24-hour ``HH:MM`` — ``25:00``,
            ``5:30`` and ``17:30:00`` are all refused, so one time has one
            spelling.
    """
    match = _TIME_OF_DAY.match(value)
    if match is None:
        raise ValueError(f"time_of_day must be HH:MM on a 24-hour clock, not {value!r}")
    return dt.time(int(match.group(1)), int(match.group(2)))


def format_time_of_day(value: dt.time) -> str:
    """Write a time of day as ``HH:MM``."""
    return f"{value.hour:02d}:{value.minute:02d}"


def check_span(starts_on: dt.date | None, ends_before: dt.date | None) -> None:
    """Refuse a span that holds no day.

    Raises:
        ValueError: When both ends are given and ``ends_before`` is not after
            ``starts_on``.
    """
    if starts_on is not None and ends_before is not None and ends_before <= starts_on:
        raise ValueError(
            f"ends_before ({ends_before.isoformat()}) must be after starts_on "
            f"({starts_on.isoformat()}): a plan's dates are [starts_on, ends_before)"
        )


def spans_overlap(
    a: tuple[dt.date | None, dt.date | None],
    b: tuple[dt.date | None, dt.date | None],
) -> bool:
    """Whether two half-open spans share a day; ``None`` is unbounded."""
    a_start, a_end = a
    b_start, b_end = b
    a_before_b_ends = b_end is None or a_start is None or a_start < b_end
    b_before_a_ends = a_end is None or b_start is None or b_start < a_end
    return a_before_b_ends and b_before_a_ends


def covers(
    starts_on: dt.date | None, ends_before: dt.date | None, day: dt.date
) -> bool:
    """Whether ``day`` falls inside ``[starts_on, ends_before)``."""
    return (starts_on is None or starts_on <= day) and (
        ends_before is None or day < ends_before
    )


def check_week_start(day: dt.date) -> None:
    """Refuse a week key that is not a Monday.

    Raises:
        ValueError: When ``day`` is not a Monday — a plan week is keyed by the
            Monday it starts on (`app.domain.plan.week_start`), and a tag
            stored under a Wednesday would belong to no week anyone asks for.
    """
    if day.weekday() != 0:
        raise ValueError(
            f"week_start must be a Monday; {day.isoformat()} is a {day:%A}"
        )


def normalise_tags(tags: Iterable[PlanWeekTag]) -> tuple[PlanWeekTag, ...]:
    """A week's tags as a set, in vocabulary order."""
    chosen = set(tags)
    return tuple(tag for tag in PlanWeekTag if tag in chosen)

"""Response schemas for the fitness series (`GET /fitness`).

**Every object keyed by a display-only metric carries ``display_only: true``**
— the day's value, the series' maturity, its explanation — so a reader holding
any fragment of the response can tell the number describes training and may
not drive a decision (`app.domain.authority`). The field is required and
constant in the contract rather than defaulted, so the generated client types
it ``true`` and never ``true | undefined``. Pinned by
`tests/unit/test_display_only_authority.py`, which walks the OpenAPI document
for every property named after a display-only metric.

The read follows the wellness trend's conventions: a value that does not exist
is ``null``, never ``0``, and each point says how many sessions it counted
(``n``) and how many it could not (``uncounted``).
"""

import datetime as dt
from typing import Literal

from pydantic import BaseModel

from app.api.schemas.metrics import ExplanationRead


class DisplayOnlyValueRead(BaseModel):
    """One day's value of a display-only metric."""

    value: float
    display_only: Literal[True]


class FitnessPointRead(BaseModel):
    """One calendar day of the series.

    Every day from the first session to today is a point, rest days included —
    the averages decay on them, and a series that skipped them would hold
    fitness flat across a month off.
    """

    local_date: dt.date
    #: The day's position counting the first session's date as day 1.
    day: int
    #: Fitness: the 42-day EWMA of cycling load.
    ctl: DisplayOnlyValueRead
    #: Fatigue: the 7-day EWMA of every discipline's load.
    atl: DisplayOnlyValueRead
    #: Form: CTL − ATL at the lag `form_lag_days` names.
    tsb: DisplayOnlyValueRead
    #: The day's total load, every discipline. `0` on a rest day; **null** on a
    #: day whose only sessions could not be priced — that is an unknown load,
    #: not a light one.
    load: float | None
    #: The cycling share of it: what fitness was fed.
    fitness_load: float
    #: Sessions that day whose load was counted.
    n: int
    #: Sessions that day neither load model could price. They add nothing to
    #: either average, and `load_incomplete` says so.
    uncounted: int
    load_incomplete: bool


class SeriesMaturityRead(BaseModel):
    """Whether one average still mostly describes where it started.

    Maturing while the seed's remaining weight `(1 − α)ⁿ` is above 10 %: CTL
    through day 96, ATL through day 16 — or mature from day 1 when the series
    started from a supplied seed.
    """

    display_only: Literal[True]
    mature: bool
    seeded: bool
    #: The first mature day, counting the first session's date as day 1.
    matures_on_day: int
    #: That day's date; null with no session to count from.
    matures_on: dt.date | None


class FitnessMaturityRead(BaseModel):
    """How far into its history the series has been read."""

    #: The day the count starts on; null with no session recorded.
    first_session: dt.date | None
    #: The last day of the window, never after today.
    as_of: dt.date
    #: `as_of`'s day number; null when it precedes the first session.
    day: int | None
    ctl: SeriesMaturityRead
    atl: SeriesMaturityRead


class SeriesExplanationRead(ExplanationRead):
    """How one series is computed, stamped with its authority."""

    display_only: Literal[True]


class FitnessExplanationsRead(BaseModel):
    """The arithmetic behind each series — once per read, not per day."""

    ctl: SeriesExplanationRead
    atl: SeriesExplanationRead
    tsb: SeriesExplanationRead


class FitnessTrendRead(BaseModel):
    """Fitness, fatigue and form over a window, and how much to trust them."""

    #: Oldest first. Empty — never a run of zeros — before the first session.
    points: list[FitnessPointRead]
    maturity: FitnessMaturityRead
    #: The `L` in `TSB_d = CTL_{d−L} − ATL_{d−L}`.
    form_lag_days: int
    explanations: FitnessExplanationsRead

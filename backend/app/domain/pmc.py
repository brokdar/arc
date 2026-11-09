"""Fitness, fatigue and form: the performance-management chart, by the day.

Three numbers describe a training history's shape. **Fitness** (CTL, chronic
training load) and **fatigue** (ATL, acute training load) are exponentially
weighted moving averages of the daily load, and **form** (TSB) is the gap
between them:

    α  = 1 − e^(−1/τ)                     τ = 42 days for CTL, 7 for ATL
    X_d = X_{d−1} + α · (load_d − X_{d−1})

**The exponential weight, not the linear one.** The original Banister
formulation and intervals.icu both decay by `e^(−1/τ)` per day; the common
shortcut `α = 1/τ` is the first-order approximation of it and is off in the
fourth decimal at τ = 42 and the third at τ = 7 — a disagreement a verification
run against the incumbent would flag on every day of the series. Pinned by
`test_domain_pmc.py::test_alpha_is_one_minus_exp`.

**Every day is an update, including the ones with no session.** A rest day is
`load_d = 0` and the averages decay; skipping it would hold fitness flat across
a month off. The series therefore runs every calendar day from the first
session's `local_date` (the day the athlete would say they rode on, see
`app.persistence.activity.SessionRow.local_date`) to the day it is read on.

**These numbers are display-only.** They *describe* training. No deterministic
rule — scoring, matching, proposals, the guardrails — may read them, and
`app.domain.authority` records that so a test can hold every one of those
modules to it.

**Pure.** The service hands this module each session's already-selected load
(`app.domain.metrics.select_training_load`, stored on the metric artefact) and
the athlete's local today; nothing here reads a clock, a database or a stream.
"""

import datetime as dt
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from app.domain.activity import SessionDiscipline
from app.domain.metrics import MetricExplanation

#: The time constant of fitness, in days.
TAU_CTL = 42
#: The time constant of fatigue, in days.
TAU_ATL = 7

#: How much of the seed may still be in the average before it is called mature.
#: A tunable default, not a validated value — a chosen convention rather than a
#: published threshold; see :func:`maturity`.
MATURITY_SEED_WEIGHT = 0.10

#: Form's default lag, in days: today's form is today's fitness minus today's
#: fatigue. A tunable default, not a validated value: intervals.icu does not
#: document its lag, and `metric-policy-and-verification` calibrates this
#: against an intervals.icu export. See :func:`form`.
DEFAULT_FORM_LAG_DAYS = 0

_CITATION = (
    "Banister EW et al. (1975), A systems model of training for athletic "
    "performance; Coggan's performance-management chart"
)


def alpha(tau: float) -> float:
    """The EWMA's daily weight for time constant ``tau``: ``1 − e^(−1/τ)``."""
    return 1.0 - math.exp(-1.0 / tau)


#: CTL's daily weight, 0.0235283.
ALPHA_CTL = alpha(TAU_CTL)
#: ATL's daily weight, 0.1331221.
ALPHA_ATL = alpha(TAU_ATL)


@dataclass(frozen=True, slots=True)
class SessionLoad:
    """One recorded session, as the PMC needs to see it.

    Args:
        local_date: The day the session belongs to on the athlete's clock.
        discipline: What it was. Decides which averages it feeds.
        load: Its selected training load, or ``None`` when neither load model
            could price it — which is not the same thing as a load of zero.
    """

    local_date: dt.date
    discipline: SessionDiscipline
    load: float | None

    @property
    def feeds_fitness(self) -> bool:
        """Whether this session is a priced input to CTL: a priced ride.

        The one place "what fitness is fed" is decided, so the daily sum and
        the day fitness is first fed (:attr:`SeriesMaturity.first_priced`)
        cannot disagree. See :class:`DailyLoad` for why only cycling counts.
        """
        return self.load is not None and self.discipline is SessionDiscipline.CYCLING

    @property
    def feeds_fatigue(self) -> bool:
        """Whether this session is a priced input to ATL: any priced session."""
        return self.load is not None


@dataclass(frozen=True, slots=True)
class DailyLoad:
    """One calendar day's input to the averages.

    **Fitness is fed by cycling load only; fatigue by every discipline.** CTL
    is the chronic adaptation the plan is building, and arc plans cycling; a
    gym session's HR-based load (the only model a strength session has, and a
    low-trust one) is real strain the athlete carries into tomorrow, so it
    belongs in fatigue, but summing it into fitness would credit squats with
    aerobic adaptation they do not produce. Pinned by
    `test_fitness_api.py::test_strength_feeds_fatigue_not_fitness`.

    **An unpriced session is not a zero.** A session neither load model could
    price contributes nothing to either average — there is no number to add —
    and its day says so with ``load_incomplete``. Treating it as ``0`` would be
    the same arithmetic with the honesty removed: the chart would read as a
    rest day.

    Args:
        local_date: The day.
        fitness_load: Summed cycling load that day, the CTL input.
        fatigue_load: Summed load of every discipline that day, the ATL input.
        n: How many sessions that day had a load and were counted.
        uncounted: How many sessions that day could not be priced.
    """

    local_date: dt.date
    fitness_load: float
    fatigue_load: float
    n: int
    uncounted: int

    @property
    def load_incomplete(self) -> bool:
        """Whether a session that day is missing from the averages."""
        return self.uncounted > 0

    @property
    def load(self) -> float | None:
        """The day's total load, or ``None`` when only unpriced sessions ran.

        A rest day — no session at all — is a genuine ``0.0``. A day whose only
        sessions could not be priced has *no* known load, and says so.
        """
        if self.n == 0 and self.uncounted > 0:
            return None
        return self.fatigue_load


@dataclass(frozen=True, slots=True)
class Seed:
    """Starting values for the averages, when somebody knows them.

    The default is zero for both — the athlete arrives with no recorded past —
    and it is what makes a young series *maturing* (see :func:`maturity`). A
    seed supplied for a series (an imported history, a value the athlete reads
    off another platform) means that series starts from the truth and is
    mature on day one.
    """

    ctl: float | None = None
    atl: float | None = None


@dataclass(frozen=True, slots=True)
class PmcPoint:
    """One day of the series.

    Args:
        local_date: The day.
        day: Its position counting from the first session's date as day 1.
        ctl: Fitness after that day's update.
        atl: Fatigue after that day's update.
        tsb: Form at the series' declared lag.
        load: What went in.
    """

    local_date: dt.date
    day: int
    ctl: float
    atl: float
    tsb: float
    load: DailyLoad


@dataclass(frozen=True, slots=True)
class SeriesMaturity:
    """Whether one average is still dominated by where it started.

    **Each average is first fed on its own day.** ``first_priced`` is the
    first day, on or before ``as_of``, with a session that *this* average
    takes in (:attr:`SessionLoad.feeds_fitness` for CTL,
    :attr:`SessionLoad.feeds_fatigue` for ATL). Before it the average is the
    seed decaying with nothing behind it — ``0`` for an athlete whose history
    began in arc, which is no data rather than zero fitness — and with it
    ``None`` that is true of every day read. One date for both averages, as
    this was first built, let a priced gym session vouch for a fitness no ride
    had ever fed. Pinned by `test_domain_pmc.py::
    test_a_priced_gym_session_feeds_fatigue_and_leaves_fitness_unassessed`.

    It counts sessions only: a supplied seed gives the average a value before
    any of them, and the service supplies none today.

    Args:
        mature: Whether it is mature on the day the trend was read for.
        seeded: Whether it started from a supplied seed.
        matures_on_day: The first day number that is mature.
        matures_on: That day's date, or ``None`` with no session to count from.
        first_priced: The first day, on or before ``as_of``, a session fed
            this average; ``None`` when none did.
    """

    mature: bool
    seeded: bool
    matures_on_day: int
    matures_on: dt.date | None
    first_priced: dt.date | None


@dataclass(frozen=True, slots=True)
class FormStanding:
    """From which day form is a balance of two fed averages.

    **Form is assessed on day d when both of its inputs are**: ``TSB_d =
    CTL_{d−L} − ATL_{d−L}`` reads two averages ``L`` days back, so
    ``first_assessed`` is the later of the two ``first_priced`` days, plus
    ``L``. Fitness unfed while fatigue is fed is not a negative form — it is
    ``0 − ATL``, a fatigue with its sign flipped — so either input missing
    leaves form unassessed. ``None`` when either average has nothing priced
    behind it, or when that day falls after ``as_of``.

    No maturity of its own: form has no time constant, and how far to trust
    it is how far its two inputs have matured.

    Args:
        first_assessed: The first day form reads two fed averages.
    """

    first_assessed: dt.date | None


@dataclass(frozen=True, slots=True)
class Maturity:
    """How far into its history a series has been read.

    Args:
        first_session: The day the count starts on, or ``None`` with no
            session at all.
        as_of: The last day the trend was read for — the end of the window,
            never after today.
        day: ``as_of``'s day number counting from ``first_session`` as 1, or
            ``None`` when ``as_of`` precedes it (or there is no session).
        ctl: Fitness's standing.
        atl: Fatigue's standing.
        tsb: From when form is a balance of the two.
    """

    first_session: dt.date | None
    as_of: dt.date
    day: int | None
    ctl: SeriesMaturity
    atl: SeriesMaturity
    tsb: FormStanding


@dataclass(frozen=True, slots=True)
class FitnessTrend:
    """What :func:`fitness_trend` answers: the window's points and the standing."""

    points: tuple[PmcPoint, ...]
    maturity: Maturity
    as_of: dt.date
    form_lag_days: int


def daily_loads(
    sessions: Iterable[SessionLoad], *, through: dt.date
) -> list[DailyLoad]:
    """Fold sessions into one :class:`DailyLoad` per calendar day.

    Every day from the first session's date to ``through`` inclusive, empty
    days included. No sessions — or none on or before ``through`` — is an
    empty list, never a run of zeros: a series with nothing behind it has
    nothing to say.

    Sessions on one day **add before smoothing**: two rides of 30 and 50 are
    one update of 80, not two updates, because the EWMA's unit is the day.
    """
    held = [session for session in sessions if session.local_date <= through]
    if not held:
        return []
    by_day: dict[dt.date, list[SessionLoad]] = {}
    for session in held:
        by_day.setdefault(session.local_date, []).append(session)
    first = min(by_day)
    days: list[DailyLoad] = []
    for offset in range((through - first).days + 1):
        day = first + dt.timedelta(days=offset)
        sessions_that_day = by_day.get(day, [])
        priced = [s for s in sessions_that_day if s.load is not None]
        days.append(
            DailyLoad(
                local_date=day,
                fitness_load=sum(
                    s.load for s in priced if s.load is not None and s.feeds_fitness
                ),
                fatigue_load=sum(
                    s.load for s in priced if s.load is not None and s.feeds_fatigue
                ),
                n=len(priced),
                uncounted=len(sessions_that_day) - len(priced),
            )
        )
    return days


def pmc(
    days: Sequence[DailyLoad],
    *,
    seed: Seed = Seed(),  # noqa: B008 — frozen, immutable default
    form_lag_days: int = DEFAULT_FORM_LAG_DAYS,
) -> list[PmcPoint]:
    """Run both averages over consecutive days, and form beside them.

    ``days`` must be consecutive and start on the first session's date, as
    :func:`daily_loads` produces them; ``day`` counts from 1 on the first.
    """
    ctl = seed.ctl if seed.ctl is not None else 0.0
    atl = seed.atl if seed.atl is not None else 0.0
    ctls: list[float] = []
    atls: list[float] = []
    for day in days:
        ctl += ALPHA_CTL * (day.fitness_load - ctl)
        atl += ALPHA_ATL * (day.fatigue_load - atl)
        ctls.append(ctl)
        atls.append(atl)
    tsbs = form(ctls, atls, lag_days=form_lag_days, seed=seed)
    return [
        PmcPoint(
            local_date=day.local_date,
            day=index + 1,
            ctl=ctls[index],
            atl=atls[index],
            tsb=tsbs[index],
            load=day,
        )
        for index, day in enumerate(days)
    ]


def form(
    ctl: Sequence[float],
    atl: Sequence[float],
    *,
    lag_days: int = DEFAULT_FORM_LAG_DAYS,
    seed: Seed = Seed(),  # noqa: B008 — frozen, immutable default
) -> list[float]:
    """Form on each day: ``CTL_{d−L} − ATL_{d−L}``.

    **The lag ``L`` is a declared parameter, default 0.** TrainingPeaks reads
    form as *yesterday's* balance (``L = 1``: how you arrive at today's
    session), intervals.icu's convention is undocumented, and hard-coding
    either would bake an unverified guess into every number on the chart. The
    response carries the lag it was computed at, and the verification run
    against intervals.icu calibrates it rather than this docstring guessing.
    Pinned by `test_domain_pmc.py::test_form_lag_is_a_parameter`.

    Before the first day the balance is the seed's — ``0 − 0`` by default.

    Raises:
        ValueError: When the lag is negative: form cannot read tomorrow.
    """
    if lag_days < 0:
        raise ValueError(f"form lag must be zero or more days, got {lag_days}")
    seed_balance = (seed.ctl or 0.0) - (seed.atl or 0.0)
    return [
        ctl[index - lag_days] - atl[index - lag_days]
        if index - lag_days >= 0
        else seed_balance
        for index in range(len(ctl))
    ]


def matures_on_day(tau: float, *, seeded: bool) -> int:
    """The first day number on which a series with time constant ``tau`` is mature.

    Day 1 when seeded; otherwise the first ``n`` with
    ``(1 − α)ⁿ ≤`` :data:`MATURITY_SEED_WEIGHT` — 97 for CTL, 17 for ATL.
    """
    if seeded:
        return 1
    decay = 1.0 - alpha(tau)
    day = 1
    while decay**day > MATURITY_SEED_WEIGHT:
        day += 1
    return day


def maturity(day: int, *, tau: float, seeded: bool) -> SeriesMaturity:
    """Whether an average is still mostly its seed on day ``day``.

    **The rule is the seed's remaining weight: maturing while
    ``(1 − α)ⁿ > 0.10``**, n the day number counting the first session's day
    as 1. After n updates the starting value still carries ``(1 − α)ⁿ`` of the
    average, so until that drops to a tenth the number describes the seed —
    zero, for an athlete whose history began in arc — more than it describes
    the training. CTL is maturing through day 96 and mature from day 97; ATL
    through day 16 and mature from 17.

    This displaces the intuitive "42 days": an EWMA has no window, and on day
    42 the seed still carries ``e^(−1) ≈ 37 %`` of fitness — a curve that is
    a third made up, printed as if it were the athlete's. A *supplied* seed is
    the truth rather than a placeholder, so a seeded series is mature on day
    one. Pinned by `test_domain_pmc.py::test_maturity_days`.
    """
    first_mature = matures_on_day(tau, seeded=seeded)
    return SeriesMaturity(
        mature=day >= first_mature,
        seeded=seeded,
        matures_on_day=first_mature,
        matures_on=None,
        first_priced=None,
    )


def _standing(
    tau: float,
    *,
    seeded: bool,
    first: dt.date | None,
    day: int | None,
    first_priced: dt.date | None,
) -> SeriesMaturity:
    """One series' maturity, dated against the first session."""
    standing = maturity(day or 0, tau=tau, seeded=seeded)
    return SeriesMaturity(
        mature=day is not None and standing.mature,
        seeded=seeded,
        matures_on_day=standing.matures_on_day,
        matures_on=(
            None
            if first is None
            else first + dt.timedelta(days=standing.matures_on_day - 1)
        ),
        first_priced=first_priced,
    )


def _form_standing(
    ctl: SeriesMaturity, atl: SeriesMaturity, *, lag_days: int, as_of: dt.date
) -> FormStanding:
    """From when form reads two fed averages; see :class:`FormStanding`."""
    if ctl.first_priced is None or atl.first_priced is None:
        return FormStanding(first_assessed=None)
    later = max(ctl.first_priced, atl.first_priced)
    if (as_of - later).days < lag_days:
        return FormStanding(first_assessed=None)
    return FormStanding(first_assessed=later + dt.timedelta(days=lag_days))


def fitness_trend(
    sessions: Iterable[SessionLoad],
    *,
    start: dt.date | None,
    end: dt.date | None,
    today: dt.date,
    seed: Seed = Seed(),  # noqa: B008 — frozen, immutable default
    form_lag_days: int = DEFAULT_FORM_LAG_DAYS,
) -> FitnessTrend:
    """The series over the half-open window ``[start, end)``, and its maturity.

    The averages are always computed **from the first session**, whatever the
    window: a window is a view of the series, never a fresh EWMA restarted at
    its left edge, which would describe a different athlete. The window is
    clipped at ``today`` — no point is dated after it — and an omitted bound is
    open on that side.

    Maturity is reported as of the window's last day (``as_of``), counting the
    first session's date as day 1, so a window that starts on day 120 is still
    mature.
    """
    held = list(sessions)
    if end is None or end > today:
        as_of = today
    else:
        # `end` is exclusive. `date.min` has no day before it, and a window
        # ending there is empty whatever `as_of` says.
        as_of = end - dt.timedelta(days=1) if end > dt.date.min else dt.date.min
    first = min((s.local_date for s in held), default=None)
    read = [s for s in held if s.local_date <= as_of]
    day = (as_of - first).days + 1 if first is not None and as_of >= first else None
    series = pmc(
        daily_loads(held, through=as_of), seed=seed, form_lag_days=form_lag_days
    )
    points = tuple(
        point
        for point in series
        if (start is None or point.local_date >= start)
        and (end is None or point.local_date < end)
    )
    ctl = _standing(
        TAU_CTL,
        seeded=seed.ctl is not None,
        first=first,
        day=day,
        first_priced=min((s.local_date for s in read if s.feeds_fitness), default=None),
    )
    atl = _standing(
        TAU_ATL,
        seeded=seed.atl is not None,
        first=first,
        day=day,
        first_priced=min((s.local_date for s in read if s.feeds_fatigue), default=None),
    )
    return FitnessTrend(
        points=points,
        maturity=Maturity(
            first_session=first,
            as_of=as_of,
            day=day,
            ctl=ctl,
            atl=atl,
            tsb=_form_standing(ctl, atl, lag_days=form_lag_days, as_of=as_of),
        ),
        as_of=as_of,
        form_lag_days=form_lag_days,
    )


#: What both averages assume about their input, in the athlete's terms.
_SHARED_ASSUMPTIONS = (
    "a day with no session is a load of 0 and the average decays",
    (
        "a session neither load model could price is left out and its day is "
        "marked load_incomplete"
    ),
)


def _maturity_assumption(tau: float) -> str:
    """The maturity rule, stated with the day it lands on for ``tau``."""
    return (
        f"maturing until the seed's weight (1 − α)ⁿ falls to "
        f"{MATURITY_SEED_WEIGHT:.0%}: day {matures_on_day(tau, seeded=False)}"
    )


def ctl_explanation() -> MetricExplanation:
    """How fitness is computed, for the series as a whole."""
    return MetricExplanation(
        formula="CTL_d = CTL_{d−1} + α·(cycling load_d − CTL_{d−1}), α = 1 − e^(−1/42)",
        inputs=MappingProxyType(
            {"τ": f"{TAU_CTL} days", "α": f"{ALPHA_CTL:.6f}", "seed": "0"}
        ),
        assumptions=(
            "only cycling load feeds fitness",
            *_SHARED_ASSUMPTIONS,
            _maturity_assumption(TAU_CTL),
        ),
        citation=_CITATION,
    )


def atl_explanation() -> MetricExplanation:
    """How fatigue is computed, for the series as a whole."""
    return MetricExplanation(
        formula="ATL_d = ATL_{d−1} + α·(load_d − ATL_{d−1}), α = 1 − e^(−1/7)",
        inputs=MappingProxyType(
            {"τ": f"{TAU_ATL} days", "α": f"{ALPHA_ATL:.6f}", "seed": "0"}
        ),
        assumptions=(
            (
                "every discipline feeds fatigue; strength through its "
                "heart-rate load, which is a low-trust estimate"
            ),
            *_SHARED_ASSUMPTIONS,
            _maturity_assumption(TAU_ATL),
        ),
        citation=_CITATION,
    )


def tsb_explanation(lag_days: int) -> MetricExplanation:
    """How form is computed, at the lag the series was read at."""
    return MetricExplanation(
        formula="TSB_d = CTL_{d−L} − ATL_{d−L}",
        inputs=MappingProxyType({"L": f"{lag_days} days"}),
        assumptions=(
            (
                "the lag is a declared parameter: TrainingPeaks reads "
                "yesterday's balance (L = 1), and intervals.icu's convention "
                "is unconfirmed"
            ),
        ),
        citation=_CITATION,
    )

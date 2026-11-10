"""Every number arc coaches with, and the evidence behind it.

A cap, a threshold or a rest default is a training decision, not arithmetic.
Each one lives here as a :class:`Rule`: a stable dotted id the coach and an
explanation can cite, the value, a one-line statement of the rule, how well
it is supported, and — when it claims research support — the DOIs that back
it. Consumers read ``rules.PROGRESSION_MAX_INCREASE.value`` and carry
``.id`` into any explanation, so "why 95 kg?" can name the rule it applied.

Evidence tags, chosen so the tag says what a later change has to argue with:

* ``backed`` — a peer-reviewed source states this value or a range that
  contains it. At least one DOI is required; :class:`Rule` refuses to build
  without one.
* ``practice`` — established coaching practice with no trial behind the exact
  number; changing it needs a coaching argument, not a citation.
* ``default`` — arc's own starting choice where neither exists; free to tune.

Certainty is separate from evidence: a ``backed`` value from one small trial
is ``low``; a ``practice`` value every programme uses can be ``moderate``.

Why a registry rather than ``MAX_*`` constants beside their use (the style of
:mod:`app.domain.strength`, whose bounds are typo guards, not coaching): a
constant beside its use cannot say what evidence it has, and the next edit
changes it without anyone noticing a training decision moved. The modules
that compute training numbers — progression, warm-ups, loading, safety — may
not carry numeric literals beyond 0, 1 and list indices;
``tests/unit/test_domain_rules.py`` walks the domain and fails the build on
one. A new number goes here first, with its evidence, and is imported.

Percentages are fractions (``0.05`` is 5 %), per the units convention.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class Evidence(StrEnum):
    """What supports a rule's value; see the module docstring."""

    BACKED = "backed"
    PRACTICE = "practice"
    DEFAULT = "default"


class Certainty(StrEnum):
    """How confident arc is that the value is right for this athlete."""

    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"


RULE_ID_PATTERN = re.compile(r"[a-z][a-z0-9_]*(\.[a-z0-9_]+)+")
#: The DOI prefix shape (``10.<registrant>/``) — enough to refuse a URL or a
#: title pasted where a DOI belongs; resolving it is a human's job.
DOI_PATTERN = re.compile(r"10\.\d{4,}/\S+")


@dataclass(frozen=True, slots=True)
class Rule[T]:
    """One training constant with its id, statement and evidence.

    Validation runs at construction, so a malformed entry fails the import of
    this module — and with it every test — rather than waiting for a reviewer.
    """

    id: str
    value: T
    summary: str
    evidence: Evidence
    certainty: Certainty
    dois: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Refuse a malformed id or DOI, and a backed rule with no DOI."""
        if not RULE_ID_PATTERN.fullmatch(self.id):
            raise ValueError(
                f"rule id {self.id!r} must be dotted lowercase, e.g. 'progression.cap.up'"
            )
        for doi in self.dois:
            if not DOI_PATTERN.fullmatch(doi):
                raise ValueError(f"{self.id}: {doi!r} is not a DOI (10.NNNN/...)")
        if self.evidence is Evidence.BACKED and not self.dois:
            raise ValueError(f"{self.id} is tagged backed but cites no DOI")


def build_registry(*entries: Rule[object]) -> Mapping[str, Rule[object]]:
    """Index rules by id, refusing a duplicate — two rules, one citation."""
    out: dict[str, Rule[object]] = {}
    for rule in entries:
        if rule.id in out:
            raise ValueError(f"duplicate rule id {rule.id!r}")
        out[rule.id] = rule
    return MappingProxyType(out)


# --- sources ---------------------------------------------------------------
# Verified against PubMed / publisher records when the registry was written.

#: ACSM position stand, Progression models in resistance training for healthy
#: adults (2009): raise load 2–10 % once the target reps are exceeded by 1–2.
_ACSM_2009 = "10.1249/MSS.0b013e3181915670"
#: Reynolds, Gordon & Robergs (2006): no more than 10 reps in a linear 1RM
#: estimate.
_REYNOLDS_2006 = "10.1519/R-15304.1"
#: Zourdos et al. (2016): the RIR-based RPE scale (RPE 10 = 0 reps in reserve).
_ZOURDOS_2016 = "10.1519/JSC.0000000000001049"
#: Refalo et al. (2023): proximity to failure and hypertrophy; non-failure sets
#: close to failure grow muscle about as well as sets to failure.
_REFALO_2023 = "10.1007/s40279-022-01784-y"
#: Schoenfeld et al. (2016): 3-minute rest beat 1-minute for strength and size.
_SCHOENFELD_2016 = "10.1519/JSC.0000000000001272"


# --- progression -----------------------------------------------------------

PROGRESSION_MAX_INCREASE = Rule(
    id="progression.cap.up",
    value=0.05,
    summary="An automatic rise on a main lift is at most 5 % of the last working weight.",
    evidence=Evidence.BACKED,
    certainty=Certainty.MODERATE,
    dois=(_ACSM_2009,),
)
PROGRESSION_MAX_DECREASE = Rule(
    id="progression.cap.down",
    value=0.10,
    summary="An automatic drop is at most 10 %, so one bad day cannot erase weeks.",
    evidence=Evidence.PRACTICE,
    certainty=Certainty.MODERATE,
)
SMALL_MUSCLE_MAX_STEPS = Rule(
    id="progression.cap.small_muscle_steps",
    value=1,
    summary=(
        "A small-muscle exercise rises by at most one loadable step, since one "
        "dumbbell step can already exceed 5 %."
    ),
    evidence=Evidence.PRACTICE,
    certainty=Certainty.MODERATE,
)
LOAD_PER_REP = Rule(
    id="progression.load_per_rep",
    value=0.025,
    summary="One rep more or fewer at a target is worth roughly 2.5 % of load.",
    evidence=Evidence.PRACTICE,
    certainty=Certainty.LOW,
)
BACKOFF_DROP = Rule(
    id="progression.backoff_drop",
    value=0.075,
    summary="Lighter sets after a heavy set sit 7.5 % below the logged heavy set.",
    evidence=Evidence.PRACTICE,
    certainty=Certainty.LOW,
)
PLATEAU_WINDOW_SHORT = Rule(
    id="progression.plateau.short_window",
    value=4,
    summary="Four exposures without progress on a lift raise a holding-steady check.",
    evidence=Evidence.DEFAULT,
    certainty=Certainty.LOW,
)
PLATEAU_WINDOW_LONG = Rule(
    id="progression.plateau.long_window",
    value=6,
    summary="Six exposures without progress call a plateau and suggest a response.",
    evidence=Evidence.DEFAULT,
    certainty=Certainty.LOW,
)


# --- effort and estimates -----------------------------------------------------

HARD_SET_MAX_RIR = Rule(
    id="effort.hard_set_max_rir",
    value=3,
    summary="A set ending with 3 or fewer reps in reserve counts as a hard set.",
    evidence=Evidence.BACKED,
    certainty=Certainty.LOW,
    dois=(_REFALO_2023, _ZOURDOS_2016),
)
E1RM_MAX_REPS = Rule(
    id="estimate.e1rm_max_reps",
    value=10,
    summary="An estimated max is computed only from sets with reps plus reps left of 10 or fewer.",
    evidence=Evidence.BACKED,
    certainty=Certainty.MODERATE,
    dois=(_REYNOLDS_2006,),
)


# --- rest ----------------------------------------------------------------------

REST_MAIN_SECONDS = Rule(
    id="rest.main",
    value=180,
    summary="Main compound lifts default to 3 minutes of rest.",
    evidence=Evidence.BACKED,
    certainty=Certainty.MODERATE,
    dois=(_SCHOENFELD_2016,),
)
REST_ACCESSORY_SECONDS = Rule(
    id="rest.accessory",
    value=120,
    summary="Accessory compound lifts default to 2 minutes of rest.",
    evidence=Evidence.PRACTICE,
    certainty=Certainty.MODERATE,
)
REST_ISOLATION_SECONDS = Rule(
    id="rest.isolation",
    value=90,
    summary="Single-joint and small-muscle exercises default to 90 seconds of rest.",
    evidence=Evidence.PRACTICE,
    certainty=Certainty.MODERATE,
)
REST_SUPERSET_TRANSITION_SECONDS = Rule(
    id="rest.superset_transition",
    value=15,
    summary="Moving between exercises inside a superset defaults to 15 seconds.",
    evidence=Evidence.DEFAULT,
    certainty=Certainty.LOW,
)


# --- warm-ups --------------------------------------------------------------------

WARMUP_BAR_REPS = Rule(
    id="warmup.bar_reps",
    value=10,
    summary="A warm-up ramp opens with the empty bar for 10 reps.",
    evidence=Evidence.PRACTICE,
    certainty=Certainty.MODERATE,
)
WARMUP_RAMP = Rule(
    id="warmup.ramp",
    value=((0.4, 5), (0.6, 3), (0.8, 2)),
    summary=(
        "After the bar, warm-ups climb at 40 %, 60 % and 80 % of the working "
        "weight for 5, 3 and 2 reps, skipping steps at or below the bar."
    ),
    evidence=Evidence.PRACTICE,
    certainty=Certainty.MODERATE,
)


# --- starting weight and mid-session --------------------------------------------

STARTING_STEP_EASY = Rule(
    id="starting.step_easy",
    value=0.20,
    summary="In the starting flow, a set that felt easy steps the weight up by 20 %.",
    evidence=Evidence.PRACTICE,
    certainty=Certainty.LOW,
)
STARTING_STEP_GOOD = Rule(
    id="starting.step_good",
    value=0.10,
    summary="In the starting flow, a set that felt good steps the weight up by 10 %.",
    evidence=Evidence.PRACTICE,
    certainty=Certainty.LOW,
)
MID_SESSION_MISS_REPS = Rule(
    id="session.miss_reps",
    value=2,
    summary="Missing the target by 2 or more reps lowers the remaining sets by one step.",
    evidence=Evidence.PRACTICE,
    certainty=Certainty.MODERATE,
)


REGISTRY: Mapping[str, Rule[object]] = build_registry(
    PROGRESSION_MAX_INCREASE,
    PROGRESSION_MAX_DECREASE,
    SMALL_MUSCLE_MAX_STEPS,
    LOAD_PER_REP,
    BACKOFF_DROP,
    PLATEAU_WINDOW_SHORT,
    PLATEAU_WINDOW_LONG,
    HARD_SET_MAX_RIR,
    E1RM_MAX_REPS,
    REST_MAIN_SECONDS,
    REST_ACCESSORY_SECONDS,
    REST_ISOLATION_SECONDS,
    REST_SUPERSET_TRANSITION_SECONDS,
    WARMUP_BAR_REPS,
    WARMUP_RAMP,
    STARTING_STEP_EASY,
    STARTING_STEP_GOOD,
    MID_SESSION_MISS_REPS,
)

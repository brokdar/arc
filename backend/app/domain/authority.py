"""Which metrics may decide something, and which may only describe.

arc computes two kinds of number. Some are **decision-capable**: a session's
training load, its time in zone, an anchor — the scoring engine, the matcher,
the proposal rules and the guardrails read them and act. Others are
**display-only**: they *describe* the training and are shown to the athlete
and the coach, but no deterministic rule may read them. Fitness, fatigue and
form are the first of those. They are smoothed, lagging, seed-dependent
summaries whose calibration against the incumbent is still a parameter
(`app.domain.pmc.form`'s lag), and a rule that moved a plan because CTL
crossed a line would be acting on a number arc itself says it is not sure of.
The athlete is told the same thing on screen: arc will never change their
plan because of this number.

**This module is the registry, and it is pure data.** Two things make it bind
rather than ask:

* every serializer that returns one of these metrics stamps it
  ``display_only: true`` — in the API's schema and in the MCP tool's result —
  and `tests/unit/test_display_only_authority.py` fails if a field keyed by a
  metric named here is not stamped;
* that test walks :data:`DETERMINISTIC_MODULES` and fails if any of them
  imports a module in :func:`display_only_sources`.

A later metric that only describes adds one entry here and is held to both
checks without anyone remembering to write a new test. This displaces a
docstring that asks nobody to do it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class DisplayOnlyMetric:
    """One metric no deterministic rule may consume.

    Args:
        key: The field name it is published under, in the API and over MCP.
        name: What the athlete calls it.
        computed_in: The dotted module whose functions compute it.
        served_by: The dotted modules that hand it out — importing one of
            those is as good as computing it.
    """

    key: str
    name: str
    computed_in: str
    served_by: tuple[str, ...] = ()


_PMC = "app.domain.pmc"
_FITNESS_SERVICE = "app.services.fitness"

#: Every display-only metric, by the key it is published under.
DISPLAY_ONLY_METRICS: Mapping[str, DisplayOnlyMetric] = MappingProxyType(
    {
        metric.key: metric
        for metric in (
            DisplayOnlyMetric("ctl", "fitness", _PMC, (_FITNESS_SERVICE,)),
            DisplayOnlyMetric("atl", "fatigue", _PMC, (_FITNESS_SERVICE,)),
            DisplayOnlyMetric("tsb", "form", _PMC, (_FITNESS_SERVICE,)),
        )
    }
)

#: The modules that decide something about the athlete's training: score it,
#: match it, propose a change to it, or gate the agent that does. None of them
#: may import a display-only source. Listed by dotted name, and a name that no
#: longer resolves to a file fails the walk — a rename cannot quietly shrink it.
DETERMINISTIC_MODULES: tuple[str, ...] = (
    "app.services.scoring",
    "app.services.matching",
    "app.services.proposals",
    "app.services.guardrails",
    "app.domain.scoring",
    "app.domain.matching",
    "app.domain.proposals",
    "app.domain.criteria",
)


def is_display_only(key: str) -> bool:
    """Whether the metric published under ``key`` is display-only."""
    return key in DISPLAY_ONLY_METRICS


def display_only_sources() -> frozenset[str]:
    """Every module that computes or serves a display-only metric."""
    return frozenset(
        module
        for metric in DISPLAY_ONLY_METRICS.values()
        for module in (metric.computed_in, *metric.served_by)
    )

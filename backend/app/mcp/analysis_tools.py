"""The coaching agent's analysis reads: the numbers the athlete looks at.

**New analysis tools register here, not in `app.mcp.tools`.** That module is
one 2,500-line `register_tools` carrying a `# noqa: C901`, and every capability
appended to it makes the next one harder to read. Fitness, the power curve,
decoupling and the efforts the athlete asks about are one family — reads of
what arc computed about training, all display-only or descriptive — and they
grow here, each as one tool and one line in :func:`register_analysis_tools`'s
body. Pinned by `test_mcp_tools.py::test_the_surface_is_exactly_this`, which
also checks that this module registers exactly :data:`ANALYSIS_TOOLS`.

The conventions are `app.mcp.tools`'s, unchanged: ``require_scope`` first, a
``session_scope()`` the service commits (reads commit nothing), one service
call, a projection from `app.mcp.views`, and ``red_flag`` on every read.
"""

import datetime as dt
from typing import Any

from fastmcp import FastMCP

from app.core.clock import athlete_today
from app.mcp import views
from app.mcp.auth import Scope
from app.mcp.identity import require_scope
from app.mcp.tools import tool_errors
from app.persistence.db import session_scope
from app.services.fitness import FitnessService
from app.services.guardrails import current_profile

#: How many days `get_fitness_trend` returns when no `start` is given — a
#: training block and its run-in, small enough that one call stays one call.
DEFAULT_FITNESS_DAYS = 90

#: Every tool this module registers.
ANALYSIS_TOOLS = frozenset({"get_fitness_trend"})


def register_analysis_tools(mcp: FastMCP) -> None:
    """Register the analysis reads on ``mcp``."""

    @mcp.tool
    async def get_fitness_trend(
        start: str | None = None, end: str | None = None
    ) -> dict[str, Any]:
        """Fitness (CTL), fatigue (ATL) and form (TSB), one point per day.

        **Display-only: describe with it, never decide with it.** Every value
        is stamped `display_only: true`; arc never changes the athlete's plan
        because of these numbers, and neither should a rule you state as one.
        Use them to say what the load curve looks like and how the plan is
        shaping it.

        * `ctl` is the 42-day exponentially weighted average of **cycling**
          load, `atl` the 7-day one of **every** discipline's load (strength
          through a low-trust heart-rate load), `tsb` is `ctl − atl` at
          `form_lag_days`. Loads are TSS-equivalent.
        * Every day is a point, rest days included. Before the first session
          there are none — an empty `points` is an empty history, not zero
          fitness.
        * `load` is null on a day whose sessions could not be priced; such a
          session adds nothing and its day says `load_incomplete: true`. `n`
          is how many sessions the day counted.
        * `maturity`: an average that started from zero is still mostly that
          zero until CTL's day 97 and ATL's day 17, counted from the first
          session. While `mature` is false, say the number is maturing rather
          than quoting it as the athlete's fitness.

        Args:
            start: First local date, `YYYY-MM-DD`, inclusive. Omit for the last
                90 days.
            end: First local date **after** the range — half-open
                `[start, end)`. Omit for through today; later is clipped.

        Requires a `read` key.
        """
        require_scope(Scope.READ)
        with tool_errors():
            today = athlete_today()
            first = (
                views.as_date(start, field="start")
                if start is not None
                else today - dt.timedelta(days=DEFAULT_FITNESS_DAYS - 1)
            )
            last = None if end is None else views.as_date(end, field="end")
            async with session_scope() as session:
                trend = await FitnessService.from_session(session).trend(
                    start=first, end=last, today=today
                )
                return {
                    "fitness": views.fitness_trend(trend),
                    "red_flag": views.red_flag(await current_profile(session)),
                }

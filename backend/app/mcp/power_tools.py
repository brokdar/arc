"""The coaching agent's power reads: the curves the athlete looks at.

**Power tools register here, not in `app.mcp.tools`.** That module is one
2,500-line `register_tools` carrying a `# noqa: C901`, and every capability
appended to it makes the next one harder to read. The power curve is the first
of a family (records, the critical-power fit, effort search) that grows here,
each as one tool in :func:`register_power_tools`'s body. Pinned by
`test_mcp_tools.py::test_the_power_tools_register_from_their_own_module`, and
swept by the scope check beside `register_tools`.

The conventions are `app.mcp.tools`'s, unchanged: ``require_scope`` first, a
``session_scope()`` (reads commit nothing), one service call, a projection,
and ``red_flag`` on every read.
"""

from typing import Any

from fastmcp import FastMCP

from app.mcp import views
from app.mcp.auth import Scope
from app.mcp.identity import require_scope
from app.mcp.tools import tool_errors
from app.persistence.db import session_scope
from app.services.guardrails import current_profile
from app.services.power_curve import PowerCurveService, SessionPowerCurve

#: Every tool this module registers.
POWER_TOOLS = frozenset({"get_power_curve"})


def power_curve(answer: SessionPowerCurve) -> dict[str, Any]:
    """The tool's projection of one session's curve — the endpoint's fields.

    One shape for both answers, so a caller reads ``status`` and never a
    missing key: an unassessed curve has empty ``points`` and a ``reason``.
    """
    curve = answer.curve
    return {
        "session_id": str(answer.session_id),
        "status": "not_assessed" if curve is None else "assessed",
        "reason": answer.reason,
        "grid_version": None if curve is None else curve.grid_version,
        "points": []
        if curve is None
        else [
            {
                "duration_s": point.duration_s,
                "watts": point.watts,
                "start_index": point.start_index,
                "clean": point.clean,
            }
            for point in curve.points
        ],
    }


def register_power_tools(mcp: FastMCP) -> None:
    """Register the power reads on ``mcp``."""

    @mcp.tool
    async def get_power_curve(session_id: str) -> dict[str, Any]:
        """One recorded session's best mean power for each duration.

        `points` holds, per duration, the highest average `watts` (to 0.1 W)
        the athlete held for exactly `duration_s` seconds anywhere in the ride,
        ascending by duration: every second up to 2 min, then every 2 s to
        10 min, 5 s to 20 min, 20 s to 60 min, 2 min to 2 h, and 5 min beyond
        (`grid_version` names that grid). Durations longer than the ride are
        absent — there is no 60-minute best on a 40-minute ride.

        * `start_index` is where the window begins, in seconds from the start
          of the session's stream; the window is `[start_index, start_index +
          duration_s)`.
        * `clean` is false when the window includes seconds arc had to fill
          (a dropout or a recording stop, read as zero) or a region the
          cleaner repaired. Do not quote an unclean best as a performance
          without saying so.

        `status` is `not_assessed` — with `reason`, and no points — when the
        session has no power meter, was entered by hand, or was recorded
        before curves existed. Never read that as zero watts.

        Args:
            session_id: The recorded session, as from `list_sessions`.

        Requires a `read` key.
        """
        require_scope(Scope.READ)
        with tool_errors():
            async with session_scope() as session:
                answer = await PowerCurveService.from_session(session).read(
                    views.as_uuid(session_id, field="session_id")
                )
                return {
                    **power_curve(answer),
                    "red_flag": views.red_flag(await current_profile(session)),
                }

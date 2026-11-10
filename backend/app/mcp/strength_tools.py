"""The coaching agent's strength tools: cards first, then everything the gym adds.

**Strength tools register here, not in `app.mcp.tools`** — that module is one
2,600-line `register_tools`, and the strength milestone adds a family of tools
(histories, next targets, swaps). Each lands as one tool in
:func:`register_strength_tools`'s body, appended at the end, and its name in
:data:`STRENGTH_TOOLS`. Pinned by
`test_mcp_tools.py::test_the_strength_tools_register_from_their_own_module`.

The conventions are `app.mcp.tools`'s: ``require_scope`` first, a
``session_scope()``, one service call, a projection, and ``red_flag`` on every
read.
"""

import datetime as dt
from typing import Any

from fastmcp import FastMCP

from app.mcp import views
from app.mcp.auth import Scope
from app.mcp.identity import require_scope
from app.mcp.tools import tool_errors
from app.persistence.cards import CardRow
from app.persistence.db import session_scope
from app.services.cards import CardService
from app.services.guardrails import current_profile

#: Every tool this module registers. Later PRs append; never reorder.
STRENGTH_TOOLS = frozenset({"get_cards"})


def _iso(moment: dt.datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def card(row: CardRow) -> dict[str, Any]:
    """The tool's projection of one card — `GET /cards`' fields."""
    return {
        "id": str(row.id),
        "kind": row.kind,
        "priority": row.priority,
        "state": row.state.value,
        "headline": row.headline,
        "because": row.because,
        "payload": row.payload,
        "first_shown": _iso(row.first_shown),
        "expires_at": _iso(row.expires_at),
        "snoozed_until": _iso(row.snoozed_until),
    }


def register_strength_tools(mcp: FastMCP) -> None:
    """Register the strength tools on ``mcp``."""

    @mcp.tool
    async def get_cards() -> dict[str, Any]:
        """The suggestion card the athlete sees on Today right now, if any.

        `cards` holds at most one card, and is `[]` when nothing is due, when
        the day's or week's budget is spent (one new card a day, three a plan
        week; safety and welcome-back cards are outside it), or while a
        session is active — cards never appear between Start and Finish.

        Each card has a `headline`, one `because` sentence, a `kind`, a
        `priority` (1 safety, 2 today, 3 sunday, 4 digest), its `state`, and
        `first_shown` / `expires_at` (seven days after first shown).

        Reading writes nothing: unlike the athlete opening Today, your read
        neither marks the card shown nor spends the athlete's budget.

        Requires a `read` key.
        """
        require_scope(Scope.READ)
        with tool_errors():
            async with session_scope() as session:
                rows = await CardService.from_session(session).peek()
                return {
                    "cards": [card(row) for row in rows],
                    "red_flag": views.red_flag(await current_profile(session)),
                }

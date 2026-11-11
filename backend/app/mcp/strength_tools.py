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
from app.services.exercise_preferences import ExercisePreferencesService
from app.services.guardrails import current_profile
from app.services.strength_history import ExerciseHistory, StrengthHistoryService

#: Every tool this module registers. Later PRs append; never reorder.
STRENGTH_TOOLS = frozenset({"get_cards", "get_exercise_history", "suggest_substitutes"})


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


def exercise_history(history: ExerciseHistory) -> dict[str, Any]:
    """The tool's projection of an exercise's records — the endpoint's fields.

    Key for key `GET /exercises/{id}/history`, so the coach and the athlete
    read the same bests and line (`test_mcp_tools.py` compares the two).
    """
    records = history.records
    return {
        "exercise_id": history.exercise.id,
        "exercise_name": history.exercise.name,
        "exposures": records.exposures,
        "bests": [
            {
                "load_kg": b.load_kg,
                "reps": b.reps,
                "date": b.date.isoformat(),
                "session_id": str(b.session_id),
            }
            for b in records.bests
        ],
        "rep_maxes": [
            {
                "target_reps": m.target_reps,
                "load_kg": m.load_kg,
                "reps": m.reps,
                "date": m.date.isoformat(),
                "session_id": str(m.session_id),
            }
            for m in records.rep_maxes
        ],
        "estimate": {
            "status": records.estimate.status,
            "current_kg": records.estimate.current_kg,
            "points": [
                {
                    "session_id": str(p.session_id),
                    "date": p.date.isoformat(),
                    "estimate_kg": p.estimate_kg,
                    "median_kg": p.median_kg,
                }
                for p in records.estimate.points
            ],
        },
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

    @mcp.tool
    async def get_exercise_history(exercise_id: str) -> dict[str, Any]:
        """One catalogue exercise's personal bests and estimated max.

        `exercise_id` is a catalogue slug (`get_exercise_catalogue`); an
        unknown slug is an error, an exercise never logged returns empty lists.

        - `bests`: every set no other set beat on both weight and reps,
          heaviest first, each with the `date` and `session_id` it was first
          lifted. Warm-ups, timed and bodyweight sets never count; a one-side
          lift counts what its weaker side did.
        - `rep_maxes`: the heaviest weight for at least 1, 3, 5, 8 and 10 reps
          (only the counts reached), read off `bests`.
        - `estimate`: a rough guide, not a record —
          `kg × (1 + (reps + reps left) / 30)` over sets rated with a reps-left
          count (not "4+", not unrated) with reps + reps left ≤ 10, outside
          warm-ups and don't-learn sessions. `points` holds each session's
          best estimate and `median_kg`, the median of the last three;
          `status` is `not_enough_data` (and `current_kg` null) until three
          sessions produced one. A rising estimate is never a personal best.

        Requires a `read` key.
        """
        require_scope(Scope.READ)
        with tool_errors():
            async with session_scope() as session:
                history = await StrengthHistoryService.from_session(
                    session
                ).exercise_history(exercise_id)
                return {
                    **exercise_history(history),
                    "red_flag": views.red_flag(await current_profile(session)),
                }

    @mcp.tool
    async def suggest_substitutes(exercise_id: str) -> dict[str, Any]:
        """Up to three catalogue exercises to swap in for one, best first.

        The same ranking the athlete's phone carries for every planned line,
        so what you suggest is what the gym screen offers. A swap shares a
        main muscle group and the movement pattern (or a close one: squat and
        lunge, horizontal and vertical push or pull), needs only equipment the
        gym has, and never makes a demand recent sharp pain rules out or names
        an exercise the athlete avoids. Order: the athlete's remembered pick,
        then more shared main groups, same pattern, same mechanic, fewer new
        demands, exercises they have logged before; "less often" ones last.
        Fewer than three, or none, when fewer qualify — never a poor fit.

        `exercise_id` is a catalogue slug (`get_exercise_catalogue`); an
        unknown slug is an error. Returns `substitutes` as
        `{exercise_id, exercise_name}`.

        Requires a `read` key.
        """
        require_scope(Scope.READ)
        with tool_errors():
            async with session_scope() as session:
                rows = await ExercisePreferencesService.from_session(
                    session
                ).substitutes(exercise_id)
                return {
                    "exercise_id": exercise_id,
                    "substitutes": [
                        {"exercise_id": row.id, "exercise_name": row.name}
                        for row in rows
                    ],
                    "red_flag": views.red_flag(await current_profile(session)),
                }

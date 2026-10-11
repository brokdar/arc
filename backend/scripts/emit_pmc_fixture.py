"""Generate the frontend's fitness-series fixture by running the real domain.

A fixture must be a payload the real API could produce (the repo's testing
strategy, rule 3), and the fitness read is all derived arithmetic: every CTL is
an exponentially weighted average over every day before it, ATL the same at a
different time constant, form their difference, and the maturity verdict a
threshold on how much of the seed is left. A hand-typed CTL of `41` on day 40
type-checks perfectly and is a number the EWMA cannot produce from any history
that also has the loads printed beside it — and the component test would then
agree with the fixture instead of with the application.

So the whole document is generated: this script holds a stored forty-day
history (:data:`HISTORY`), runs `app.domain.pmc.fitness_trend` over it, and
projects the result through **`app.api.routes.fitness.to_fitness_read`** — the
very function the endpoint uses — so the emitted JSON is byte for byte a
response the API serves.

The history is shaped for what `/fitness` has to render:

* forty days, so CTL is **maturing** (day 40 of 97) while ATL is mature —
  the annotation and its absence on one page;
* rest days, so the series decays between sessions rather than stepping;
* gym sessions priced by heart rate, which feed fatigue and not fitness;
* one gym session **nobody could price**, so its day is `load_incomplete`.

It also emits the empty read — the domain over no sessions at all — and the
unpriced one: a history whose every session neither load model could price
(:data:`UNPRICED_HISTORY`), which the domain answers with points of zero and
no ``first_priced_session``.

Run it with ``just pmc-fixture`` after changing `app/domain/pmc.py` or the
fitness read shape, and commit the result.
"""

import datetime as dt
import json
import sys
from pathlib import Path

from app.api.routes.fitness import to_fitness_read
from app.domain.activity import SessionDiscipline
from app.domain.pmc import SessionLoad, fitness_trend

ROOT = Path(__file__).resolve().parents[2]
DESTINATION = ROOT / "frontend" / "tests" / "mocks" / "generated-pmc.ts"

#: The day the document is read on. Fixed, so the file is stable in git; the
#: mock rebases it onto the athlete's today, which moves the calendar and none
#: of the numbers.
AS_OF = dt.date(2026, 8, 15)

#: How many days of history: CTL is still maturing at the end of it. The first
#: day is a Tuesday — a session — so the read's last day is day 40.
DAYS = 40

#: The offset (days after the first session) of the gym session no load model
#: could price — a Thursday, a gym day.
UNPRICED_OFFSET = 23

CYCLING = SessionDiscipline.CYCLING
STRENGTH = SessionDiscipline.STRENGTH


def _history() -> tuple[SessionLoad, ...]:
    """Forty days of a synthetic athlete's sessions, oldest first."""
    first = AS_OF - dt.timedelta(days=DAYS - 1)
    sessions: list[SessionLoad] = []
    for offset in range(DAYS):
        day = first + dt.timedelta(days=offset)
        weekday = day.weekday()
        if weekday in (0, 4):  # Monday and Friday off.
            continue
        ride = {1: 72.0, 2: 55.0, 3: 88.0, 5: 145.0, 6: 96.0}[weekday]
        # A slow build across the block, so the curve has a direction.
        sessions.append(SessionLoad(day, CYCLING, round(ride + offset * 0.8, 1)))
        if weekday in (1, 3):
            load = None if offset == UNPRICED_OFFSET else 30.0
            sessions.append(SessionLoad(day, STRENGTH, load))
    if not any(s.load is None for s in sessions):
        raise SystemExit(
            "the history lost its unpriced session — check UNPRICED_OFFSET"
        )
    return tuple(sessions)


#: The stored history the fixture is computed from.
HISTORY = _history()

#: Manual sessions only — a ride logged without power or heart rate and two gym
#: sessions without heart rate — over the last ten days of the read.
UNPRICED_HISTORY = (
    SessionLoad(AS_OF - dt.timedelta(days=9), CYCLING, None),
    SessionLoad(AS_OF - dt.timedelta(days=6), STRENGTH, None),
    SessionLoad(AS_OF - dt.timedelta(days=2), STRENGTH, None),
)

HEADER = """// GENERATED FILE — do not hand-edit.
//
// Produced by `backend/scripts/emit_pmc_fixture.py` (`just pmc-fixture`) by
// running `app.domain.pmc.fitness_trend` over a stored forty-day history and
// projecting the result through `app.api.routes.fitness.to_fitness_read` — the
// same function the endpoint uses. Every CTL, ATL, form value and maturity
// verdict below is therefore the domain's own, which a hand-typed fixture
// cannot promise.
//
// Regenerate after changing app/domain/pmc.py or the fitness read shape, and
// commit the result.

import type { components } from "@/generated/api/schema";

type Schemas = components["schemas"];

/**
 * Forty days of training read on its last day: CTL maturing (day 40 of 97),
 * ATL mature, rest days decaying, and one `load_incomplete` day where a gym
 * session could not be priced.
 *
 * The dates end on a fixed day so the file is stable in git;
 * `tests/mocks/handlers-fitness.ts` rebases the whole document onto the
 * athlete's today, which moves the calendar and none of the numbers.
 */
export const FITNESS_TREND: Schemas["FitnessTrendRead"] = """

EMPTY_HEADER = """;

/** The same read over no sessions at all: no points, and a maturity. */
export const FITNESS_TREND_EMPTY: Schemas["FitnessTrendRead"] = """

UNPRICED_HEADER = """;

/**
 * Ten days whose every session no load model could price: points whose
 * averages are 0 with nothing behind them, and no `first_priced_session`.
 */
export const FITNESS_TREND_UNPRICED: Schemas["FitnessTrendRead"] = """


def _dump(sessions: tuple[SessionLoad, ...]) -> str:
    """One trend read over ``sessions``, as the endpoint serializes it."""
    trend = fitness_trend(sessions, start=None, end=None, today=AS_OF)
    payload = to_fitness_read(trend).model_dump(mode="json")
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)


def main() -> None:
    """Write the generated module."""
    body = (
        HEADER
        + _dump(HISTORY)
        + EMPTY_HEADER
        + _dump(())
        + UNPRICED_HEADER
        + _dump(UNPRICED_HISTORY)
        + ";\n"
    )
    DESTINATION.write_text(body, encoding="utf-8")
    print(f"wrote {DESTINATION.relative_to(ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    main()

"""Generate the frontend's suggested-weight fixtures from the domain.

A fixture must be a payload the real API could produce (the repo's testing
strategy, rule 3). A suggestion is arithmetic — Epley's estimate over the
reps and reps left of two sessions, limits, then the nearest loadable weight
— and a hand-typed ``computed_kg: 102.6`` beside sets that make 102.63 would
type-check and agree with nothing.

So the documents are generated: this script runs
`app.domain.progression.suggest` over stored sessions and projects the result
through **`app.api.schemas.packet.SuggestionRead`** — the model the packet
endpoint serves — so the emitted JSON is a response the API produces.

* :data:`EASIER_TWICE` is the direction's worked example (AC-68's edge case):
  target 6 with 2 left, two sessions of 100 kg × 6 with 3 left → 102.5 kg.
* :data:`CAPPED_DOWN` is AC-68's big miss: target 8 with 2 left, 100 kg × 5
  with 0 left → computed 87.5 kg, held at 90 kg by the 10 % limit.
* :data:`FIRST_TIME` is an exercise never logged.

Run it with ``just progression-fixture`` after changing
`app/domain/progression.py` or the suggestion read shape, and commit the
result.
"""

import datetime as dt
import json
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path

from app.api.schemas.packet import SuggestionRead
from app.domain.loading import Equipment
from app.domain.metrics import PerformedSet
from app.domain.progression import LineGoal, suggest
from app.domain.strength import Progression
from app.domain.strength_records import Exposure

ROOT = Path(__file__).resolve().parents[2]
DESTINATION = ROOT / "frontend" / "tests" / "mocks" / "generated-progression.ts"

FIRST_DAY = dt.date(2026, 9, 1)


def _goal(reps: int, left: int) -> LineGoal:
    return LineGoal(
        rule=Progression.RIR_LOAD,
        reps=reps,
        reps_left=left,
        main=True,
        small_muscle=False,
        equipment=Equipment.BARBELL,
    )


def _on(n: int, load: float, reps: int, left: int) -> Exposure:
    return Exposure(
        session_id=uuid.UUID(f"0199a1c3-0000-7000-8000-{n:012d}"),
        date=FIRST_DAY + dt.timedelta(days=3 * n),
        sets=[PerformedSet(load_kg=load, reps=reps, rir=left)],
    )


def _json(goal: LineGoal, history: Sequence[Exposure]) -> str:
    payload = SuggestionRead.model_validate(suggest(goal, history))
    return json.dumps(
        payload.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False
    )


HEADER = """// GENERATED FILE — do not hand-edit.
//
// Produced by `backend/scripts/emit_progression_fixture.py`
// (`just progression-fixture`) by running `app.domain.progression.suggest`
// over stored sessions and projecting the result through `SuggestionRead`, the
// model the packet serves. Every weight and computed value is the domain's own.
//
// Regenerate after changing app/domain/progression.py or the suggestion read
// shape, and commit the result.

import type { components } from "@/generated/api/schema";

type Suggestion = components["schemas"]["SuggestionRead"];

/**
 * Target 6 reps with 2 left; the last two sessions were 100 kg × 6 with 3
 * left → 102.5 kg (computed 102.6).
 */
export const EASIER_TWICE: Suggestion = """

CAPPED_HEADER = """;

/**
 * Target 8 reps with 2 left; 100 kg × 5 with 0 left → 90 kg (computed 87.5,
 * held by the 10 % limit).
 */
export const CAPPED_DOWN: Suggestion = """

FIRST_HEADER = """;

/** An exercise never logged: no weight, no rule. */
export const FIRST_TIME: Suggestion = """


def render() -> str:
    """The generated module's text."""
    return (
        HEADER
        + _json(_goal(6, 2), [_on(1, 100.0, 6, 3), _on(2, 100.0, 6, 3)])
        + CAPPED_HEADER
        + _json(_goal(8, 2), [_on(1, 100.0, 5, 0)])
        + FIRST_HEADER
        + _json(_goal(8, 2), [])
        + ";\n"
    )


def main() -> None:
    """Write the generated module."""
    DESTINATION.write_text(render(), encoding="utf-8")
    print(f"wrote {DESTINATION.relative_to(ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    main()

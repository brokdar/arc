"""Generate the frontend's personal-best and exercise-history fixtures from the domain.

A fixture must be a payload the real API could produce (the repo's testing
strategy, rule 3). Whether 100 kg × 8 is a new best depends on every earlier
set of the lift, and an estimated max is arithmetic over reps, reps left and a
three-session median — a hand-typed `estimate_kg: 125` beside sets that make
123.3 type-checks perfectly and agrees with nothing.

So the documents are generated: this script holds stored sessions, runs
`app.domain.strength_records.new_bests` and `exercise_records` over them, and
projects the results through **`app.api.schemas.strength_history`'s
`to_session_bests_read` and `to_history_read`** — the functions the endpoints
use — so the emitted JSON is a response the API serves.

* :data:`SUMMARY_SESSION` follows :data:`SUMMARY_PRIOR`: a Romanian deadlift
  set of 100 kg × 8 beats the earlier 100 kg × 7 (and 100 × 7 the same day
  does not repeat it), the warm-up counts for nothing, and the squats match
  last time — no best there. The deadlift is logged as free text, the way a
  recap or an import names a movement, which the summary prints as written.
* :data:`NO_BEST_SESSION` lifts less than before: an empty list.
* :data:`SQUAT_HISTORY` is five squat sessions, one of them don't-learn, so
  the line has a median and a hole; and the empty history of a lift never
  logged.

Run it with ``just strength-records-fixture`` after changing
`app/domain/strength_records.py` or the read shapes, and commit the result.
"""

import datetime as dt
import json
import sys
import uuid
from collections.abc import Mapping
from pathlib import Path

from app.api.schemas.strength_history import to_history_read, to_session_bests_read
from app.domain.activity import LoggedSetKind
from app.domain.metrics import PerformedSet
from app.domain.strength_records import Exposure, exercise_records, new_bests
from app.persistence.exercises import ExerciseRow
from app.services.strength_history import ExerciseHistory, SessionBest

ROOT = Path(__file__).resolve().parents[2]
DESTINATION = ROOT / "frontend" / "tests" / "mocks" / "generated-strength-records.ts"

FIRST_DAY = dt.date(2026, 9, 1)
RDL_NAME = "Romanian deadlift"


def _id(n: int) -> uuid.UUID:
    """A fixed session id, so the file is stable in git."""
    return uuid.UUID(f"0199a1b2-0000-7000-8000-{n:012d}")


def _on(n: int, *sets: PerformedSet, dont_learn: bool = False) -> Exposure:
    return Exposure(
        session_id=_id(n),
        date=FIRST_DAY + dt.timedelta(days=n),
        sets=sets,
        dont_learn=dont_learn,
    )


#: Earlier sessions, per exercise: (slug or None, name) → exposures.
SUMMARY_PRIOR: dict[tuple[str | None, str], list[Exposure]] = {
    (None, RDL_NAME): [_on(1, PerformedSet(load_kg=100.0, reps=7, rir=1))],
    ("back_squat", "Back Squat"): [_on(1, PerformedSet(load_kg=100.0, reps=5, rir=2))],
}

#: The session just finished, per exercise.
SUMMARY_SESSION: dict[tuple[str | None, str], list[PerformedSet]] = {
    (None, RDL_NAME): [
        PerformedSet(load_kg=60.0, reps=8, kind=LoggedSetKind.WARMUP),
        PerformedSet(load_kg=100.0, reps=7, rir=1),
        PerformedSet(load_kg=100.0, reps=8, rir=0),
    ],
    ("back_squat", "Back Squat"): [PerformedSet(load_kg=100.0, reps=5, rir=2)],
}

NO_BEST_SESSION: dict[tuple[str | None, str], list[PerformedSet]] = {
    (None, RDL_NAME): [PerformedSet(load_kg=100.0, reps=6, rir=1)]
}

SQUAT_HISTORY = [
    _on(0, PerformedSet(load_kg=95.0, reps=5, rir=2)),
    _on(3, PerformedSet(load_kg=100.0, reps=5, rir=2)),
    _on(7, PerformedSet(load_kg=110.0, reps=3, rir=2), dont_learn=True),
    _on(10, PerformedSet(load_kg=100.0, reps=8, rir=1)),
    _on(14, PerformedSet(load_kg=102.5, reps=5, rir=2)),
]

SQUAT = ExerciseRow(id="back_squat", name="Back Squat")


def _bests(session: Mapping[tuple[str | None, str], list[PerformedSet]]) -> str:
    found = [
        SessionBest(
            exercise_id=slug, exercise_name=name, load_kg=b.load_kg, reps=b.reps
        )
        for (slug, name), sets in session.items()
        for b in new_bests(SUMMARY_PRIOR.get((slug, name), []), _on(20, *sets))
    ]
    return _json(to_session_bests_read(found).model_dump(mode="json"))


def _history(exposures: list[Exposure]) -> str:
    history = ExerciseHistory(exercise=SQUAT, records=exercise_records(exposures))
    return _json(to_history_read(history).model_dump(mode="json"))


def _json(payload: object) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)


HEADER = """// GENERATED FILE — do not hand-edit.
//
// Produced by `backend/scripts/emit_strength_records_fixture.py`
// (`just strength-records-fixture`) by running `app.domain.strength_records`
// over stored sessions and projecting the result through the functions the
// endpoints use. Every best, rep max and estimate below is the domain's own.
//
// Regenerate after changing app/domain/strength_records.py or the read
// shapes, and commit the result.

import type { components } from "@/generated/api/schema";

type Schemas = components["schemas"];

/**
 * `GET /personal-bests` for a session whose Romanian deadlift (free text)
 * reached 100 kg × 8 after an earlier 100 kg × 7; its squats matched last
 * time and set no best.
 */
export const SESSION_BESTS: Schemas["SessionBestsRead"] = """

NONE_HEADER = """;

/** The same lift, weaker than before: no new best. */
export const SESSION_BESTS_NONE: Schemas["SessionBestsRead"] = """

HISTORY_HEADER = """;

/**
 * `GET /exercises/back_squat/history` over five sessions, the third
 * don't-learn: it holds a best but no estimate.
 */
export const SQUAT_HISTORY: Schemas["ExerciseHistoryRead"] = """

EMPTY_HEADER = """;

/** A lift never logged: empty lists and `not_enough_data`. */
export const SQUAT_HISTORY_EMPTY: Schemas["ExerciseHistoryRead"] = """


def render() -> str:
    """The generated module's text."""
    return (
        HEADER
        + _bests(SUMMARY_SESSION)
        + NONE_HEADER
        + _bests(NO_BEST_SESSION)
        + HISTORY_HEADER
        + _history(SQUAT_HISTORY)
        + EMPTY_HEADER
        + _history([])
        + ";\n"
    )


def main() -> None:
    """Write the generated module."""
    DESTINATION.write_text(render(), encoding="utf-8")
    print(f"wrote {DESTINATION.relative_to(ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    main()

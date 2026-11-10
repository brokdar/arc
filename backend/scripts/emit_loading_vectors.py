"""Write the loading vectors the phone's arithmetic is tested against.

The phone suggests loadable weights, plate breakdowns, starting weights and
step-downs with no signal, so `frontend/lib/strength` computes them itself.
The rules stay in `app.domain.loading`; the TypeScript is a twin, and this
script is what keeps it one. It runs the Python domain over a fixed list of
inputs and writes each input with the domain's answer to
`backend/tests/vectors/loading.json`, which both suites read:

* `tests/unit/test_loading_vectors.py` re-runs every case through the domain
  and checks the committed file is byte-for-byte what this script writes now
  — so changing the Python rounding without re-emitting fails CI;
* `frontend/lib/strength/loading.vectors.test.ts` runs every case through the
  twin — so re-emitting without changing the TypeScript fails CI too.

The inputs are chosen at the edges the acceptance criteria name: ties, the
empty bar, past the maximum, each equipment, refusals, and float noise such as
``0.1 + 0.2`` — which JSON carries as its shortest round-trip repr, so both
languages read back the same double.

Run it with ``just loading-vectors`` after changing `app/domain/loading.py`,
and commit the result.
"""

import json
from pathlib import Path
from typing import Any

from app.domain.loading import (
    DEFAULT_GYM,
    Equipment,
    LoadingError,
    kg_to_lb,
    lb_to_kg,
    loading_for,
    nearest_loadable,
    percent_of_set,
    plates_per_side,
    starting_step,
    step_down,
)

#: Where the vectors land.
DESTINATION = Path(__file__).parents[1] / "tests" / "vectors" / "loading.json"

_B, _D, _S = Equipment.BARBELL.value, Equipment.DUMBBELL.value, Equipment.STACK.value

#: ``(function, args)`` for every case, in the order written.
CASES: tuple[tuple[str, tuple[Any, ...]], ...] = (
    # Nearest loadable: the criteria's numbers, ties, floor, cap, noise.
    *(
        ("nearest_loadable", (target, _B))
        for target in (
            101.3,
            101.2,
            101.25,
            0.0,
            12.0,
            20.0,
            21.25,
            21.2500001,
            0.1 + 0.2,
            # Float noise one ulp past a tie: 101.25000000000001 rounds up.
            (0.1 + 0.2) * 337.5,
            62.5,
            374.9,
            375.0,
            500.0,
        )
    ),
    *(
        ("nearest_loadable", (target, _D))
        for target in (0.0, 0.5, 12.9, 13.0, 13.1, 49.0, 49.1, 80.0, 0.1 + 0.2)
    ),
    *(
        ("nearest_loadable", (target, _S))
        for target in (1.0, 37.4, 37.5, 37.6, 150.0, 151.0, 0.1 + 0.2)
    ),
    ("nearest_loadable", (-1.0, _B)),
    # The full answer, capped flag and plates included.
    *(
        ("loading_for", (target, equipment))
        for target, equipment in (
            (101.3, _B),
            (12.0, _B),
            (1000.0, _B),
            (13.1, _D),
            (1000.0, _D),
            (37.6, _S),
            (1000.0, _S),
        )
    ),
    # Plates per side.
    *(
        ("plates_per_side", (kg, _B))
        for kg in (20.0, 12.0, 22.5, 60.0, 70.0, 100.0, 102.5, 142.5, 375.0)
    ),
    ("plates_per_side", (14.0, _D)),
    ("plates_per_side", (40.0, _S)),
    # Starting weight from the first-set answer.
    *(
        ("starting_step", (kg, answer, _B))
        for kg, answer in (
            (20.0, "easy"),
            (40.0, "good"),
            (40.0, "hard"),
            (20.0, "hard"),
            (60.0, "easy"),
            (0.1 + 0.2, "good"),
            (40.0, "meh"),
        )
    ),
    ("starting_step", (2.0, "good", _D)),
    ("starting_step", (10.0, "easy", _D)),
    ("starting_step", (30.0, "hard", _S)),
    # Step-down mid-session.
    *(("step_down", (kg, _B)) for kg in (102.5, 20.0, 21.0, 375.0)),
    ("step_down", (14.0, _D)),
    ("step_down", (2.0, _D)),
    ("step_down", (40.0, _S)),
    # Percent of a set.
    *(
        ("percent_of_set", (kg, fraction, _B))
        for kg, fraction in (
            (102.5, 0.925),
            (100.0, 1.5),
            (100.0, 0.5),
            (60.0, 0.1 + 0.2),
            (337.5, 0.1 + 0.2),
            (337.5, 0.3),
            (102.5, 0.0),
            (102.5, 1.5000001),
        )
    ),
    ("percent_of_set", (30.0, 0.8, _D)),
    # Units.
    *(("kg_to_lb", (kg,)) for kg in (0.0, 1.0, 0.45359237, 20.0, 102.5, 0.1 + 0.2)),
    *(("lb_to_kg", (lb,)) for lb in (0.0, 1.0, 45.0, 225.0, 0.1 + 0.2)),
)


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    """The domain's answer to one case, as the vectors file states it.

    A refusal is ``{"error": true}`` rather than the message: the twin must
    refuse the same inputs, not word the refusal identically.
    """
    fn, args = case["fn"], case["args"]
    try:
        match fn:
            case "nearest_loadable":
                value: Any = nearest_loadable(args[0], Equipment(args[1]))
            case "loading_for":
                result = loading_for(args[0], Equipment(args[1]))
                value = {
                    "kg": result.kg,
                    "capped": result.capped,
                    "plates_per_side": (
                        None
                        if result.plates_per_side is None
                        else list(result.plates_per_side)
                    ),
                }
            case "plates_per_side":
                value = plates_per_side(args[0], Equipment(args[1]))
            case "starting_step":
                value = starting_step(args[0], args[1], Equipment(args[2]))
            case "step_down":
                value = step_down(args[0], Equipment(args[1]))
            case "percent_of_set":
                value = percent_of_set(args[0], args[1], Equipment(args[2]))
            case "kg_to_lb":
                value = kg_to_lb(args[0])
            case "lb_to_kg":
                value = lb_to_kg(args[0])
            case _:
                raise KeyError(f"no loading function {fn!r}")
    except LoadingError:
        return {"error": True}
    return {"value": value}


def build_vectors() -> dict[str, Any]:
    """The whole document: the gym every case ran against, and the cases."""
    cases = []
    for fn, args in CASES:
        case: dict[str, Any] = {"fn": fn, "args": list(args)}
        case["expect"] = run_case(case)
        cases.append(case)
    gym = DEFAULT_GYM
    return {
        "gym": {
            "bar_kg": gym.bar_kg,
            "plate_pairs": [list(pair) for pair in gym.plate_pairs],
            "dumbbell_step_kg": gym.dumbbell_step_kg,
            "dumbbell_max_kg": gym.dumbbell_max_kg,
            "stack_step_kg": gym.stack_step_kg,
            "stack_max_kg": gym.stack_max_kg,
        },
        "cases": cases,
    }


def render(document: dict[str, Any]) -> str:
    """The file's exact text, so the drift test can compare bytes."""
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def load_vectors(path: Path) -> dict[str, Any]:
    """Read a vectors file, refusing one with nothing in it.

    Every consumer loops over the cases, and a loop over none passes. An empty
    or truncated file is therefore an error here, not a green run.

    Raises:
        ValueError: If the file has no cases.
    """
    document = json.loads(path.read_text(encoding="utf-8"))
    if not document.get("cases"):
        raise ValueError(f"{path} has no cases; run `just loading-vectors`")
    return document


def main() -> None:
    """Write the vectors file."""
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    DESTINATION.write_text(render(build_vectors()), encoding="utf-8")
    print(f"wrote {DESTINATION.relative_to(Path(__file__).parents[2])}")


if __name__ == "__main__":
    main()

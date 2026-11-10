"""The shared loading vectors agree with the Python domain.

`backend/tests/vectors/loading.json` is what `frontend/lib/strength` is tested
against, so it is the bridge between the two languages. Three ways it could
lie, three tests: a case whose expected value the domain no longer produces
(someone changed rounding and did not re-emit), a committed file that differs
from what the emitter would write now (a case added to the script but not to
the file, or the reverse), and an empty file that every per-case loop would
pass without checking anything.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import emit_loading_vectors
from scripts.emit_loading_vectors import (
    DESTINATION,
    build_vectors,
    load_vectors,
    run_case,
)

VECTORS = load_vectors(DESTINATION)


def _case_id(case: dict[str, Any]) -> str:
    return f"{case['fn']}{case['args']}"


@pytest.mark.parametrize("case", VECTORS["cases"], ids=_case_id)
def test_every_vector_matches_the_domain(case: dict[str, Any]) -> None:
    assert run_case(case) == case["expect"]


def test_the_committed_file_is_what_the_emitter_writes_now() -> None:
    assert DESTINATION.read_text(encoding="utf-8") == emit_loading_vectors.render(
        build_vectors()
    ), "run `just loading-vectors` and commit the result"


def test_the_vectors_cover_the_edges_the_criteria_name() -> None:
    fns = {case["fn"] for case in VECTORS["cases"]}
    assert fns >= {
        "nearest_loadable",
        "loading_for",
        "plates_per_side",
        "starting_step",
        "step_down",
        "percent_of_set",
        "kg_to_lb",
        "lb_to_kg",
    }
    # The float-noise input travels through JSON unchanged.
    assert 0.1 + 0.2 in [case["args"][0] for case in VECTORS["cases"]]
    assert any("error" in case["expect"] for case in VECTORS["cases"])


def test_an_empty_vectors_file_fails_rather_than_passing_vacuously(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "loading.json"
    empty.write_text(json.dumps({"gym": {}, "cases": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="no cases"):
        load_vectors(empty)
    empty.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="no cases"):
        load_vectors(empty)


def test_a_case_with_a_changed_expectation_is_caught() -> None:
    case = dict(VECTORS["cases"][0])
    case["expect"] = {"value": "not what the domain says"}
    assert run_case(case) != case["expect"]

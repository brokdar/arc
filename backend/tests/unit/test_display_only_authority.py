"""No deterministic rule consumes a display-only metric.

`app.domain.authority` records which metrics only *describe* training. This
file is what makes that record bind: it walks every module that decides
something about the athlete's plan — scores a session, matches it, files or
accepts a proposal, gates the agent — and fails if one of them imports the
code that computes or serves a display-only number. A docstring asking nobody
to do it is the thing this displaces.

The walk is over each listed module's **source**, parsed rather than imported:
an import that only happens inside a function body is still a dependency, and
importing the module to inspect it would run whatever it imports.
"""

import ast
import datetime as dt
import shutil
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.core.clock import athlete_today
from app.domain.authority import (
    DETERMINISTIC_MODULES,
    DISPLAY_ONLY_METRICS,
    display_only_sources,
    is_display_only,
)

BACKEND = Path(__file__).resolve().parents[2]


def module_path(dotted: str) -> Path:
    """The source file of one ``app.*`` module, or a failure naming it.

    Raises:
        FileNotFoundError: When the module does not exist. A rename that left
            the list behind must fail here rather than shrink the walk.
    """
    base = BACKEND.joinpath(*dotted.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"{dotted} is listed as a deterministic module but has no source file — "
        "update app.domain.authority.DETERMINISTIC_MODULES"
    )


def imported_names(source: str) -> set[str]:
    """Every dotted name a module imports, at any depth of its body.

    ``from app.domain import pmc`` yields both ``app.domain`` and
    ``app.domain.pmc``, so importing the module object is caught the same as
    importing a function out of it.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def walk(paths: Iterable[Path]) -> list[Path]:
    """The files among ``paths`` that import a display-only source."""
    sources = display_only_sources()
    return [
        path
        for path in paths
        if imported_names(path.read_text(encoding="utf-8")) & sources
    ]


# --- the walk ----------------------------------------------------------------------


def test_the_deterministic_modules_import_no_display_only_metric() -> None:
    offenders = walk(module_path(name) for name in DETERMINISTIC_MODULES)

    assert offenders == [], (
        "a deterministic rule imports the code behind a display-only metric "
        f"(app.domain.authority): {[str(p.relative_to(BACKEND)) for p in offenders]}"
    )


def test_the_list_names_the_rule_modules() -> None:
    for name in (
        "app.services.scoring",
        "app.services.matching",
        "app.services.proposals",
        "app.services.guardrails",
    ):
        assert name in DETERMINISTIC_MODULES


def test_the_walk_reports_a_module_that_imports_the_pmc(tmp_path: Path) -> None:
    # A copy of the real scoring service with one line added — the walk must
    # name that file and only that file.
    scoring = module_path("app.services.scoring")
    tainted = tmp_path / "scoring.py"
    shutil.copy(scoring, tainted)
    tainted.write_text(
        tainted.read_text(encoding="utf-8") + "\nfrom app.domain.pmc import pmc\n",
        encoding="utf-8",
    )
    stub = [
        tainted,
        module_path("app.services.matching"),
        module_path("app.services.proposals"),
        module_path("app.services.guardrails"),
    ]

    assert walk(stub) == [tainted]
    # The untouched original, in the same list, passes.
    assert walk([scoring, *stub[1:]]) == []


@pytest.mark.parametrize(
    "line",
    [
        "from app.domain import pmc",
        "import app.domain.pmc",
        "from app.services.fitness import FitnessService",
        "def later():\n    from app.domain.pmc import fitness_trend\n",
    ],
)
def test_every_spelling_of_the_import_is_caught(tmp_path: Path, line: str) -> None:
    module = tmp_path / "rule.py"
    module.write_text(f'"""A rule."""\n{line}\n', encoding="utf-8")

    assert walk([module]) == [module]


def test_a_listed_module_that_no_longer_exists_fails() -> None:
    with pytest.raises(FileNotFoundError, match="app.services.scoring_v2"):
        list(walk(module_path(name) for name in ("app.services.scoring_v2",)))


def test_every_listed_module_exists() -> None:
    for name in DETERMINISTIC_MODULES:
        assert module_path(name).is_file()


def test_the_sources_exist() -> None:
    # A source that was renamed would make the walk look for an import nobody
    # can write, and pass forever.
    for name in display_only_sources():
        if name.count(".") >= 2:
            assert module_path(name).is_file()


# --- the registry and the stamp ------------------------------------------------------


def test_fitness_fatigue_and_form_are_display_only() -> None:
    assert {"ctl", "atl", "tsb"} <= set(DISPLAY_ONLY_METRICS)
    assert all(is_display_only(key) for key in ("ctl", "atl", "tsb"))
    assert not is_display_only("training_load")


def _ref_name(schema: dict[str, Any]) -> str | None:
    """The component a property schema points at, through `allOf`/`anyOf`."""
    if "$ref" in schema:
        return str(schema["$ref"]).rsplit("/", 1)[-1]
    for key in ("allOf", "anyOf", "oneOf"):
        for branch in schema.get(key, []):
            found = _ref_name(branch)
            if found is not None:
                return found
    return None


def test_every_contract_field_named_for_a_display_only_metric_is_stamped(
    app: FastAPI,
) -> None:
    components = app.openapi()["components"]["schemas"]
    found: list[tuple[str, str]] = []
    for owner, schema in components.items():
        for prop, prop_schema in schema.get("properties", {}).items():
            if not is_display_only(prop):
                continue
            found.append((owner, prop))
            target = _ref_name(prop_schema)
            assert target is not None, f"{owner}.{prop} is not a metric object"
            stamped = components[target]
            assert stamped["properties"]["display_only"].get("const") is True, target
            assert "display_only" in stamped.get("required", []), target

    assert {prop for _, prop in found} >= {"ctl", "atl", "tsb"}


def _keyed(document: Any) -> Iterator[tuple[str, Any]]:
    """Every (key, value) pair at any depth of a JSON document."""
    if isinstance(document, dict):
        for key, value in document.items():
            yield key, value
            yield from _keyed(value)
    elif isinstance(document, list):
        for value in document:
            yield from _keyed(value)


async def test_every_payload_field_named_for_one_carries_the_stamp(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/manual-sessions",
        json={
            "start_time": f"{(athlete_today() - dt.timedelta(days=3)).isoformat()}"
            "T08:00:00Z",
            "timezone": "UTC",
            "duration_s": 3_600,
            "discipline": "cycling",
        },
    )
    assert response.status_code == 201, response.text

    body = (await client.get("/api/v1/fitness")).json()

    stamped = [(key, value) for key, value in _keyed(body) if is_display_only(key)]
    assert stamped, "the fitness read carried no display-only field at all"
    # Every object keyed by one — the per-day value, its series' maturity,
    # its explanation — says so, so a reader holding any fragment of the
    # response can tell it is not a number to act on.
    for key, value in stamped:
        assert isinstance(value, dict), key
        assert value["display_only"] is True, key

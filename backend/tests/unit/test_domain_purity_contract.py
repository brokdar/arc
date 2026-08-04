"""Guard: the domain-purity deny-list covers every backend dependency.

The import-linter contract in `pyproject.toml` is a *forbidden* contract — it
only rejects the packages it names, so `import fastmcp` in `app/domain` passed
review, CI and pre-push while the list was hand-maintained. This test makes
the list self-maintaining: every distribution in `[project].dependencies` must
be either forbidden in the domain or on the small allowlist below, so adding a
dependency without deciding which fails the suite.
"""

import re
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path
from typing import Any

PYPROJECT = Path(__file__).parents[2] / "pyproject.toml"

#: Import names the domain MAY use, with the reason it is not a purity risk.
#: Keep this minimal — an entry here is a permanent hole in the contract.
DOMAIN_MAY_IMPORT = {
    # Plain data modelling: no I/O and no framework coupling. The domain uses
    # it for value objects; `pydantic_settings` (which reads the environment)
    # stays forbidden.
    "pydantic": "pure data modelling — validation and value objects, no I/O",
    # WP-5 moves metrics computation into the domain and adds polars/pyarrow
    # as dependencies; allowlist them here then (a dataframe library is
    # in-process computation, not I/O) — that is the expected edit when this
    # test first fails on them.
}


def _canonical(distribution_name: str) -> str:
    """Normalize a distribution name per PEP 503 (`Pydantic.Settings` -> `pydantic-settings`)."""
    return re.sub(r"[-_.]+", "-", distribution_name).lower()


def _declared_distributions(dependencies: list[str]) -> set[str]:
    """Canonical distribution names from PEP 508 requirement strings.

    Strips extras and version specifiers, so `fastapi[standard]>=0.141.1`
    becomes `fastapi`.
    """
    return {
        _canonical(re.split(r"[\s\[<>=!~;(]", requirement, maxsplit=1)[0])
        for requirement in dependencies
    }


def _import_names(distributions: set[str]) -> dict[str, set[str]]:
    """Map each canonical distribution name to the import names it provides.

    `packages_distributions()` maps the other way (import name -> providing
    distributions), so invert it. A distribution can provide several import
    names, and one import name can come from a distribution with a different
    name (`pydantic-settings` -> `pydantic_settings`).
    """
    provided_by: dict[str, set[str]] = {}
    for import_name, providers in packages_distributions().items():
        for provider in providers:
            provided_by.setdefault(_canonical(provider), set()).add(import_name)
    installed_import_names = set(packages_distributions())

    resolved: dict[str, set[str]] = {}
    for distribution in distributions:
        if names := provided_by.get(distribution):
            resolved[distribution] = names
            continue
        # Wrapper distributions ship only metadata: `fastmcp` installs no
        # modules of its own, `fastmcp-slim` provides the `fastmcp` package.
        # Fall back to the conventional name, but only if something really
        # installed it under that name.
        fallback = distribution.replace("-", "_")
        assert fallback in installed_import_names, (
            f"Cannot determine the import name of dependency {distribution!r}: "
            "it provides no top-level package and nothing is importable as "
            f"{fallback!r}. Is the environment out of sync (`uv sync`)? If the "
            "import name genuinely differs, teach this helper about it."
        )
        resolved[distribution] = {fallback}
    return resolved


def _domain_purity_contract(pyproject: dict[str, Any]) -> dict[str, Any]:
    """The single `forbidden` contract whose source is `app.domain`."""
    contracts = [
        contract
        for contract in pyproject["tool"]["importlinter"]["contracts"]
        if contract["type"] == "forbidden"
        and contract["source_modules"] == ["app.domain"]
    ]
    assert len(contracts) == 1, (
        f"Expected exactly one forbidden contract on app.domain, found "
        f"{len(contracts)}."
    )
    return contracts[0]


def test_every_dependency_is_forbidden_in_the_domain_or_allowlisted() -> None:
    pyproject = tomllib.loads(PYPROJECT.read_text())
    forbidden = set(_domain_purity_contract(pyproject)["forbidden_modules"])
    dependencies = _declared_distributions(pyproject["project"]["dependencies"])

    unclassified = {
        distribution: sorted(import_names)
        for distribution, import_names in _import_names(dependencies).items()
        if not import_names <= forbidden | set(DOMAIN_MAY_IMPORT)
    }

    assert not unclassified, (
        "New backend dependencies are neither forbidden in app/domain nor "
        f"allowlisted: {unclassified}. Add each import name to "
        "forbidden_modules in the 'Domain is pure' contract in pyproject.toml "
        "(the default — the domain has no business importing it), or, if the "
        "domain may legitimately use it, to DOMAIN_MAY_IMPORT in this test "
        "with the reason why."
    )


def test_the_allowlist_has_no_stale_or_contradictory_entries() -> None:
    pyproject = tomllib.loads(PYPROJECT.read_text())
    forbidden = set(_domain_purity_contract(pyproject)["forbidden_modules"])
    dependencies = _declared_distributions(pyproject["project"]["dependencies"])
    installed = {
        name for names in _import_names(dependencies).values() for name in names
    }

    assert not set(DOMAIN_MAY_IMPORT) & forbidden, (
        "Allowlisted and forbidden at the same time: "
        f"{sorted(set(DOMAIN_MAY_IMPORT) & forbidden)}. The contract wins — "
        "drop the DOMAIN_MAY_IMPORT entry."
    )
    assert set(DOMAIN_MAY_IMPORT) <= installed, (
        "DOMAIN_MAY_IMPORT names something that is no longer a backend "
        f"dependency: {sorted(set(DOMAIN_MAY_IMPORT) - installed)}. Remove it, "
        "so the allowlist stays a list of real decisions."
    )

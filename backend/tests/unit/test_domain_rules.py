"""The training-rule registry, and the walk that keeps constants inside it.

Two halves. The registry tests hold every entry to its contract — a rule id,
an evidence tag, a DOI when it claims to be backed, and no id twice. The walk
tests hold the *rest of the domain* to the registry: a governed module (one
that computes training numbers — progression, warm-ups, loading, safety) may
not carry a bare numeric literal beyond a tiny allow-list, so a cap or a
threshold cannot be typed in where it is used and drift from its evidence.
"""

import ast
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.domain import rules
from app.domain.rules import REGISTRY, Certainty, Evidence, Rule, build_registry

DOMAIN_DIR = Path(rules.__file__).parent

#: The modules whose numbers are training decisions. Matched by stem, so a
#: module that does not exist yet is covered the day it lands — the PRs that
#: add `progression.py`, `warmups.py`, `loading.py` and `safety.py` draw their
#: constants from `app.domain.rules` or fail here.
GOVERNED_MODULES = frozenset({"progression", "warmups", "loading", "safety"})

#: Literals that are arithmetic, not training choices: zero and one (identity,
#: empty, "the first"), as ints or floats. Compared by type and value, so
#: `0.075` or `0.5` is never mistaken for an allowed number.
ALLOWED_VALUES = frozenset({0, 1})

DOI_PATTERN = re.compile(r"10\.\d{4,}/")


@dataclass(frozen=True)
class BareLiteral:
    """One numeric literal a governed module carries instead of a rule."""

    path: Path
    line: int
    value: int | float | complex

    def __str__(self) -> str:
        return f"{self.path.name}:{self.line}: bare literal {self.value!r}"


def _index_literal_ids(tree: ast.AST) -> set[int]:
    """Ids of int constants used as a subscript index or slice bound."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        parts = [node.slice]
        if isinstance(node.slice, ast.Slice):
            parts = [node.slice.lower, node.slice.upper, node.slice.step]
        for part in parts:
            if isinstance(part, ast.UnaryOp) and isinstance(part.op, ast.USub):
                part = part.operand
            if (
                isinstance(part, ast.Constant)
                and type(part.value) is int
                and not isinstance(part.value, bool)
            ):
                found.add(id(part))
    return found


def bare_literals(path: Path) -> list[BareLiteral]:
    """Every numeric literal in `path` that the allow-list does not excuse."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    indices = _index_literal_ids(tree)
    out: list[BareLiteral] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or isinstance(node.value, bool):
            continue
        value = node.value
        if not isinstance(value, (int, float, complex)):
            continue
        if id(node) in indices:
            continue
        if type(value) in (int, float) and value in ALLOWED_VALUES:
            continue
        out.append(BareLiteral(path, node.lineno, value))
    return out


def walk_governed(domain_dir: Path) -> list[BareLiteral]:
    """Bare literals across every governed module under `domain_dir`."""
    found: list[BareLiteral] = []
    for path in sorted(domain_dir.rglob("*.py")):
        if path.stem in GOVERNED_MODULES:
            found.extend(bare_literals(path))
    return found


# --- the registry ---------------------------------------------------------


def test_registry_is_not_empty_and_keyed_by_rule_id() -> None:
    assert REGISTRY
    for rule_id, rule in REGISTRY.items():
        assert rule.id == rule_id


@pytest.mark.parametrize("rule", list(REGISTRY.values()), ids=lambda r: r.id)
def test_every_entry_has_id_evidence_and_doi_when_backed(rule: Rule[object]) -> None:
    assert re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z0-9_]+)+", rule.id)
    assert isinstance(rule.evidence, Evidence)
    assert isinstance(rule.certainty, Certainty)
    assert rule.summary.strip()
    if rule.evidence is Evidence.BACKED:
        assert rule.dois, f"{rule.id} is tagged backed but cites no DOI"
    for doi in rule.dois:
        assert DOI_PATTERN.match(doi), f"{rule.id}: {doi!r} is not a DOI"


def test_every_rule_in_the_module_is_registered() -> None:
    """A `Rule` defined in `rules.py` but left out of `REGISTRY` is invisible."""
    defined = {v.id for v in vars(rules).values() if isinstance(v, Rule)}
    assert defined == set(REGISTRY)


def test_the_plan_constants_carry_their_values() -> None:
    assert rules.PROGRESSION_MAX_INCREASE.value == 0.05
    assert rules.PROGRESSION_MAX_DECREASE.value == 0.10
    assert rules.SMALL_MUSCLE_MAX_STEPS.value == 1
    assert rules.LOAD_PER_REP.value == 0.025
    assert rules.HARD_SET_MAX_RIR.value == 3
    assert rules.E1RM_MAX_REPS.value == 10
    assert rules.PLATEAU_WINDOW_SHORT.value == 4
    assert rules.PLATEAU_WINDOW_LONG.value == 6
    assert rules.BACKOFF_DROP.value == 0.075
    assert rules.REST_MAIN_SECONDS.value == 180
    assert rules.REST_ACCESSORY_SECONDS.value == 120
    assert rules.REST_ISOLATION_SECONDS.value == 90
    assert rules.REST_SUPERSET_TRANSITION_SECONDS.value == 15
    assert rules.WARMUP_RAMP.value == ((0.4, 5), (0.6, 3), (0.8, 2))
    assert rules.WARMUP_BAR_REPS.value == 10
    assert rules.STARTING_STEP_EASY.value == 0.20
    assert rules.STARTING_STEP_GOOD.value == 0.10
    assert rules.MID_SESSION_MISS_REPS.value == 2


def test_a_duplicate_rule_id_fails() -> None:
    first = Rule(
        id="test.dup",
        value=1,
        summary="first",
        evidence=Evidence.DEFAULT,
        certainty=Certainty.LOW,
    )
    second = Rule(
        id="test.dup",
        value=2,
        summary="second",
        evidence=Evidence.PRACTICE,
        certainty=Certainty.LOW,
    )
    with pytest.raises(ValueError, match="test.dup"):
        build_registry(first, second)


def test_the_real_registry_has_no_duplicate_ids() -> None:
    every = [v for v in vars(rules).values() if isinstance(v, Rule)]
    assert len({r.id for r in every}) == len(every)


def test_a_backed_rule_without_a_doi_cannot_be_built() -> None:
    with pytest.raises(ValueError, match="DOI"):
        Rule(
            id="test.backed",
            value=0,
            summary="claims evidence",
            evidence=Evidence.BACKED,
            certainty=Certainty.HIGH,
        )


def test_a_malformed_doi_cannot_be_built() -> None:
    with pytest.raises(ValueError, match="DOI"):
        Rule(
            id="test.backed",
            value=0,
            summary="claims evidence",
            evidence=Evidence.BACKED,
            certainty=Certainty.HIGH,
            dois=("doi.org/abc",),
        )


@pytest.mark.parametrize("bad_id", ["", "nodots", "Upper.case", "trailing.", "a..b"])
def test_a_malformed_rule_id_cannot_be_built(bad_id: str) -> None:
    with pytest.raises(ValueError, match="rule id"):
        Rule(
            id=bad_id,
            value=0,
            summary="x",
            evidence=Evidence.DEFAULT,
            certainty=Certainty.LOW,
        )


# --- the walk -------------------------------------------------------------


def _fixture(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


def test_walk_fails_on_a_bare_005_in_a_governed_module(tmp_path: Path) -> None:
    _fixture(
        tmp_path,
        "progression.py",
        '"""Caps."""\n\n\ndef capped(load: float) -> float:\n    return load * (1 + 0.05)\n',
    )
    found = walk_governed(tmp_path)
    assert [(f.path.name, f.line, f.value) for f in found] == [
        ("progression.py", 5, 0.05)
    ]


def test_walk_ignores_ungoverned_modules(tmp_path: Path) -> None:
    _fixture(tmp_path, "zones.py", "THRESHOLD = 0.05\n")
    assert walk_governed(tmp_path) == []


def test_walk_finds_governed_modules_in_subpackages(tmp_path: Path) -> None:
    (tmp_path / "strength").mkdir()
    _fixture(tmp_path / "strength", "warmups.py", "RAMP = (0.4, 0.6)\n")
    assert [f.value for f in walk_governed(tmp_path)] == [0.4, 0.6]


def test_allow_list_does_not_hide_0075(tmp_path: Path) -> None:
    """0, 1 and indices pass; a training number next to them is still caught."""
    _fixture(
        tmp_path,
        "loading.py",
        "def backoff(sets: list[float]) -> float:\n"
        "    if not sets or len(sets) == 0:\n"
        "        return 0.0\n"
        "    top = sets[0] + sets[2] + sets[-1] + sum(sets[1:3]) * 1.0\n"
        "    return top * (1 - 0.075)\n",
    )
    found = walk_governed(tmp_path)
    assert [(f.line, f.value) for f in found] == [(5, 0.075)]


def test_allow_list_excuses_only_int_indices(tmp_path: Path) -> None:
    """A float in a subscript is a value lookup, not a list index."""
    _fixture(tmp_path, "safety.py", "LIMITS = {0.075: 'x'}\nx = LIMITS[0.075]\n")
    assert [f.value for f in walk_governed(tmp_path)] == [0.075, 0.075]


def test_allow_list_does_not_excuse_other_small_ints(tmp_path: Path) -> None:
    _fixture(tmp_path, "safety.py", "MISS = 2\nok = True\n")
    assert [f.value for f in walk_governed(tmp_path)] == [2]


def test_docstrings_and_strings_are_not_literals(tmp_path: Path) -> None:
    _fixture(
        tmp_path,
        "progression.py",
        '"""Up to 5 % (0.05) per session."""\nLABEL = "0.05"\n',
    )
    assert walk_governed(tmp_path) == []


def test_walk_passes_on_the_real_tree() -> None:
    found = walk_governed(DOMAIN_DIR)
    assert found == [], "\n".join(str(f) for f in found)


def test_rules_module_itself_is_not_governed() -> None:
    """The registry is where literals belong; scanning it would be circular."""
    assert "rules" not in GOVERNED_MODULES

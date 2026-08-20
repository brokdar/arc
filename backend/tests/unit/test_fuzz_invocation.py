"""Guard: `just fuzz` and the CI fuzz job run the same fuzzer the same way.

A local run that excludes different checks, or samples a different number of
examples, cannot tell you whether CI will pass — and the two invocations live
in different files with no shared definition.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "api-fuzz.yml"
SCRIPT = REPO_ROOT / "scripts" / "run-fuzz.sh"

#: Flags whose value decides what the run actually covers.
SHARED_FLAGS = ("--max-examples", "--workers", "--exclude-path", "--exclude-checks")


def _flag_values(path: Path) -> dict[str, str]:
    """The flags as *invoked*. Comment lines are dropped: both files discuss
    these flags in prose, and prose is not what runs."""
    commands = "\n".join(
        line
        for line in path.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )
    return {
        flag: match.group(1)
        for flag in SHARED_FLAGS
        if (match := re.search(rf"{re.escape(flag)}\s+(\S+)", commands))
    }


def test_the_local_run_and_ci_agree_on_what_is_fuzzed() -> None:
    workflow = _flag_values(WORKFLOW)
    script = _flag_values(SCRIPT)

    assert set(workflow) == set(SHARED_FLAGS), f"workflow is missing {workflow}"
    assert workflow == script


def test_both_run_the_locked_schemathesis() -> None:
    """`uvx` would resolve a tree this repo neither pins nor updates."""
    for path in (WORKFLOW, SCRIPT):
        text = path.read_text()
        assert "uv run schemathesis run" in text
        assert "uvx" not in text

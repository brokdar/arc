"""Guard: the pre-commit hook and the CI job audit workflows with one zizmor.

The version is not in either invocation — both take it from
`tools/requirements.txt` — so the two lines have to stay identical for a local
pass and a CI pass to mean the same thing. Drop the `--constraints` from one
side and `uvx` resolves whatever is newest that morning: the hook goes green on
a different set of audits than the job that gates the merge, which is the
failure mode a pinned tool exists to prevent. Nothing enforced that but a
comment.
"""

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "zizmor.yml"
HOOK_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"

#: The single place either side is allowed to learn zizmor's version from.
CONSTRAINTS = "tools/requirements.txt"


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text())


def _invocation(command: str) -> str:
    """The `uvx …` command itself, normalised for whitespace.

    Parsed out of YAML rather than matched in the raw file so a reflow, a block
    scalar or a changed quote style cannot fail this — only a change to what
    runs. The hook wraps its command in `run-if-changed.sh <pattern> --`, and
    the wrapper is a hook concern the workflow has no equivalent of, so both
    sides are cut back to the `uvx` that does the work.
    """
    return " ".join(command[command.index("uvx") :].split())


def _workflow_invocation() -> str:
    return _invocation(
        next(
            step["run"]
            for job in _load(WORKFLOW)["jobs"].values()
            for step in job["steps"]
            if "zizmor" in step.get("run", "")
        )
    )


def _hook_invocation() -> str:
    return _invocation(
        next(
            hook["entry"]
            for repo in _load(HOOK_CONFIG)["repos"]
            for hook in repo["hooks"]
            if hook["id"] == "zizmor"
        )
    )


def test_the_hook_and_ci_run_zizmor_the_same_way() -> None:
    assert _hook_invocation() == _workflow_invocation()


def test_both_take_the_version_from_the_constraints_file() -> None:
    """A version named in a `run:` block is a pin nothing raises."""
    for invocation in (_hook_invocation(), _workflow_invocation()):
        assert f"--constraints {CONSTRAINTS}" in invocation
        assert "zizmor==" not in invocation

"""Guard: `mise.toml` pins just and prek to the versions Dependabot raises.

Dependabot's `pip` entry watches `tools/requirements.txt` and knows nothing of
`mise.toml`, so the two pins in mise.toml are copies that rot unless something
fails when they diverge. Both files must name one version of each tool.
"""

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).parents[3]
SHARED = {"just": "rust-just", "prek": "prek"}


def _requirement_pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in (REPO_ROOT / "tools" / "requirements.txt").read_text().splitlines():
        spec = line.split("#")[0].strip()
        if "==" in spec:
            name, version = spec.split("==")
            pins[name.strip()] = version.strip()
    return pins


def test_mise_pins_match_tools_requirements() -> None:
    mise = tomllib.loads((REPO_ROOT / "mise.toml").read_text())["tools"]
    pins = _requirement_pins()
    for mise_name, pip_name in SHARED.items():
        assert mise[mise_name] == pins[pip_name], (
            f"mise.toml pins {mise_name} {mise[mise_name]}, "
            f"tools/requirements.txt pins {pip_name} {pins[pip_name]}"
        )

"""Guard: every backend setting must be documented in the root .env.example.

When you add a field to the settings model, add it (commented with its
default is fine) to .env.example so deployments can discover it.
"""

import re
from pathlib import Path

from app.core.config import settings_env_keys

ENV_EXAMPLE = Path(__file__).parents[3] / ".env.example"


def test_env_example_documents_all_settings() -> None:
    content = ENV_EXAMPLE.read_text()
    documented = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", content, re.MULTILINE))

    expected = settings_env_keys()
    missing = expected - documented

    assert not missing, (
        f"Settings missing from .env.example: {sorted(missing)}. "
        "Document them (commented-out with defaults is fine)."
    )

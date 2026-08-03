"""Guard: every backend setting must be documented in the root .env.example.

When you add a field to the settings model, add it (commented with its
default is fine) to .env.example so deployments can discover it.
"""

import re
from pathlib import Path

from pydantic import BaseModel

from app.core.config import Settings

ENV_EXAMPLE = Path(__file__).parents[3] / ".env.example"


def _env_keys_for_model(model: type[BaseModel], prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for name, field in model.model_fields.items():
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            keys |= _env_keys_for_model(annotation, f"{prefix}{name.upper()}__")
        else:
            keys.add(f"{prefix}{name.upper()}")
    return keys


def test_env_example_documents_all_settings() -> None:
    content = ENV_EXAMPLE.read_text()
    documented = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", content, re.MULTILINE))

    expected = _env_keys_for_model(Settings)
    missing = expected - documented

    assert not missing, (
        f"Settings missing from .env.example: {sorted(missing)}. "
        "Document them (commented-out with defaults is fine)."
    )

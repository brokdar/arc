"""Loading the bundled purpose templates and exercise catalogue.

Both are **data in the repository**, not code (build plan WP-2.4 for the
templates, WP-2.2 for the catalogue), and both are validated against the
domain the moment they are read. This module is the only place that touches
those files; `app.domain` stays free of I/O, so the parsing lives there and
the reading lives here.

Failure is loud and early. `app.main`'s lifespan calls
:func:`load_purpose_templates` at startup, so a template file that names an
unknown scoring axis, omits a purpose, or holds a criterion the domain
rejects stops the application from booting rather than surfacing weeks later
as a session that cannot be scored. Both loaders are cached: the files cannot
change without a redeploy, and re-reading them per request would turn a
constant into IO.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.domain.strength import Exercise, parse_catalogue
from app.domain.templates import PurposeTemplate, parse_templates

#: Where the bundled data files live, beside the application package.
RESOURCE_ROOT = Path(__file__).parents[1] / "resources"

#: Per-purpose default criteria and applicable scoring axes.
PURPOSE_TEMPLATES_FILE = RESOURCE_ROOT / "purpose_templates.json"

#: The hand-curated exercise catalogue the `exercises` table is seeded from.
EXERCISE_CATALOGUE_FILE = RESOURCE_ROOT / "exercise_catalogue.json"


class ResourceError(RuntimeError):
    """A bundled data file is missing or malformed.

    Not an `AppError`: this is a deployment fault, not a request fault. There
    is no status code that would help a client, and the only correct response
    is for the process to fail to start.
    """


def _read(path: Path) -> Any:
    """Read and decode one bundled JSON file.

    Raises:
        ResourceError: When the file is missing or is not valid JSON.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ResourceError(f"cannot read bundled resource {path}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ResourceError(f"{path} is not valid JSON: {exc}") from exc


@lru_cache
def load_purpose_templates() -> dict[str, PurposeTemplate]:
    """Return the purpose templates, keyed by purpose value.

    Keyed by the enum's *value* rather than the member so the result is a
    plain, hashable-key mapping that the API layer can serialize directly;
    :func:`purpose_templates` gives the enum-keyed view the domain wants.

    Raises:
        ResourceError: When the file is missing, malformed, or describes a
            template the domain rejects.
    """
    try:
        templates = parse_templates(_read(PURPOSE_TEMPLATES_FILE))
    except ValueError as exc:
        raise ResourceError(f"{PURPOSE_TEMPLATES_FILE}: {exc}") from exc
    return {purpose.value: template for purpose, template in templates.items()}


def purpose_templates() -> dict[Any, PurposeTemplate]:
    """Return the purpose templates keyed by :class:`~app.domain.purpose.Purpose`."""
    return {
        template.purpose: template for template in load_purpose_templates().values()
    }


@lru_cache
def load_exercise_catalogue() -> tuple[Exercise, ...]:
    """Return the bundled exercise catalogue, in file order.

    Raises:
        ResourceError: When the file is missing, malformed, or holds a
            duplicate slug.
    """
    try:
        return parse_catalogue(_read(EXERCISE_CATALOGUE_FILE))
    except ValueError as exc:
        raise ResourceError(f"{EXERCISE_CATALOGUE_FILE}: {exc}") from exc


def verify_bundled_resources() -> None:
    """Load every bundled data file, failing loudly if any is unusable.

    Called from the application lifespan. Nothing is returned: the point is
    the exception, raised while there is still a person watching the deploy.
    """
    load_purpose_templates()
    load_exercise_catalogue()

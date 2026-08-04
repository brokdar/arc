"""Persistence layer: ORM models, repositories and the Alembic migrations.

A model only exists as far as SQLAlchemy is concerned once its module has been
imported. Two consumers need *all* of them and neither has a reason to import
any particular one: Alembic's autogenerate (a model whose module is not
imported produces an empty revision, silently) and the unit-test
`Base.metadata.create_all` (which otherwise only sees what `app.main`'s import
graph happens to reach — `no such table` at the first query). Both call
:func:`load_models` instead of maintaining a hand-written import list.
"""

import importlib
import pkgutil
from collections.abc import Iterable, Sequence

#: Sub-packages the sweep must never import. `alembic` holds `env.py`, which
#: runs migrations as a side effect of being imported.
_NOT_MODELS = frozenset({"alembic"})


def _submodules(search_path: Sequence[str], prefix: str) -> Iterable[str]:
    """Yield the dotted name of every module below ``search_path``, recursively.

    Written on `iter_modules` rather than `walk_packages` because the latter
    imports a sub-package in order to recurse into it — the skip list would
    then come too late for `alembic`.
    """
    for info in pkgutil.iter_modules(search_path):
        if info.name in _NOT_MODELS:
            continue
        name = f"{prefix}.{info.name}"
        yield name
        if info.ispkg:
            yield from _submodules(importlib.import_module(name).__path__, name)


def load_models() -> None:
    """Import every module in this package so its tables register on ``Base``.

    Idempotent — already-imported modules come straight from ``sys.modules``.
    """
    for name in _submodules(__path__, __name__):
        importlib.import_module(name)

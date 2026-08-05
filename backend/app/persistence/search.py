"""Case-insensitive substring search, spelled the same way everywhere.

The build plan says ILIKE is fine for the MVP's search (WP-2.6), and it is —
one athlete, a few hundred rows. What is *not* fine is passing the athlete's
search term into a LIKE pattern unescaped: ``%`` then matches everything and
``_`` matches any single character, so the search silently does something
other than what was typed.

`ilike` on SQLite renders as ``lower(a) LIKE lower(b)``, so the same
expression works in the unit suite and on Postgres.
"""

from typing import Any

from sqlalchemy import ColumnElement
from sqlalchemy.orm import InstrumentedAttribute

#: The character `contains` escapes wildcards with. Backslash is not special
#: in a SQL string literal on either dialect, so it needs no doubling here.
LIKE_ESCAPE = "\\"


def escape_like(term: str) -> str:
    """Neutralize LIKE wildcards in a user-supplied search term."""
    escaped = term.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
    return escaped.replace("%", f"{LIKE_ESCAPE}%").replace("_", f"{LIKE_ESCAPE}_")


def contains(column: InstrumentedAttribute[Any], term: str) -> ColumnElement[bool]:
    """Return a criterion matching rows whose ``column`` contains ``term``.

    A NULL column never matches, which is what a nullable description should
    do — SQL's three-valued logic gets this right without help.
    """
    return column.ilike(f"%{escape_like(term)}%", escape=LIKE_ESCAPE)

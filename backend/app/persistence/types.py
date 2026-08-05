"""Column-type conventions shared by every ORM model.

Unit tests run on SQLite and production runs on Postgres, so a column type is
only useful here if it behaves *identically* on both. The two places that
silently diverge are timestamps and JSON:

* ``DateTime(timezone=True)`` round-trips an aware datetime on Postgres and a
  **naive** one on SQLite (SQLite has no timestamptz — the offset is dropped on
  write and nothing is reattached on read). Code that then does
  ``value - datetime.now(UTC)`` works in production and raises in the unit
  suite, or the other way around. :class:`UtcDateTime` normalizes both ends.
* ``JSON`` is fine on SQLite but should be ``JSONB`` on Postgres (indexable,
  no reparse per read). :data:`JSONColumn` is the one spelling that gives each
  dialect its best type from a single model definition.

Enums get :func:`enum_column`: stored as a plain ``VARCHAR`` + ``CHECK``, never
a native Postgres ``ENUM`` type — adding a member to a native enum needs
``ALTER TYPE`` (non-transactional before PG 12, still a migration hazard) and
SQLite has no equivalent at all, so the check constraint is the portable
option. Renaming a member stays a migration either way.
"""

import datetime as dt
from enum import Enum

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeDecorator

#: JSON document column: portable ``JSON``, ``JSONB`` on Postgres.
#:
#: Use as ``mapped_column(JSONColumn)``. It is a type *instance* (variants can
#: only be attached to one), so it is shared between models — do not mutate it.
JSONColumn = sa.JSON().with_variant(postgresql.JSONB, "postgresql")


class UtcDateTime(TypeDecorator[dt.datetime]):
    """Timestamp column that is timezone-aware UTC on every dialect.

    Binds are converted to UTC (a naive value is rejected rather than guessed
    at), and results always come back with ``tzinfo=UTC``: converted on
    Postgres, attached on SQLite, where the stored text carries no offset.

    Attaching UTC on read is correct only because writes go through here and
    server defaults are UTC: ``now()`` on Postgres is aware, and SQLite's
    ``CURRENT_TIMESTAMP`` is documented as UTC.
    """

    impl = sa.DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self, value: dt.datetime | None, dialect: Dialect
    ) -> dt.datetime | None:
        """Normalize an inbound value to aware UTC."""
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                "UtcDateTime received a naive datetime; timestamps must carry a "
                "timezone (use datetime.now(datetime.UTC), not utcnow())"
            )
        return value.astimezone(dt.UTC)

    def process_result_value(
        self, value: dt.datetime | None, dialect: Dialect
    ) -> dt.datetime | None:
        """Return an aware-UTC value regardless of what the driver produced."""
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.UTC)
        return value.astimezone(dt.UTC)


def enum_column[E: Enum](enum_class: type[E]) -> sa.Enum:
    """Return the column type for storing ``enum_class``.

    Non-native by convention: a ``VARCHAR`` plus a ``CHECK`` constraint on the
    member names, which behaves the same on SQLite and Postgres and makes
    adding a member an ordinary column-constraint migration.
    """
    return sa.Enum(enum_class, native_enum=False, validate_strings=True)

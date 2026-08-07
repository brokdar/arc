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

    Non-native by convention: a plain ``VARCHAR(n)`` on both SQLite and
    Postgres, where ``n`` is the length of the **longest member value**. There
    is no ``CHECK`` constraint — SQLAlchemy's ``create_constraint`` defaults to
    ``False`` and this codebase leaves it there (D81), so the vocabulary is
    enforced by ``validate_strings=True`` on the way in, not by the database.
    A row written by hand-rolled SQL can therefore hold a value the enum does
    not have; the ORM refuses to read it back.

    Two consequences worth knowing before adding a member:

    * a member whose value is **longer** than every existing one widens the
      column, so it needs an ``ALTER COLUMN ... TYPE`` migration — inside
      ``batch_alter_table``, because SQLite cannot alter a column in place.
      A shorter or equal-length member needs no migration at all;
    * nothing in the database will stop a mis-spelled value from being stored
      by something that is not this ORM.

    What is stored is the member's **value**, not its name
    (``values_callable``). SQLAlchemy defaults to the name, which for the
    ``StrEnum`` members this codebase uses would put ``MAX_HR`` in the database
    while the API, the OpenAPI schema and every JSON payload say ``max_hr`` —
    two spellings of one vocabulary, and hand-written SQL would have to know
    which side of the ORM it is on.
    """
    return sa.Enum(
        enum_class,
        native_enum=False,
        validate_strings=True,
        values_callable=lambda members: [str(member.value) for member in members],
    )

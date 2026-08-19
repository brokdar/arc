"""Migration-chain guards, and what `0017` does to rows that already exist.

These catch the two classic failure modes:
- model changed but no migration written (drift)
- migration written but downgrade path broken (irreversible chain)

`0017` adds a third thing worth pinning: it **backfills**. Everything below the
chain guards runs the migration against a database seeded at `0015` — the
revision before integrations existed — because a backfill is only correct
against data it did not create, and a unit test on an empty SQLite file cannot
tell a backfill that classified nothing from one that classified everything.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.core.config import get_settings

#: The revision under test, and the one it is applied on top of.
INTEGRATIONS = "0017"
BEFORE_INTEGRATIONS = "0015"


def test_no_model_migration_drift(alembic_config: Config) -> None:
    """`alembic check` fails if autogenerate would produce a new revision.

    Also AC-7: the inert `oauth_authorizations.state` / `redirect_uri` columns
    are on the model, so a `0017` that forgot to add them fails right here.
    """
    command.check(alembic_config)


def test_migration_chain_roundtrips(alembic_config: Config) -> None:
    """head -> base -> head must work; broken downgrades block rollbacks."""
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")


# --- helpers ------------------------------------------------------------------


async def _with_engine[T](
    body: Callable[[AsyncConnection], Awaitable[T]], *, write: bool
) -> T:
    """Run one unit of work on a throwaway engine and leave nothing behind.

    `NullPool` and the yield after `dispose()`: asyncpg closes its transports
    on the loop, and `asyncio.run` tears the loop down the moment this returns
    — a socket still closing at that point surfaces as an unraisable
    `ResourceWarning` charged to whichever test happens to run next.
    """
    engine = create_async_engine(get_settings().postgres.async_url, poolclass=NullPool)
    try:
        opener = engine.begin() if write else engine.connect()
        async with opener as conn:
            return await body(conn)
    finally:
        await engine.dispose()
        await asyncio.sleep(0)


def run_sql(statements: Sequence[tuple[str, dict[str, Any]]]) -> None:
    """Execute statements against the test database, committing.

    `asyncio.run` in a synchronous test: `command.upgrade` runs its own loop
    (`alembic/env.py` ends in `asyncio.run`), so this module cannot be async
    without nesting one loop inside another — and only asyncpg is installed,
    so there is no synchronous driver to reach for.
    """

    async def _body(conn: AsyncConnection) -> None:
        for sql, params in statements:
            await conn.execute(text(sql), params)

    asyncio.run(_with_engine(_body, write=True))


def rows(sql: str, **params: Any) -> list[tuple[Any, ...]]:
    """Read the database back as plain tuples."""

    async def _body(conn: AsyncConnection) -> list[tuple[Any, ...]]:
        result = await conn.execute(text(sql), params)
        return [tuple(row) for row in result]

    return asyncio.run(_with_engine(_body, write=False))


def seed_connection(connection_id: uuid.UUID, provider: str = "dropbox") -> None:
    """One `connections` row as `0015` shaped it — credential bytes and all."""
    run_sql(
        [
            (
                (
                    "INSERT INTO connections (id, provider, status, scopes, "
                    "credentials, created_at, updated_at) VALUES "
                    "(:id, :provider, 'connected', '[]'::jsonb, "
                    "'\\x00'::bytea, now(), now())"
                ),
                {"id": connection_id, "provider": provider},
            )
        ]
    )


def seed_feed(feed_id: uuid.UUID, connection_id: uuid.UUID, remote_path: str) -> None:
    """One `feeds` row as `0015` shaped it — no `integration_id` column yet."""
    run_sql(
        [
            (
                (
                    "INSERT INTO feeds (id, connection_id, remote_path, enabled, "
                    "cursor_attempts, created_at) VALUES "
                    "(:id, :connection_id, :remote_path, true, 0, now())"
                ),
                {
                    "id": feed_id,
                    "connection_id": connection_id,
                    "remote_path": remote_path,
                },
            )
        ]
    )


def at_revision(alembic_config: Config, revision: str) -> None:
    """Move the database to one revision, from wherever it is."""
    command.downgrade(alembic_config, revision)


# --- AC-6: the backfill --------------------------------------------------------


def test_a_catalogue_folder_is_classified_and_nothing_else_is_guessed(
    alembic_config: Config,
) -> None:
    at_revision(alembic_config, BEFORE_INTEGRATIONS)
    connection_id = uuid.uuid7()
    wahoo_feed, other_feed = uuid.uuid7(), uuid.uuid7()
    seed_connection(connection_id)
    seed_feed(wahoo_feed, connection_id, "/apps/wahoofitness")
    seed_feed(other_feed, connection_id, "/photos")

    command.upgrade(alembic_config, "head")

    assert rows(
        "SELECT i.kind FROM feeds f JOIN integrations i "
        "ON i.id = f.integration_id WHERE f.id = :id",
        id=wahoo_feed,
    ) == [("wahoo",)]
    # No guess: `/photos` holds activity files for all arc knows, and naming
    # it Wahoo would put a folder under a source the athlete never chose.
    assert rows("SELECT integration_id FROM feeds WHERE id = :id", id=other_feed) == [
        (None,)
    ]
    # Nothing was deleted and no path was rewritten.
    assert rows("SELECT count(*) FROM feeds") == [(2,)]
    assert sorted(rows("SELECT remote_path FROM feeds")) == [
        ("/apps/wahoofitness",),
        ("/photos",),
    ]
    assert rows("SELECT count(*) FROM integrations") == [(1,)]


def test_the_integration_id_column_is_nullable(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")

    assert rows(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = 'feeds' AND column_name = 'integration_id'"
    ) == [("YES",)]


def test_a_database_with_no_feeds_gets_no_integrations(
    alembic_config: Config,
) -> None:
    at_revision(alembic_config, BEFORE_INTEGRATIONS)
    seed_connection(uuid.uuid7())

    command.upgrade(alembic_config, "head")

    assert rows("SELECT count(*) FROM integrations") == [(0,)]


def test_one_catalogue_folder_on_two_connections_is_one_integration(
    alembic_config: Config,
) -> None:
    at_revision(alembic_config, BEFORE_INTEGRATIONS)
    first, second = uuid.uuid7(), uuid.uuid7()
    seed_connection(first, provider="dropbox")
    # A second storage provider: `connections` is unique on `provider`, so two
    # accounts mean two providers. The column is a plain VARCHAR with no CHECK
    # (`enum_column`), which is what lets this stand in for one — but it is
    # sized to the longest **member value**, seven characters today, so the
    # placeholder has to fit. Adding a longer member is an `ALTER COLUMN`
    # migration, exactly as the `enum_column` docstring warns.
    seed_connection(second, provider="gdrive")
    seed_feed(uuid.uuid7(), first, "/apps/wahoofitness")
    seed_feed(uuid.uuid7(), second, "/apps/wahoofitness")

    command.upgrade(alembic_config, "head")

    assert rows("SELECT count(*) FROM integrations WHERE kind = 'wahoo'") == [(1,)]
    assert rows(
        "SELECT count(*) FROM feeds f JOIN integrations i "
        "ON i.id = f.integration_id WHERE i.kind = 'wahoo'"
    ) == [(2,)]


def test_no_local_drop_row_is_ever_created(alembic_config: Config) -> None:
    at_revision(alembic_config, BEFORE_INTEGRATIONS)
    connection_id = uuid.uuid7()
    seed_connection(connection_id)
    seed_feed(uuid.uuid7(), connection_id, "/apps/wahoofitness")

    command.upgrade(alembic_config, "head")

    # Locked decision: the local drop is synthesized by the service, never
    # stored. A row would be one the athlete could delete and never get back.
    assert rows("SELECT count(*) FROM integrations WHERE kind = 'local_drop'") == [(0,)]


def test_replaying_the_migration_does_not_duplicate_an_integration(
    alembic_config: Config,
) -> None:
    at_revision(alembic_config, BEFORE_INTEGRATIONS)
    connection_id = uuid.uuid7()
    seed_connection(connection_id)
    seed_feed(uuid.uuid7(), connection_id, "/apps/wahoofitness")
    command.upgrade(alembic_config, "head")
    assert rows("SELECT count(*) FROM integrations") == [(1,)]

    # Down to before the revision and up again: the feed is still there and
    # still at a catalogue path, so the backfill runs a second time over it.
    at_revision(alembic_config, BEFORE_INTEGRATIONS)
    command.upgrade(alembic_config, "head")

    assert rows("SELECT count(*) FROM integrations") == [(1,)]
    assert rows("SELECT count(*) FROM feeds WHERE integration_id IS NOT NULL") == [(1,)]


def test_downgrade_drops_what_it_added_and_keeps_every_feed(
    alembic_config: Config,
) -> None:
    at_revision(alembic_config, BEFORE_INTEGRATIONS)
    connection_id = uuid.uuid7()
    seed_connection(connection_id)
    seed_feed(uuid.uuid7(), connection_id, "/apps/wahoofitness")
    seed_feed(uuid.uuid7(), connection_id, "/photos")
    command.upgrade(alembic_config, "head")

    at_revision(alembic_config, BEFORE_INTEGRATIONS)

    assert rows("SELECT count(*) FROM feeds") == [(2,)]
    assert sorted(rows("SELECT remote_path FROM feeds")) == [
        ("/apps/wahoofitness",),
        ("/photos",),
    ]
    gone = rows(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name IN ('feeds', 'oauth_authorizations') "
        "AND column_name IN ('integration_id', 'state', 'redirect_uri')"
    )
    assert gone == []
    assert rows("SELECT to_regclass('public.integrations')") == [(None,)]

    command.upgrade(alembic_config, "head")


# --- AC-7: the columns PR-6 will read ------------------------------------------


def test_the_inert_oauth_columns_exist_and_are_nullable(
    alembic_config: Config,
) -> None:
    command.upgrade(alembic_config, "head")

    assert sorted(
        rows(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'oauth_authorizations' "
            "AND column_name IN ('state', 'redirect_uri')"
        )
    ) == [("redirect_uri", "YES"), ("state", "YES")]


def test_an_existing_authorization_survives_with_both_columns_null(
    alembic_config: Config,
) -> None:
    at_revision(alembic_config, BEFORE_INTEGRATIONS)
    authorization_id = uuid.uuid7()
    run_sql(
        [
            (
                (
                    "INSERT INTO oauth_authorizations "
                    "(id, provider, code_verifier, created_at, expires_at) "
                    "VALUES (:id, 'dropbox', 'a-verifier', now(), "
                    "now() + interval '15 min')"
                ),
                {"id": authorization_id},
            )
        ]
    )

    command.upgrade(alembic_config, "head")

    assert rows(
        "SELECT code_verifier, state, redirect_uri FROM oauth_authorizations "
        "WHERE id = :id",
        id=authorization_id,
    ) == [("a-verifier", None, None)]

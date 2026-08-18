"""Migration-chain guards.

These catch the two classic failure modes:
- model changed but no migration written (drift)
- migration written but downgrade path broken (irreversible chain)

The `0016`-specific tests below are here rather than in the unit suite for the
reason every schema test is: SQLite would accept an `ALTER TABLE` this project
never runs, and the question — does the chain leave Postgres holding exactly
the columns the models declare, in both directions — is only answerable on the
database the deployment uses.

Every test here is **synchronous**. Alembic's `env.py` drives the async engine
with `asyncio.run`, which cannot be called from inside a running loop, so a
test that awaited would not be able to migrate at all; the database reads
below open their own short-lived loop with :func:`_run`.
"""

import asyncio
import datetime as dt
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

#: The two columns `0016` ships **inert** for the redirect PR that follows.
INERT_AUTHORIZATION_COLUMNS = frozenset({"state", "redirect_uri"})


def _run[T](work: Callable[[AsyncConnection], Awaitable[T]]) -> T:
    """Run one piece of database work on its own connection and loop.

    A fresh connection per call, deliberately: a connection held across an
    `alembic upgrade` would be reading inside a transaction snapshot taken
    before the DDL, and the assertions would describe the schema as it was.
    """

    async def go() -> T:
        engine = create_async_engine(
            get_settings().postgres.async_url, poolclass=NullPool
        )
        try:
            async with engine.connect() as conn:
                result = await work(conn)
                await conn.commit()
                return result
        finally:
            await engine.dispose()

    return asyncio.run(go())


def table_names() -> set[str]:
    def read(sync_conn: Connection) -> set[str]:
        return set(inspect(sync_conn).get_table_names())

    return _run(lambda conn: conn.run_sync(read))


def columns_of(table: str) -> set[str]:
    def read(sync_conn: Connection) -> set[str]:
        return {column["name"] for column in inspect(sync_conn).get_columns(table)}

    return _run(lambda conn: conn.run_sync(read))


def execute(sql: str, params: dict[str, Any] | None = None) -> list[tuple[Any, ...]]:
    async def run(conn: AsyncConnection) -> list[tuple[Any, ...]]:
        result = await conn.execute(text(sql), params or {})
        if not result.returns_rows:
            return []
        return [tuple(row) for row in result.all()]

    return _run(run)


def test_no_model_migration_drift(alembic_config: Config) -> None:
    """`alembic check` fails if autogenerate would produce a new revision."""
    command.check(alembic_config)


def test_migration_chain_roundtrips(alembic_config: Config) -> None:
    """head -> base -> head must work; broken downgrades block rollbacks."""
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")


def test_head_carries_the_app_key_table_and_the_inert_columns() -> None:
    """AC-4: at head the schema holds both halves of `0016`."""
    assert "provider_apps" in table_names()
    assert columns_of("provider_apps") == {
        "id",
        "provider",
        "app_key",
        "created_at",
        "updated_at",
    }
    assert columns_of("oauth_authorizations") >= INERT_AUTHORIZATION_COLUMNS


def test_downgrading_one_drops_the_table_and_both_inert_columns(
    alembic_config: Config,
) -> None:
    """AC-4 edge: `downgrade -1` undoes both halves of `0016`, not just one."""
    command.downgrade(alembic_config, "-1")
    try:
        assert "provider_apps" not in table_names()
        assert not (INERT_AUTHORIZATION_COLUMNS & columns_of("oauth_authorizations"))
    finally:
        command.upgrade(alembic_config, "head")
    assert "provider_apps" in table_names()
    # The schema the models describe, again — a downgrade/upgrade round trip
    # that left a column behind would show up here and nowhere else.
    command.check(alembic_config)


def test_upgrading_keeps_a_pending_authorization_with_null_new_columns(
    alembic_config: Config,
) -> None:
    """AC-4 edge: a flow in progress survives the upgrade, unfinished.

    The columns are added nullable precisely so this is true: an athlete who
    was mid-connect when the deployment restarted keeps a redeemable flow,
    with the two redirect columns empty because the flow was started by the
    paste ritual that does not use them.
    """
    command.downgrade(alembic_config, "-1")
    row_id = uuid.uuid7()
    now = dt.datetime.now(dt.UTC)
    execute(
        "INSERT INTO oauth_authorizations "
        "(id, provider, code_verifier, created_at, expires_at) "
        "VALUES (:id, 'dropbox', :verifier, :now, :expires)",
        {
            "id": row_id,
            "verifier": "a-verifier-from-before-the-upgrade",
            "now": now,
            "expires": now + dt.timedelta(minutes=15),
        },
    )

    command.upgrade(alembic_config, "head")

    assert execute(
        "SELECT code_verifier, state, redirect_uri FROM oauth_authorizations "
        "WHERE id = :id",
        {"id": row_id},
    ) == [("a-verifier-from-before-the-upgrade", None, None)]

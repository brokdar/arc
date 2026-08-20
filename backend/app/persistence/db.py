"""Async SQLAlchemy engine, session factory, and the two ways to get a session.

Transaction boundary: **the caller owns the commit.** Services call
:func:`commit` at the end of a mutating use-case; neither the FastAPI
dependency nor :func:`session_scope` commits on the way out. Committing in the
dependency's teardown would put the COMMIT *after* the response boundary, where
a deferred-constraint violation can no longer reach the exception handlers
registered in `app.core.exceptions` — the client gets a bare plain-text 500
instead of the documented JSON error envelope.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends
from sqlalchemy import MetaData
from sqlalchemy.exc import DBAPIError, IntegrityError, InvalidRequestError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.exceptions import ConflictError, ValidationError

#: Deterministic names for every index and constraint.
#:
#: Without this, unnamed constraints get whatever the backend invents
#: (`athlete_pkey` on Postgres, nothing usable on SQLite), and Alembic then
#: cannot emit a `DROP CONSTRAINT` for them — a downgrade or a batch migration
#: on SQLite fails on a constraint it has no name for. The convention has to be
#: set before the first migration, because renaming constraints later is itself
#: a migration.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return the lazily-created process-wide engine."""
    global _engine  # noqa: PLW0603
    if _engine is None:
        settings = get_settings()
        # In tests every pytest test runs in its own event loop; pooled
        # asyncpg connections must not outlive the loop they were created on.
        pool_kwargs = {"poolclass": NullPool} if settings.environment == "test" else {}
        _engine = create_async_engine(settings.postgres.async_url, **pool_kwargs)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the lazily-created session factory."""
    global _session_factory  # noqa: PLW0603
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


def set_session_factory(factory: async_sessionmaker[AsyncSession] | None) -> None:
    """Override the process-wide session factory; ``None`` restores the default.

    The test seam for everything that does not go through the FastAPI
    dependency — MCP tools, scheduler jobs, ingest — so a unit fixture can bind
    them to the in-memory SQLite engine.

    Also drops the cached engine. It is built once from `get_settings()`, which
    tests re-read after `get_settings.cache_clear()`; without this the first
    test to touch the database would pin every later one to its settings. The
    dropped engine is not disposed (this is sync): pass a factory whose engine
    the caller owns and disposes, which is what the fixtures do.
    """
    global _engine, _session_factory  # noqa: PLW0603
    _engine = None
    _session_factory = factory


#: Postgres `character_not_in_repertoire` — a NUL byte in a text value.
#:
#: Matched on the SQLSTATE rather than the driver's exception class or its
#: message: the code is in the SQL standard, so it survives an asyncpg upgrade
#: and needs no driver import here. SQLite stores NUL happily, so this never
#: fires there and the unit suite cannot see the case at all.
_CHARACTER_NOT_IN_REPERTOIRE = "22021"


@asynccontextmanager
async def _write_failures_translated(session: AsyncSession) -> AsyncIterator[None]:
    """Turn a write the caller caused into an `AppError`, never a 500.

    Three ways a write fails for a reason that is not arc breaking, all of them
    reachable from ordinary concurrent use and none of them a server fault:

    * a **uniqueness** violation — a service's pre-check lost a race;
    * a **stale** UPDATE — the row was deleted between the read and the flush
      of a read-modify-write, which is the same lost race seen from the other
      side, and so also a 409;
    * a **NUL byte** in text, which Postgres refuses outright.

    The session is rolled back first so it stays usable and no connection is
    left holding a failed transaction. Only the driver's own message is
    surfaced: the statement's bound parameters can hold anything the client
    sent.
    """
    try:
        yield
    except IntegrityError as exc:
        await session.rollback()
        original = getattr(exc, "orig", None)
        detail = f"Conflicts with existing data: {original}" if original else str(exc)
        raise ConflictError(detail) from exc
    except StaleDataError as exc:
        await session.rollback()
        raise ConflictError(
            "That record changed while this write was in flight — it was "
            "removed by another write. Read it again and retry."
        ) from exc
    except DBAPIError as exc:
        # Everything else a DBAPI raises — a dropped connection, a timeout — is
        # arc's problem and must stay a 500, so this re-raises unless it is the
        # one client-caused code.
        if getattr(exc.orig, "sqlstate", None) != _CHARACTER_NOT_IN_REPERTOIRE:
            raise
        await session.rollback()
        raise ValidationError(
            "That text contains a NUL byte, which cannot be stored. Remove it "
            "and send the value again."
        ) from exc


async def flush(session: AsyncSession) -> None:
    """Flush pending changes, translating the write failures a caller causes.

    Repositories flush through here: a pre-check can always lose a race, and an
    untranslated driver exception is a 500.
    """
    async with _write_failures_translated(session):
        await session.flush()


async def refresh(
    session: AsyncSession, instance: object, attribute_names: list[str] | None = None
) -> None:
    """Re-read server-generated columns, translating a row that vanished.

    The write half of a read-modify-write is guarded by :func:`flush`; this is
    the read that follows it, and it loses the same race one line later. When
    another transaction deleted the row in between, SQLAlchemy has no row to
    refresh from and raises `InvalidRequestError` — inside this function that
    has exactly one meaning, because the only statement it issues is the
    re-read of an instance the caller just wrote.
    """
    try:
        await session.refresh(instance, attribute_names)
    except InvalidRequestError as exc:
        await session.rollback()
        raise ConflictError(
            "That record was removed while this write was in flight. Read it "
            "again and retry."
        ) from exc


async def commit(session: AsyncSession) -> None:
    """Commit, translating the write failures a caller causes.

    The COMMIT is the last moment a write can fail — deferred constraints and
    races both surface here — so it happens inside the request/tool boundary
    where `AppError` still becomes a proper response.
    """
    async with _write_failures_translated(session):
        await session.commit()


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Session for non-HTTP callers: MCP tools, scheduler jobs, ingest.

    Rolls back and closes on error; the caller commits (see the module
    docstring). Bound to the test engine by :func:`set_session_factory`.
    """
    async with get_session_factory()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session. Rolls back on error; never commits."""
    async with session_scope() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]

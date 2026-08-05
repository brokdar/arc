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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.exceptions import ConflictError

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


@asynccontextmanager
async def _integrity_as_conflict(session: AsyncSession) -> AsyncIterator[None]:
    """Turn a constraint violation into a `ConflictError` (409, not 500).

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


async def flush(session: AsyncSession) -> None:
    """Flush pending changes, translating constraint violations.

    Repositories flush through here: a service's uniqueness pre-check can
    always lose a race, and an untranslated `IntegrityError` is a 500.
    """
    async with _integrity_as_conflict(session):
        await session.flush()


async def commit(session: AsyncSession) -> None:
    """Commit, translating constraint violations into :class:`ConflictError`.

    The COMMIT is the last moment a write can fail — deferred constraints and
    races both surface here — so it happens inside the request/tool boundary
    where `AppError` still becomes a proper response.
    """
    async with _integrity_as_conflict(session):
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

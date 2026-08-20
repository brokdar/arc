"""A NUL byte in text is a 422, on the only dialect that can tell.

SQLite stores `\\x00` in a `TEXT` column without complaint, so the unit suite
cannot see this case at all: Postgres refuses it with SQLSTATE 22021, and
before `app.persistence.db` classified that code the athlete got a 500 from
any endpoint whose free text happened to carry one.

Found by Schemathesis, which generates NUL bytes readily.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.exceptions import ValidationError
from app.domain.connections import ConnectionProvider
from app.persistence.connections import ProviderAppRow
from app.persistence.db import flush


@asynccontextmanager
async def _session() -> AsyncIterator[AsyncSession]:
    """One session on its own engine, disposed on the way out.

    `NullPool` and the explicit disposal for the same reasons as
    `test_connections_postgres._session`.
    """
    engine = create_async_engine(get_settings().postgres.async_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


async def test_a_nul_byte_in_text_is_a_validation_error_not_a_crash() -> None:
    async with _session() as session:
        session.add(
            ProviderAppRow(provider=ConnectionProvider.DROPBOX, app_key="ab\x00cd")
        )

        with pytest.raises(ValidationError):
            await flush(session)


async def test_the_session_survives_a_refused_nul() -> None:
    """The rollback keeps the connection usable for the next statement."""
    async with _session() as session:
        session.add(
            ProviderAppRow(provider=ConnectionProvider.DROPBOX, app_key="ab\x00cd")
        )
        with pytest.raises(ValidationError):
            await flush(session)

        session.add(
            ProviderAppRow(provider=ConnectionProvider.DROPBOX, app_key="clean")
        )
        await flush(session)


async def test_text_postgres_does_accept_is_still_stored() -> None:
    """The bound is NUL, not "unusual characters" — control chars still store."""
    async with _session() as session:
        row = ProviderAppRow(provider=ConnectionProvider.DROPBOX, app_key="ab\x01cd ")
        session.add(row)
        await flush(session)

        assert row.app_key == "ab\x01cd "

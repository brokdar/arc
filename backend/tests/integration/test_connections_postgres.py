"""AC-8: the encrypted credential and the feed constraint, on real Postgres.

Two things the SQLite unit suite cannot prove. The credential is a `bytea`
round-trip — a driver that mangled the ciphertext would still hand SQLite back
something, and the failure would surface months later as a connection that
cannot refresh — and the one-feed-per-folder rule is a database constraint, so
the test that matters is the one the *database* fails, not the one the service
pre-check catches.
"""

import base64
import datetime as dt
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domain.connections import ConnectionProvider, ConnectionStatus
from app.persistence.connections import (
    ConnectionRow,
    CredentialDecryptionError,
    EncryptedCredentials,
    FeedRow,
)

#: The key the fixture installs, and a second one that is merely valid.
KEY = base64.urlsafe_b64encode(b"integration-test-key-32-bytes-ok").decode()
OTHER_KEY = base64.urlsafe_b64encode(b"a-different-key-of-32-bytes-here").decode()

REFRESH_TOKEN = "refresh-token-that-must-never-be-readable"


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Install a known key, and clear the settings cache on both sides.

    On the way out as well as in, because a test here rotates the key mid-run
    and a cached `Settings` holding the rotated one would follow the suite into
    the next module.
    """
    monkeypatch.setenv("SECRETS__ENCRYPTION_KEY", KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@asynccontextmanager
async def _session() -> AsyncIterator[AsyncSession]:
    """One session on its own engine, disposed on the way out.

    The disposal is not tidiness: `filterwarnings = ["error"]` turns the
    `ResourceWarning` from a pooled connection finalised by the garbage
    collector into a test failure — in whichever test happens to be running
    when it fires, which is never this one. `NullPool` for the same reason
    `app.persistence.db` uses it under `ENVIRONMENT=test`: every test has its
    own event loop, and a pooled asyncpg connection must not outlive one.
    """
    engine = create_async_engine(get_settings().postgres.async_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


async def _connection() -> uuid.UUID:
    async with _session() as session:
        row = ConnectionRow(
            provider=ConnectionProvider.DROPBOX,
            status=ConnectionStatus.CONNECTED,
            account_label="Ada Lovelace (ada@example.com)",
            scopes=["account_info.read", "files.metadata.read", "files.content.read"],
            credentials=EncryptedCredentials.seal(
                {"access_token": "access", "refresh_token": REFRESH_TOKEN}
            ),
            access_token_expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=4),
        )
        session.add(row)
        await session.commit()
        return row.id


async def test_the_encrypted_credential_round_trips_through_postgres() -> None:
    connection_id = await _connection()

    async with _session() as session:
        stored = await session.get(ConnectionRow, connection_id)
        assert stored is not None
        assert EncryptedCredentials.unseal(stored.credentials) == {
            "access_token": "access",
            "refresh_token": REFRESH_TOKEN,
        }
        # And the column really is ciphertext, read past the ORM.
        raw = await session.scalar(
            text("SELECT credentials FROM connections WHERE id = :id"),
            {"id": connection_id},
        )
        assert REFRESH_TOKEN.encode() not in bytes(raw)
        assert bytes(raw) == stored.credentials


async def test_decrypting_under_another_key_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection_id = await _connection()
    monkeypatch.setenv("SECRETS__ENCRYPTION_KEY", OTHER_KEY)
    get_settings.cache_clear()

    async with _session() as session:
        stored = await session.get(ConnectionRow, connection_id)
        assert stored is not None
        with pytest.raises(CredentialDecryptionError) as raised:
            EncryptedCredentials.unseal(stored.credentials)

    # The error has to name the setting, or the operator is left guessing
    # between a rotated key and a corrupted column.
    assert "SECRETS__ENCRYPTION_KEY" in str(raised.value)


async def test_a_connection_whose_key_has_moved_reads_as_error_not_connected(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _connection()
    monkeypatch.setenv("SECRETS__ENCRYPTION_KEY", OTHER_KEY)
    get_settings.cache_clear()

    response = await client.get("/api/v1/connections")

    assert response.status_code == 200, response.text
    connection = response.json()["items"][0]
    assert connection["status"] == "error"
    assert "SECRETS__ENCRYPTION_KEY" in (connection["last_error"] or "")


async def test_the_database_itself_refuses_a_second_feed_on_one_folder() -> None:
    connection_id = await _connection()

    async with _session() as session:
        session.add(FeedRow(connection_id=connection_id, remote_path="/apps/wahoo"))
        await session.commit()

    async with _session() as session:
        # The service normalises and pre-checks; this is the constraint under
        # it, which is what makes the rule true for a race and for anything
        # writing outside the service.
        session.add(FeedRow(connection_id=connection_id, remote_path="/apps/wahoo"))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_a_sealed_credential_is_not_readable_without_the_key() -> None:
    blob = EncryptedCredentials.seal({"refresh_token": REFRESH_TOKEN})

    assert REFRESH_TOKEN.encode() not in blob
    assert Fernet(KEY).decrypt(blob)

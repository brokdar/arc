"""Unit test fixtures: the full app wired to an in-memory SQLite database.

Unit tests must not require external services. Anything needing a real
Postgres belongs in tests/integration.
"""

from collections.abc import AsyncIterator, Iterator
from typing import Any

import bcrypt
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.main import create_app
from app.persistence import load_models
from app.persistence.db import Base, get_session, set_session_factory

#: The password every authenticated fixture logs in with.
TEST_PASSWORD = "test-password"


@pytest.fixture(scope="session")
def password_hash() -> str:
    """bcrypt hash of TEST_PASSWORD, computed once (cost 4 — tests, not prod)."""
    return bcrypt.hashpw(TEST_PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()


@pytest.fixture(autouse=True)
def _auth_env(password_hash: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give every test a bootable auth config.

    `get_settings` is `lru_cache`d, so the cache is cleared on both sides:
    before, so the app under test picks these up; after, so a test that pokes
    at settings itself cannot leak into the next one.
    """
    monkeypatch.setenv("AUTH__PASSWORD_HASH", password_hash)
    monkeypatch.setenv("AUTH__SESSION__SECRET_KEY", "unit-test-secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """In-memory SQLite engine with the full schema created.

    `load_models()` first: `create_all` only emits the tables registered on
    `Base.metadata`, which without the sweep is whatever `app.main`'s import
    graph happens to reach — a model nobody imports becomes `no such table` at
    the first query rather than a missing-model error.
    """
    load_models()
    engine = create_async_engine("sqlite+aiosqlite://")

    # SQLite ignores foreign keys unless asked, per connection. Without this,
    # `ON DELETE CASCADE` and `ON DELETE SET NULL` are inert in the unit suite
    # and enforced in production — the exact divergence `app.persistence.types`
    # exists to prevent (D29), one layer down in the schema.
    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(connection: Any, _record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> Iterator[async_sessionmaker[AsyncSession]]:
    """Session factory on the test engine, installed as the process-wide one.

    Installing it is what binds non-HTTP code — MCP tools, scheduler jobs,
    ingest — to SQLite: those call `session_scope()`, which has no dependency
    injection to override. Reset afterwards so nothing leaks into the next
    test (and so a stray `get_session_factory()` cannot reach a real Postgres).
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    set_session_factory(factory)
    yield factory
    set_session_factory(None)


@pytest.fixture
async def db_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """A session on the test engine, for tests that assert against the database."""
    async with session_factory() as session:
        yield session


@pytest.fixture
def app(session_factory: async_sessionmaker[AsyncSession]) -> FastAPI:
    """The application, with request sessions bound to the test engine.

    The override mirrors `db.get_session` exactly: yield, never commit — the
    service owns the commit, so that a failure at COMMIT still reaches the
    exception handlers.
    """

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    application = create_app()
    application.dependency_overrides[get_session] = override_get_session
    return application


@pytest.fixture
async def anon_client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """HTTP client against the app with NO session — exercises the guard."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture
async def client(anon_client: AsyncClient) -> AsyncClient:
    """Authenticated HTTP client — logs in for real and keeps the cookie.

    Going through the login endpoint (rather than forging a cookie) means
    every protected-route test also exercises the session round-trip.
    """
    response = await anon_client.post(
        "/api/v1/auth/login", json={"password": TEST_PASSWORD}
    )
    assert response.status_code == 204, response.text
    return anon_client

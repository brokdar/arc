"""Unit test fixtures: the full app wired to an in-memory SQLite database.

Unit tests must not require external services. Anything needing a real
Postgres belongs in tests/integration.
"""

from collections.abc import AsyncIterator, Iterator

import bcrypt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.main import create_app
from app.persistence.db import Base, get_session

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
    """In-memory SQLite engine with the full schema created."""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def anon_client(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    """HTTP client against the app with NO session — exercises the guard."""
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session
            await session.commit()

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
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

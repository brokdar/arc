"""Integration test fixtures — require a real Postgres.

Run via `just test-int`, which starts the docker compose test database and
sets POSTGRES__* env vars before invoking pytest with this directory.

The schema is built by running the REAL Alembic migrations (not
``create_all``), so every integration run also validates that the migration
chain produces the schema the models expect.
"""

from collections.abc import AsyncIterator

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.main import create_app
from app.persistence.db import Base


@pytest.fixture(scope="session")
def alembic_config() -> Config:
    """Alembic config; tests run with cwd=backend so relative paths resolve."""
    return Config("alembic.ini")


@pytest.fixture(scope="session", autouse=True)
def _migrated_database(alembic_config: Config) -> None:
    """Build the schema from scratch via the real migration chain."""
    get_settings.cache_clear()  # settings are cached; pick up POSTGRES__* env
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")


@pytest.fixture(autouse=True)
async def _clean_tables(_migrated_database: None) -> AsyncIterator[None]:
    """Empty all tables after each test for isolation (keeps the schema)."""
    yield
    engine = create_async_engine(get_settings().postgres.async_url)
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())
    await engine.dispose()


#: Matches the AUTH__PASSWORD_HASH exported by scripts/run-integration-tests.sh.
TEST_PASSWORD = "integration-test-password"


@pytest.fixture
async def anon_client() -> AsyncIterator[AsyncClient]:
    """HTTP client against the app using the real database, with no session."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture
async def client(anon_client: AsyncClient) -> AsyncClient:
    """Authenticated HTTP client — logs in for real, keeping the cookie."""
    response = await anon_client.post(
        "/api/v1/auth/login", json={"password": TEST_PASSWORD}
    )
    assert response.status_code == 204, response.text
    return anon_client

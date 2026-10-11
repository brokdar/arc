"""`session_mmp` against a real Postgres: JSONB arrays, the unique key, the cascade.

What the unit suite cannot prove on SQLite: that the four parallel arrays come
back from JSONB exactly as written (integers stay integers, booleans stay
booleans), that the one-row-per-session key holds in the database rather than
in the service's good intentions, that `ON DELETE CASCADE` is real, and that
`0022` applies to a database already holding sessions with `alembic check`
reporting no drift.
"""

import asyncio
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from httpx import AsyncClient
from sqlalchemy import NullPool
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.domain.actor import Actor
from app.domain.power_curve import GRID_VERSION, MmpPoint, PowerCurve
from app.main import DATA_SUBDIRECTORIES
from app.persistence.power_curve import SessionMmpRow
from app.services.power_curve import PowerCurveService
from tests.unit.golden_fit import golden

UPLOAD = "/api/v1/ingest/upload"

#: A curve with every awkward value a round trip could lose: a fractional
#: tenth, a zero, a large index, and both flag values.
CURVE = PowerCurve(
    grid_version=GRID_VERSION,
    points=(
        MmpPoint(duration_s=1, watts=912.3, start_index=4_017, clean=False),
        MmpPoint(duration_s=2, watts=845.0, start_index=4_016, clean=True),
        MmpPoint(duration_s=3, watts=0.1, start_index=0, clean=True),
    ),
)


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A throwaway runtime data tree, with `DATA__ROOT` pointed at it."""
    root = tmp_path / "runtime-data"
    for name in DATA_SUBDIRECTORIES:
        (root / name).mkdir(parents=True)
    monkeypatch.setenv("DATA__ROOT", str(root))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


async def upload(client: AsyncClient, name: str) -> uuid.UUID:
    """Ingest one golden file and return its session id."""
    response = await client.post(
        UPLOAD,
        files={"file": (name, golden(name).read_bytes(), "application/octet-stream")},
    )
    assert response.status_code == 200, response.text
    [session_id] = response.json()["session_ids"]
    return uuid.UUID(session_id)


async def fetch(sql: str, **params: object) -> list[tuple[object, ...]]:
    """Read the database back as plain tuples, on a throwaway connection."""
    engine = create_async_engine(get_settings().postgres.async_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(sa.text(sql), params)
            return [tuple(row) for row in result]
    finally:
        await engine.dispose()


async def store(session_id: uuid.UUID, curve: PowerCurve | None) -> None:
    """Write one curve through the service, in its own transaction."""
    engine = create_async_engine(get_settings().postgres.async_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await PowerCurveService.from_session(session).store(
                session_id, curve, actor=Actor.system()
            )
    finally:
        await engine.dispose()


async def read(session_id: uuid.UUID) -> PowerCurve | None:
    """Read one session's curve back through the service."""
    engine = create_async_engine(get_settings().postgres.async_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as session:
            return (
                await PowerCurveService.from_session(session).read(session_id)
            ).curve
    finally:
        await engine.dispose()


async def test_the_row_round_trips_unchanged(
    data_root: Path, client: AsyncClient
) -> None:
    # A session with no power, so the ingest leaves the table empty and the
    # only row is the one written here.
    session_id = await upload(client, "strength_watch.fit")

    await store(session_id, CURVE)

    assert await read(session_id) == CURVE
    [row] = await fetch(
        "SELECT grid_version, durations_s, watts_dw, start_indices, clean, "
        "jsonb_typeof(watts_dw -> 0), jsonb_typeof(clean -> 0) "
        "FROM session_mmp WHERE session_id = :id",
        id=session_id,
    )
    assert row == (
        GRID_VERSION,
        [1, 2, 3],
        [9123, 8450, 1],
        [4_017, 4_016, 0],
        [False, True, True],
        "number",
        "boolean",
    )


async def test_recompute_replaces_the_row(data_root: Path, client: AsyncClient) -> None:
    session_id = await upload(client, "outdoor_ride.fit")
    [(computed_id, computed_watts)] = await fetch(
        "SELECT id, watts_dw FROM session_mmp WHERE session_id = :id", id=session_id
    )
    # A stale curve in its place, as a stream rebuilt since would leave it.
    await store(session_id, CURVE)

    response = await client.post(f"/api/v1/sessions/{session_id}/metrics/recompute")
    assert response.status_code == 200, response.text

    [(replaced_id, replaced_watts)] = await fetch(
        "SELECT id, watts_dw FROM session_mmp WHERE session_id = :id", id=session_id
    )
    assert replaced_watts == computed_watts
    assert replaced_id != computed_id


async def test_recomputing_twice_leaves_exactly_one_row(
    data_root: Path, client: AsyncClient
) -> None:
    session_id = await upload(client, "outdoor_ride.fit")

    for _ in range(2):
        response = await client.post(f"/api/v1/sessions/{session_id}/metrics/recompute")
        assert response.status_code == 200, response.text

    assert await fetch(
        "SELECT count(*) FROM session_mmp WHERE session_id = :id", id=session_id
    ) == [(1,)]


async def test_the_database_refuses_a_second_row_for_one_session(
    data_root: Path, client: AsyncClient
) -> None:
    session_id = await upload(client, "outdoor_ride.fit")
    engine = create_async_engine(get_settings().postgres.async_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as session:
            session.add(
                SessionMmpRow(
                    session_id=session_id,
                    grid_version=GRID_VERSION,
                    durations_s=[1],
                    watts_dw=[1],
                    start_indices=[0],
                    clean=[True],
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
    finally:
        await engine.dispose()


async def test_deleting_the_session_deletes_the_row(
    data_root: Path, client: AsyncClient
) -> None:
    session_id = await upload(client, "outdoor_ride.fit")
    assert await fetch(
        "SELECT count(*) FROM session_mmp WHERE session_id = :id", id=session_id
    ) == [(1,)]

    engine = create_async_engine(get_settings().postgres.async_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            # Raw SQL, so the database's ON DELETE CASCADE does the work and
            # not an ORM relationship.
            await conn.execute(
                sa.text("DELETE FROM sessions WHERE id = :id"), {"id": session_id}
            )
    finally:
        await engine.dispose()

    assert await fetch("SELECT count(*) FROM session_mmp") == [(0,)]


async def test_a_stream_with_no_power_channel_writes_no_row(
    data_root: Path, client: AsyncClient
) -> None:
    session_id = await upload(client, "strength_watch.fit")

    assert await fetch("SELECT count(*) FROM session_mmp") == [(0,)]
    response = await client.get(f"/api/v1/sessions/{session_id}/power-curve")
    assert response.json()["status"] == "not_assessed"


async def test_the_migration_applies_over_existing_sessions_without_drift(
    data_root: Path, client: AsyncClient, alembic_config: Config
) -> None:
    session_id = await upload(client, "outdoor_ride.fit")

    # Back to the revision before the table, with the session still there…
    await asyncio.to_thread(command.downgrade, alembic_config, "0021")
    assert await fetch("SELECT to_regclass('session_mmp')") == [(None,)]
    assert await fetch(
        "SELECT count(*) FROM sessions WHERE id = :id", id=session_id
    ) == [(1,)]

    # …and forward again: the session survives, has no curve until a rebuild,
    # and the models agree with the schema the chain built.
    await asyncio.to_thread(command.upgrade, alembic_config, "head")
    await asyncio.to_thread(command.check, alembic_config)

    assert await fetch(
        "SELECT count(*) FROM sessions WHERE id = :id", id=session_id
    ) == [(1,)]
    assert await fetch("SELECT count(*) FROM session_mmp") == [(0,)]

"""The column conventions must behave identically on SQLite and Postgres.

Unit tests run on SQLite and production runs on Postgres, so anything that
diverges between the two is a bug the suite cannot see. These tests pin the
SQLite half; `tests/integration/test_anchors_postgres.py` pins the other.
"""

import datetime as dt
import enum
import uuid

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.persistence.types import JSONColumn, UtcDateTime, enum_column

BERLIN = dt.timezone(dt.timedelta(hours=2))


class Colour(enum.StrEnum):
    RED = "red"
    BLUE = "blue"


@pytest.fixture
def probe_table() -> sa.Table:
    """A throwaway table using every convention, on its own MetaData."""
    return sa.Table(
        "probe",
        sa.MetaData(),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("at", UtcDateTime, server_default=sa.func.now()),
        sa.Column("doc", JSONColumn),
        sa.Column("colour", enum_column(Colour)),
    )


@pytest.fixture
async def probe(engine: AsyncEngine, probe_table: sa.Table) -> sa.Table:
    """The probe table, created on the in-memory SQLite engine."""
    async with engine.begin() as conn:
        await conn.run_sync(probe_table.metadata.create_all)
    return probe_table


# --- UtcDateTime --------------------------------------------------------------


async def test_server_default_reads_back_as_aware_utc(
    engine: AsyncEngine, probe: sa.Table
) -> None:
    # Plain DateTime(timezone=True) returns a NAIVE datetime here and an aware
    # one on Postgres — the divergence this type exists to remove.
    async with engine.begin() as conn:
        await conn.execute(probe.insert().values(id=uuid.uuid7()))
        stored = await conn.scalar(sa.select(probe.c.at))

    assert stored is not None
    assert stored.tzinfo is not None
    assert stored.utcoffset() == dt.timedelta(0)


async def test_offset_datetimes_are_normalized_to_utc(
    engine: AsyncEngine, probe: sa.Table
) -> None:
    noon_in_berlin = dt.datetime(2026, 6, 1, 12, 0, tzinfo=BERLIN)

    async with engine.begin() as conn:
        await conn.execute(probe.insert().values(id=uuid.uuid7(), at=noon_in_berlin))
        stored = await conn.scalar(sa.select(probe.c.at))

    assert stored is not None
    assert stored == noon_in_berlin
    assert stored.tzinfo is dt.UTC
    assert stored.hour == 10


async def test_naive_datetimes_are_rejected_not_guessed_at(
    engine: AsyncEngine, probe: sa.Table
) -> None:
    naive = dt.datetime(2026, 6, 1, 12, 0)  # noqa: DTZ001 — the point of the test

    with pytest.raises(StatementError, match="naive datetime"):
        async with engine.begin() as conn:
            await conn.execute(probe.insert().values(id=uuid.uuid7(), at=naive))


async def test_null_timestamps_survive_both_directions(engine: AsyncEngine) -> None:
    nullable = sa.Table(
        "nullable_at",
        sa.MetaData(),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("at", UtcDateTime, nullable=True),
    )
    async with engine.begin() as conn:
        await conn.run_sync(nullable.metadata.create_all)
        await conn.execute(nullable.insert().values(id=uuid.uuid7(), at=None))

        assert await conn.scalar(sa.select(nullable.c.at)) is None


async def test_api_timestamps_are_aware_utc_iso_strings(client: AsyncClient) -> None:
    # The end-to-end consequence: the JSON the frontend parses carries an
    # offset. Serializing a naive datetime would silently emit local time.
    created = (await client.get("/api/v1/athlete")).json()

    for field in ("created_at", "updated_at"):
        parsed = dt.datetime.fromisoformat(created[field])
        assert parsed.tzinfo is not None, created[field]
        assert parsed.utcoffset() == dt.timedelta(0), created[field]


# --- JSONColumn ---------------------------------------------------------------


async def test_json_round_trips_nested_documents(
    engine: AsyncEngine, probe: sa.Table
) -> None:
    document = {"zones": [1, 2, 3], "model": {"name": "coggan_7", "ok": True}}

    async with engine.begin() as conn:
        await conn.execute(probe.insert().values(id=uuid.uuid7(), doc=document))

        assert await conn.scalar(sa.select(probe.c.doc)) == document


def test_json_becomes_jsonb_on_postgres(probe_table: sa.Table) -> None:
    column_type = probe_table.c.doc.type

    assert isinstance(column_type.dialect_impl(postgresql.dialect()), postgresql.JSONB)
    assert isinstance(column_type.dialect_impl(sqlite.dialect()), sa.JSON)


# --- enums and ids ------------------------------------------------------------


async def test_enums_round_trip_as_members(
    engine: AsyncEngine, probe: sa.Table
) -> None:
    async with engine.begin() as conn:
        await conn.execute(probe.insert().values(id=uuid.uuid7(), colour=Colour.BLUE))

        assert await conn.scalar(sa.select(probe.c.colour)) is Colour.BLUE


async def test_enums_are_stored_as_their_value_not_their_name(
    engine: AsyncEngine, probe: sa.Table
) -> None:
    # `MAX_HR` in the database while every JSON payload says `max_hr` would be
    # two spellings of one vocabulary. Read as raw text, bypassing the ORM
    # conversion, so this sees what is really on disk.
    async with engine.begin() as conn:
        await conn.execute(probe.insert().values(id=uuid.uuid7(), colour=Colour.BLUE))

        stored = await conn.scalar(sa.text("SELECT colour FROM probe"))

    assert stored == "blue"


def test_enums_are_stored_non_native() -> None:
    # A native Postgres ENUM needs ALTER TYPE to gain a member and has no
    # SQLite equivalent; a VARCHAR + CHECK is the portable storage.
    assert enum_column(Colour).native_enum is False


async def test_ids_are_time_ordered_uuid7(client: AsyncClient) -> None:
    anchor = {"anchor_type": "ftp", "value": 250, "provenance": "estimated"}
    first = (await client.post("/api/v1/anchors", json=anchor)).json()["id"]
    second = (await client.post("/api/v1/anchors", json=anchor)).json()["id"]

    assert uuid.UUID(first).version == 7
    # uuid7's leading 48 bits are a millisecond timestamp, so ids sort by
    # creation — that is the property indexes and pagination rely on.
    assert first < second

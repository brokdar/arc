"""Items CRUD against a real Postgres — verifies dialect-specific behavior."""

import datetime as dt

from httpx import AsyncClient

ITEMS = "/api/v1/items"


async def test_full_crud_roundtrip(client: AsyncClient) -> None:
    created = (
        await client.post(ITEMS, json={"name": "pg-item", "description": "real db"})
    ).json()

    listed = (await client.get(ITEMS)).json()
    assert listed["total"] == 1

    updated = (
        await client.patch(f"{ITEMS}/{created['id']}", json={"description": "new"})
    ).json()
    assert updated["description"] == "new"

    assert (await client.delete(f"{ITEMS}/{created['id']}")).status_code == 204
    assert (await client.get(f"{ITEMS}/{created['id']}")).status_code == 404


async def test_unique_constraint_enforced_by_database(client: AsyncClient) -> None:
    assert (await client.post(ITEMS, json={"name": "dup"})).status_code == 201
    assert (await client.post(ITEMS, json={"name": "dup"})).status_code == 409


async def test_timestamps_are_aware_utc_here_too(client: AsyncClient) -> None:
    # The Postgres half of the UtcDateTime contract; the SQLite half is in
    # tests/unit/test_persistence_types.py. Both dialects must agree, or the
    # unit suite is testing a different application than the one that ships.
    created = (await client.post(ITEMS, json={"name": "stamped"})).json()

    for field in ("created_at", "updated_at"):
        parsed = dt.datetime.fromisoformat(created[field])
        assert parsed.utcoffset() == dt.timedelta(0), created[field]

"""Every failed write must come back as the documented JSON error envelope.

The regression these guard: the commit used to happen in `get_session`'s
teardown, i.e. AFTER the endpoint returned. An exception there — a deferred
constraint, a serialization failure, a race the service's pre-check missed —
never reached `register_exception_handlers`, so the client got a plain-text
500 that is in no OpenAPI contract and no generated frontend type.
"""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.items import ItemRepository

ITEMS = "/api/v1/items"


def _assert_error_envelope(response: Any, status: int) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/json"), (
        f"error rendered as {response.headers['content-type']}: {response.text[:200]}"
    )
    assert isinstance(response.json()["detail"], str)


async def test_failure_at_commit_returns_the_json_envelope(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing_commit(self: AsyncSession) -> None:
        raise IntegrityError(
            "INSERT INTO items ...", {}, Exception("deferred constraint violated")
        )

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)

    response = await client.post(ITEMS, json={"name": "doomed"})

    _assert_error_envelope(response, 409)
    assert "deferred constraint violated" in response.json()["detail"]


async def test_race_losing_insert_conflicts_rather_than_500(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A concurrent writer taking the name between the service's uniqueness
    # check and its INSERT looks exactly like this: the pre-check passes and
    # the database refuses the write.
    assert (await client.post(ITEMS, json={"name": "taken"})).status_code == 201

    async def blind_check(self: ItemRepository, name: str) -> None:
        return None

    monkeypatch.setattr(ItemRepository, "get_by_name", blind_check)

    _assert_error_envelope(await client.post(ITEMS, json={"name": "taken"}), 409)


async def test_a_failed_write_leaves_the_session_usable(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The translation rolls back before raising; without that the connection
    # sits in a failed transaction and the next statement raises too.
    assert (await client.post(ITEMS, json={"name": "first"})).status_code == 201

    async def blind_check(self: ItemRepository, name: str) -> None:
        return None

    monkeypatch.setattr(ItemRepository, "get_by_name", blind_check)
    assert (await client.post(ITEMS, json={"name": "first"})).status_code == 409

    monkeypatch.undo()
    assert (await client.get(ITEMS)).json()["total"] == 1
    assert (await client.post(ITEMS, json={"name": "second"})).status_code == 201


async def test_service_errors_still_use_the_envelope(client: AsyncClient) -> None:
    _assert_error_envelope(await client.get(f"{ITEMS}/{uuid.uuid4()}"), 404)

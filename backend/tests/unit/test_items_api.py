"""CRUD flow tests for the example items domain."""

import uuid

from httpx import AsyncClient

ITEMS = "/api/v1/items"


async def _create(client: AsyncClient, name: str = "widget") -> dict:
    response = await client.post(ITEMS, json={"name": name, "description": "a thing"})
    assert response.status_code == 201
    return response.json()


async def test_create_returns_item_with_generated_fields(client: AsyncClient) -> None:
    item = await _create(client)

    assert item["name"] == "widget"
    assert item["description"] == "a thing"
    assert uuid.UUID(item["id"])
    assert item["created_at"]


async def test_create_duplicate_name_conflicts(client: AsyncClient) -> None:
    await _create(client)

    response = await client.post(ITEMS, json={"name": "widget"})

    assert response.status_code == 409


async def test_list_returns_page(client: AsyncClient) -> None:
    await _create(client, "one")
    await _create(client, "two")

    response = await client.get(ITEMS)

    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 2
    assert {item["name"] for item in page["items"]} == {"one", "two"}


async def test_get_returns_item(client: AsyncClient) -> None:
    created = await _create(client)

    response = await client.get(f"{ITEMS}/{created['id']}")

    assert response.status_code == 200
    assert response.json()["name"] == "widget"


async def test_get_unknown_id_returns_404(client: AsyncClient) -> None:
    response = await client.get(f"{ITEMS}/{uuid.uuid4()}")

    assert response.status_code == 404


async def test_update_changes_fields(client: AsyncClient) -> None:
    created = await _create(client)

    response = await client.patch(
        f"{ITEMS}/{created['id']}", json={"description": "updated"}
    )

    assert response.status_code == 200
    assert response.json()["description"] == "updated"
    assert response.json()["name"] == "widget"


async def test_update_rename_to_taken_name_conflicts(client: AsyncClient) -> None:
    await _create(client, "taken")
    created = await _create(client, "renameme")

    response = await client.patch(f"{ITEMS}/{created['id']}", json={"name": "taken"})

    assert response.status_code == 409


# The next four tests pin down edge cases originally found by Schemathesis
# fuzzing — keep them: they guard 500s at the validation boundary.


async def test_update_with_explicit_null_name_is_rejected(
    client: AsyncClient,
) -> None:
    created = await _create(client)

    response = await client.patch(f"{ITEMS}/{created['id']}", json={"name": None})

    assert response.status_code == 422


async def test_update_with_explicit_null_description_clears_it(
    client: AsyncClient,
) -> None:
    created = await _create(client)

    response = await client.patch(
        f"{ITEMS}/{created['id']}", json={"description": None}
    )

    assert response.status_code == 200
    assert response.json()["description"] is None


async def test_nul_bytes_in_name_are_rejected(client: AsyncClient) -> None:
    response = await client.post(ITEMS, json={"name": "bad\x00name"})

    assert response.status_code == 422


async def test_lone_surrogates_get_422_not_crash(client: AsyncClient) -> None:
    # Pydantic rejects the lone surrogate, but the default 422 handler would
    # then CRASH serializing the error (it echoes the bad input back). Our
    # sanitizing handler must return a well-formed 422.
    response = await client.post(
        ITEMS,
        content=b'{"name": "\\udbdd"}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]  # response body itself is valid JSON


async def test_truly_malformed_body_returns_documented_400(
    client: AsyncClient,
) -> None:
    response = await client.post(
        ITEMS,
        content=b"\x0f\xff\xfe not json",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400


async def test_huge_offset_is_rejected_not_500(client: AsyncClient) -> None:
    response = await client.get(ITEMS, params={"offset": 36656796423090853642240})

    assert response.status_code == 422


async def test_delete_removes_item(client: AsyncClient) -> None:
    created = await _create(client)

    response = await client.delete(f"{ITEMS}/{created['id']}")
    assert response.status_code == 204

    response = await client.get(f"{ITEMS}/{created['id']}")
    assert response.status_code == 404

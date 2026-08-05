"""Zones through HTTP: how the anchor version is selected, and what comes back."""

import uuid
from typing import Any

from httpx import AsyncClient

ANCHORS = "/api/v1/anchors"
ZONES = "/api/v1/zones"


async def append(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "anchor_type": "ftp",
        "value": 250,
        "provenance": "estimated",
    } | overrides
    response = await client.post(ANCHORS, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def test_zones_default_to_the_anchor_version_in_force(
    client: AsyncClient,
) -> None:
    await append(client, value=240, effective_date="2026-01-01")
    current = await append(client, value=250, effective_date="2026-03-01")

    response = await client.get(ZONES, params={"anchor_type": "ftp"})

    assert response.status_code == 200
    body = response.json()
    assert body["anchor_version"]["id"] == current["id"]
    assert body["model"] == "coggan_7"
    assert len(body["zones"]) == 7
    assert body["zones"][3]["lower"] == 225.0  # 90% of 250 W


async def test_zones_can_be_pinned_to_a_specific_version(client: AsyncClient) -> None:
    old = await append(client, value=240, effective_date="2026-01-01")
    await append(client, value=250, effective_date="2026-03-01")

    body = (await client.get(f"{ANCHORS}/{old['id']}/zones")).json()

    # What a frozen prescription asks for: the version its targets were
    # computed from, not whatever is current now.
    assert body["anchor_version"]["id"] == old["id"]
    assert body["zones"][3]["lower"] == 216.0  # 90% of 240 W


async def test_heart_rate_zones_use_the_lthr_model(client: AsyncClient) -> None:
    await append(client, anchor_type="lthr", value=170, provenance="athlete_reported")

    body = (await client.get(ZONES, params={"anchor_type": "lthr"})).json()

    assert body["model"] == "lthr_5"
    assert len(body["zones"]) == 5
    assert body["zones"][0]["unit"] == "bpm"


async def test_the_zone_model_can_be_named_explicitly(client: AsyncClient) -> None:
    await append(client, value=250)

    body = (
        await client.get(ZONES, params={"anchor_type": "ftp", "zone_model": "coggan_7"})
    ).json()

    assert body["model"] == "coggan_7"


async def test_a_zone_model_that_does_not_fit_the_anchor_is_rejected(
    client: AsyncClient,
) -> None:
    await append(client, value=250)

    response = await client.get(
        ZONES, params={"anchor_type": "ftp", "zone_model": "lthr_5"}
    )

    assert response.status_code == 422
    assert "derives from lthr" in response.json()["detail"]


async def test_an_anchor_type_with_no_zone_model_says_so(client: AsyncClient) -> None:
    await append(client, anchor_type="max_hr", value=190)

    response = await client.get(ZONES, params={"anchor_type": "max_hr"})

    assert response.status_code == 422
    assert "no zone model derives from" in response.json()["detail"]


async def test_the_top_zone_is_open_ended(client: AsyncClient) -> None:
    await append(client, value=250)

    zones = (await client.get(ZONES, params={"anchor_type": "ftp"})).json()["zones"]

    assert zones[-1]["upper"] is None
    assert zones[-1]["upper_pct"] is None


async def test_the_current_zones_endpoint_needs_an_anchor_type(
    client: AsyncClient,
) -> None:
    # The selector is required rather than defaulted: there is no anchor type
    # that is obviously "the" one, and guessing would silently answer about
    # power when the caller meant heart rate.
    assert (await client.get(ZONES)).status_code == 422


async def test_a_pinned_zone_model_can_be_named_too(client: AsyncClient) -> None:
    created = await append(client, value=250)

    body = (
        await client.get(
            f"{ANCHORS}/{created['id']}/zones", params={"zone_model": "coggan_7"}
        )
    ).json()

    assert body["model"] == "coggan_7"
    assert body["anchor_version"]["id"] == created["id"]


async def test_zones_for_an_unknown_version_return_404(client: AsyncClient) -> None:
    response = await client.get(f"{ANCHORS}/{uuid.uuid4()}/zones")

    assert response.status_code == 404


async def test_zones_without_an_anchor_return_404(client: AsyncClient) -> None:
    response = await client.get(ZONES, params={"anchor_type": "ftp"})

    assert response.status_code == 404


async def test_zones_are_never_stored(client: AsyncClient) -> None:
    # Nothing to assert against a zones table because there is none: the same
    # request answered twice from the same anchor gives the same numbers, and
    # a new anchor changes them without a migration or a recompute job.
    await append(client, value=250, effective_date="2026-01-01")
    before = (await client.get(ZONES, params={"anchor_type": "ftp"})).json()

    await append(client, value=300, effective_date="2026-06-01")
    after = (await client.get(ZONES, params={"anchor_type": "ftp"})).json()

    assert before["zones"][3]["lower"] == 225.0
    assert after["zones"][3]["lower"] == 270.0


async def test_zones_need_a_session(anon_client: AsyncClient) -> None:
    assert (await anon_client.get(ZONES)).status_code == 401
    assert (await anon_client.get(f"{ANCHORS}/{uuid.uuid4()}/zones")).status_code == 401

"""Anchors through HTTP: append, read, and the append-only refusals.

The 405 tests are the point of this file. FastAPI answers an undefined
method+path combination with 404, which reads as "wrong id" — so PUT, PATCH
and DELETE on an anchor version are real handlers that say *why* the operation
does not exist.
"""

import datetime as dt
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import athlete_today
from app.core.exceptions import ValidationError
from app.domain.actor import Actor
from app.domain.anchors import AnchorSource, AnchorType, Provenance
from app.persistence.anchors import AnchorVersionRow
from app.services.anchors import AnchorService

ANCHORS = "/api/v1/anchors"


async def append(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    """Append an anchor version, asserting it was accepted."""
    payload: dict[str, Any] = {
        "anchor_type": "ftp",
        "value": 250,
        "provenance": "estimated",
    } | overrides
    response = await client.post(ANCHORS, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# --- appending ----------------------------------------------------------------


async def test_append_returns_the_stored_version(client: AsyncClient) -> None:
    version = await append(client)

    assert uuid.UUID(version["id"])
    assert version["anchor_type"] == "ftp"
    assert version["value"] == 250
    # Defaults the API fills in: the anchor type's own unit, today, `fresh`,
    # and `athlete` — the agent writes through MCP (WP-8), not this endpoint.
    assert version["unit"] == "W"
    assert version["effective_date"] == athlete_today().isoformat()
    assert version["staleness_state"] == "fresh"
    assert version["source"] == "athlete"


async def test_appending_does_not_replace_the_previous_version(
    client: AsyncClient,
) -> None:
    first = await append(client, value=240)
    second = await append(client, value=260)

    listed = (await client.get(ANCHORS)).json()

    assert listed["total"] == 2
    assert {version["id"] for version in listed["items"]} == {
        first["id"],
        second["id"],
    }


async def test_a_tested_value_without_a_protocol_is_rejected(
    client: AsyncClient,
) -> None:
    response = await client.post(
        ANCHORS,
        json={"anchor_type": "ftp", "value": 260, "provenance": "tested"},
    )

    assert response.status_code == 422
    assert "protocol" in response.json()["detail"]


async def test_a_tested_value_with_a_protocol_is_accepted(client: AsyncClient) -> None:
    version = await append(
        client, provenance="tested", protocol="20min x0.95", value=265
    )

    assert version["provenance"] == "tested"
    assert version["protocol"] == "20min x0.95"


async def test_an_implausible_value_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        ANCHORS,
        json={"anchor_type": "ftp", "value": 25_000, "provenance": "estimated"},
    )

    assert response.status_code == 422
    assert "between" in response.json()["detail"]


async def test_the_wrong_unit_is_rejected_rather_than_converted(
    client: AsyncClient,
) -> None:
    response = await client.post(
        ANCHORS,
        json={
            "anchor_type": "ftp",
            "value": 250,
            "provenance": "estimated",
            "unit": "bpm",
        },
    )

    assert response.status_code == 422


async def test_an_unknown_anchor_type_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        ANCHORS,
        json={"anchor_type": "vo2max", "value": 60, "provenance": "estimated"},
    )

    assert response.status_code == 422


async def test_an_inverted_confidence_interval_is_rejected(
    client: AsyncClient,
) -> None:
    response = await client.post(
        ANCHORS,
        json={
            "anchor_type": "ftp",
            "value": 250,
            "provenance": "estimated",
            "ci_low": 260,
            "ci_high": 240,
        },
    )

    assert response.status_code == 422


# --- reading ------------------------------------------------------------------


async def test_list_filters_by_anchor_type(client: AsyncClient) -> None:
    await append(client, anchor_type="ftp", value=250)
    await append(client, anchor_type="lthr", value=168)

    page = (await client.get(ANCHORS, params={"anchor_type": "lthr"})).json()

    assert page["total"] == 1
    assert page["items"][0]["anchor_type"] == "lthr"
    assert page["items"][0]["unit"] == "bpm"


async def test_list_returns_the_version_in_force_first(client: AsyncClient) -> None:
    await append(client, value=240, effective_date="2026-01-01")
    newest = await append(client, value=260, effective_date="2026-03-01")

    page = (await client.get(ANCHORS)).json()

    assert page["items"][0]["id"] == newest["id"]


async def test_get_returns_one_version(client: AsyncClient) -> None:
    created = await append(client)

    response = await client.get(f"{ANCHORS}/{created['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


async def test_get_unknown_id_returns_404(client: AsyncClient) -> None:
    assert (await client.get(f"{ANCHORS}/{uuid.uuid4()}")).status_code == 404


async def test_current_resolves_the_version_in_force(client: AsyncClient) -> None:
    await append(client, value=240, effective_date="2026-01-01")
    in_force = await append(client, value=255, effective_date="2026-03-01")
    # Effective a month out: appended, but not yet the athlete's FTP.
    future = (dt.datetime.now(dt.UTC).date() + dt.timedelta(days=30)).isoformat()
    await append(client, value=270, effective_date=future)

    response = await client.get(f"{ANCHORS}/current", params={"anchor_type": "ftp"})

    assert response.status_code == 200
    assert response.json()["id"] == in_force["id"]


async def test_the_default_effective_date_is_the_athletes_day_not_the_utc_one(
    client: AsyncClient, athlete_zone: Callable[[str], None]
) -> None:
    """An FTP appended at 08:00 on the 20th in Auckland applies from the 20th.

    Two extreme zones rather than one, because that is what makes this
    deterministic: at any instant, ``UTC+14:00`` is on a later calendar date
    than Greenwich or ``UTC-11:00`` is on an earlier one — never neither. So
    one of these two appends is guaranteed to be a day the UTC clock would not
    have chosen, whenever the suite happens to run.

    Before issue #62 the default was `created_at.date()` with `created_at` in
    UTC, which back-dated every Auckland morning's append by a day and put it
    in force for a day of training it was not measured on.
    """
    utc_today = dt.datetime.now(dt.UTC).date()
    dated: list[dt.date] = []

    for zone in ("UTC+14:00", "UTC-11:00"):
        athlete_zone(zone)
        version = await append(client)
        assert version["effective_date"] == athlete_today().isoformat()
        dated.append(dt.date.fromisoformat(version["effective_date"]))

    assert any(day != utc_today for day in dated), (
        "neither zone disagreed with UTC, which cannot happen: "
        f"{dated} against {utc_today}"
    )


async def test_effectivity_is_resolved_on_the_athletes_day(
    client: AsyncClient, athlete_zone: Callable[[str], None]
) -> None:
    """The same history, the same instant, two athletes, two answers.

    ``UTC+14:00`` and ``UTC-11:00`` are twenty-five hours apart, so whenever
    this runs the eastern athlete's calendar date is strictly later than the
    western one's. A version effective from the *eastern* date is therefore in
    force for them and still in the future for the westerner — which is the
    whole of what `effective_date` means, and precisely what reading the day
    off the UTC instant destroyed: an anchor effective "from the 20th" was out
    of force for the first hours of an Auckland 20th and in force two hours
    early for a Honolulu athlete's 19th (issue #62, finding 7).
    """
    athlete_zone("UTC+14:00")
    east = athlete_today()
    athlete_zone("UTC-11:00")
    west = athlete_today()
    assert east > west, "the two zones are 25 hours apart and cannot share a date"

    await append(client, value=240, effective_date="2020-01-01")
    from_the_eastern_day = await append(
        client, value=299, effective_date=east.isoformat()
    )

    async def in_force() -> dict[str, Any]:
        response = await client.get(f"{ANCHORS}/current", params={"anchor_type": "ftp"})
        assert response.status_code == 200, response.text
        return response.json()

    # Still on ``UTC-11:00``: the eastern day has not arrived here.
    assert (await in_force())["value"] == 240

    athlete_zone("UTC+14:00")
    assert (await in_force())["id"] == from_the_eastern_day["id"]


async def test_a_version_stamped_in_the_future_is_still_in_force(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """`current` reads `effective_date`, and must not read `created_at`.

    This is the shape a **non-monotonic clock** produces, and it is not
    hypothetical: `created_at` is stamped from `dt.datetime.now`, which reads
    `CLOCK_REALTIME`, and a host that steps that clock backwards — WSL2 does,
    by roughly 180 ms every 30 s — gives a version appended a moment ago a
    stamp in the future of the very next read.

    `current` used to go through `anchor_as_of`, which additionally requires
    `created_at <= now`. Under that rule this version is invisible, and the
    athlete is told to "append an FTP first" about the FTP they just appended
    — which is what made planning a session, pinning an anchor and computing
    zones fail at random in the suite and, more rarely, in a real deployment.
    """
    appended = await append(client, value=282)
    # Reach past the service to do what a backwards clock does to it: the same
    # row, with a stamp the next read's `now()` has not reached yet.
    row = await db_session.get(AnchorVersionRow, uuid.UUID(appended["id"]))
    assert row is not None
    row.created_at = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=5)
    await db_session.commit()

    response = await client.get(f"{ANCHORS}/current", params={"anchor_type": "ftp"})

    assert response.status_code == 200, response.text
    assert response.json()["id"] == appended["id"]


async def test_current_without_any_version_returns_404(client: AsyncClient) -> None:
    response = await client.get(f"{ANCHORS}/current", params={"anchor_type": "ftp"})

    assert response.status_code == 404
    assert "append one first" in response.json()["detail"]


# --- append-only --------------------------------------------------------------


async def test_a_version_cannot_be_replaced(client: AsyncClient) -> None:
    created = await append(client)

    response = await client.put(f"{ANCHORS}/{created['id']}", json={"value": 300})

    assert response.status_code == 405
    assert "append-only" in response.json()["detail"]


async def test_a_version_cannot_be_edited(client: AsyncClient) -> None:
    created = await append(client)

    response = await client.patch(f"{ANCHORS}/{created['id']}", json={"value": 300})

    assert response.status_code == 405
    assert "append-only" in response.json()["detail"]


async def test_a_version_cannot_be_deleted(client: AsyncClient) -> None:
    created = await append(client)

    response = await client.delete(f"{ANCHORS}/{created['id']}")

    assert response.status_code == 405
    assert "append-only" in response.json()["detail"]


async def test_the_refusal_says_what_the_resource_does_accept(
    client: AsyncClient,
) -> None:
    # RFC 9110: a 405 must carry an `Allow` header. Ours is the difference
    # between "you cannot do that" and "you cannot do that, here is what you
    # can".
    created = await append(client)

    response = await client.delete(f"{ANCHORS}/{created['id']}")

    assert response.headers["allow"] == "GET"


async def test_a_non_uuid_id_is_still_refused_with_405(client: AsyncClient) -> None:
    # `PUT /anchors/current` used to answer 422 about UUID syntax, which is
    # true but beside the point: the method is what does not exist.
    response = await client.put(f"{ANCHORS}/current", json={"value": 300})

    assert response.status_code == 405


async def test_a_refused_edit_changes_nothing(client: AsyncClient) -> None:
    created = await append(client, value=250)

    await client.patch(f"{ANCHORS}/{created['id']}", json={"value": 300})
    await client.delete(f"{ANCHORS}/{created['id']}")

    still_there = (await client.get(f"{ANCHORS}/{created['id']}")).json()
    assert still_there["value"] == 250
    assert (await client.get(ANCHORS)).json()["total"] == 1


def test_the_repository_offers_no_way_to_edit_history() -> None:
    # The 405s are the polite half of the rule; this is the half a new caller
    # cannot route around.
    from app.persistence.anchors import AnchorRepository

    assert not {"update", "delete", "remove", "merge"} & set(vars(AnchorRepository))


async def test_anchors_need_a_session(anon_client: AsyncClient) -> None:
    assert (await anon_client.get(ANCHORS)).status_code == 401
    assert (await anon_client.post(ANCHORS, json={})).status_code == 401
    assert (await anon_client.delete(f"{ANCHORS}/{uuid.uuid4()}")).status_code == 401


# Found by Schemathesis fuzzing in CI — keep it: an optional query parameter
# typed `X | None` advertises `null` as a legal value in the contract, but a
# query string delivers `anchor_type=null` as a four-letter string the enum
# rejects with a 422. Optional-by-omission parameters use
# `SkipJsonSchema[None]` so the contract only promises what the parser accepts.
async def test_optional_query_params_do_not_advertise_null(
    client: AsyncClient,
) -> None:
    spec = (await client.get("/openapi.json")).json()
    offenders = [
        (path, param["name"])
        for path, methods in spec["paths"].items()
        for operation in methods.values()
        for param in operation.get("parameters", [])
        if param["in"] == "query" and "null" in str(param["schema"])
    ]
    assert offenders == []


# Reserved anchor types: `cp` and `w_prime` exist as vocabulary so that
# WP-5 can add the critical-power model without migrating stored values, but
# nothing may write them yet. The contract's create enum only offers the MVP
# three, and the service refuses the reserved two for callers that do not come
# through the schema (WP-8's MCP tools).


async def test_reserved_anchor_types_cannot_be_appended(client: AsyncClient) -> None:
    for reserved in ("cp", "w_prime"):
        response = await client.post(
            ANCHORS,
            json={"anchor_type": reserved, "value": 300, "provenance": "estimated"},
        )

        assert response.status_code == 422


async def test_the_service_refuses_reserved_types_without_the_schema(
    db_session: AsyncSession,
) -> None:
    service = AnchorService.from_session(db_session)

    with pytest.raises(ValidationError, match="reserved"):
        await service.append(
            actor=Actor.parse("agent:test"),
            anchor_type=AnchorType.CP,
            value=300,
            provenance=Provenance.ESTIMATED,
            source=AnchorSource.AGENT,
        )

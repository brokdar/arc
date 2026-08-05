"""The athlete profile through HTTP: bootstrap, partial update, validation."""

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.athlete import SINGLETON_ATHLETE_ID, Athlete
from app.persistence.audit import AuditLogEntry

ATHLETE = "/api/v1/athlete"


async def test_the_profile_is_bootstrapped_empty_on_first_read(
    client: AsyncClient,
) -> None:
    # No migration seeds the row (see docs/decisions.md): the first caller
    # creates it, so a fresh database and a restored dump behave the same.
    response = await client.get(ATHLETE)

    assert response.status_code == 200
    profile = response.json()
    assert profile["name"] is None
    assert profile["sex"] == "unspecified"
    assert profile["capabilities"] == {}
    assert profile["created_at"]


async def test_reading_twice_returns_the_same_profile(client: AsyncClient) -> None:
    first = (await client.get(ATHLETE)).json()
    second = (await client.get(ATHLETE)).json()

    assert first["created_at"] == second["created_at"]


async def test_update_creates_the_profile_if_it_does_not_exist_yet(
    client: AsyncClient,
) -> None:
    response = await client.patch(ATHLETE, json={"name": "Alex"})

    assert response.status_code == 200
    assert response.json()["name"] == "Alex"


async def test_update_is_partial(client: AsyncClient) -> None:
    await client.patch(ATHLETE, json={"name": "Alex", "height_cm": 178.0})

    response = await client.patch(ATHLETE, json={"sex": "female"})

    profile = response.json()
    assert profile["sex"] == "female"
    assert profile["name"] == "Alex"
    assert profile["height_cm"] == 178.0


async def test_explicit_null_clears_a_field(client: AsyncClient) -> None:
    await client.patch(ATHLETE, json={"name": "Alex"})

    response = await client.patch(ATHLETE, json={"name": None})

    assert response.json()["name"] is None


async def test_the_full_profile_round_trips(client: AsyncClient) -> None:
    payload = {
        "name": "Alex Rider",
        "date_of_birth": "1990-06-15",
        "sex": "male",
        "height_cm": 181.5,
        "capabilities": {"cycling": {"weekly_hours": 8}},
    }

    response = await client.patch(ATHLETE, json=payload)

    assert response.status_code == 200
    assert {key: response.json()[key] for key in payload} == payload
    assert (await client.get(ATHLETE)).json()["capabilities"] == payload["capabilities"]


async def test_an_implausible_height_is_rejected(client: AsyncClient) -> None:
    response = await client.patch(ATHLETE, json={"height_cm": 17.0})

    assert response.status_code == 422


async def test_a_future_date_of_birth_is_rejected(client: AsyncClient) -> None:
    response = await client.patch(ATHLETE, json={"date_of_birth": "2099-01-01"})

    assert response.status_code == 422


async def test_a_birth_year_before_1900_is_rejected_by_the_domain(
    client: AsyncClient,
) -> None:
    # No schema constraint covers this one: it reaches the domain rule, which
    # the service translates into the same 422 envelope.
    response = await client.patch(ATHLETE, json={"date_of_birth": "1089-05-04"})

    assert response.status_code == 422
    assert "1900" in response.json()["detail"]


async def test_an_unknown_field_is_rejected_rather_than_ignored(
    client: AsyncClient,
) -> None:
    # With one athlete and no undo, a silently dropped edit is expensive.
    response = await client.patch(ATHLETE, json={"nmae": "typo"})

    assert response.status_code == 422


async def test_a_blank_name_is_rejected(client: AsyncClient) -> None:
    response = await client.patch(ATHLETE, json={"name": "   "})

    assert response.status_code == 422


async def test_the_profile_needs_a_session(anon_client: AsyncClient) -> None:
    assert (await anon_client.get(ATHLETE)).status_code == 401
    assert (await anon_client.patch(ATHLETE, json={"name": "x"})).status_code == 401


# The next three pin edge cases found by Schemathesis fuzzing — keep them:
# they guard 500s at the validation boundary.


async def test_nul_bytes_in_a_capability_key_are_rejected(
    client: AsyncClient,
) -> None:
    # `capabilities` is free-form JSON stored as JSONB, so the driver's limits
    # apply at every depth, not just to the top-level string columns.
    response = await client.patch(
        ATHLETE, json={"capabilities": {"cycling\x00": {"weekly_hours": 8}}}
    )

    assert response.status_code == 422


async def test_lone_surrogates_in_a_nested_capability_are_rejected(
    client: AsyncClient,
) -> None:
    response = await client.patch(
        ATHLETE,
        content=b'{"capabilities": {"cycling": {"note": "\\udba6"}}}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]  # the response body is itself valid JSON


async def test_a_body_that_is_not_an_object_gets_422_not_500(
    client: AsyncClient,
) -> None:
    response = await client.patch(
        ATHLETE,
        content=b"[null, null]",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422


async def test_a_rejected_update_does_not_bootstrap_the_profile(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # A whitespace-only name passes the schema (min_length counts characters)
    # and fails the domain's blank-name rule — so this 422 comes from the
    # service, after the point where `update` used to bootstrap. A rejected
    # first-ever PATCH must be a pure no-op: no profile row, no audit trail.
    response = await client.patch(ATHLETE, json={"name": "   "})

    assert response.status_code == 422
    assert await db_session.get(Athlete, SINGLETON_ATHLETE_ID) is None
    audit_rows = await db_session.execute(select(AuditLogEntry))
    assert list(audit_rows.scalars()) == []


async def test_update_bootstraps_and_updates_in_one_transaction(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # A valid first-ever PATCH creates the row and applies the update
    # atomically, auditing both halves.
    response = await client.patch(ATHLETE, json={"name": "Alex"})

    assert response.status_code == 200
    rows = await db_session.execute(select(AuditLogEntry).order_by(AuditLogEntry.id))
    assert [row.action for row in rows.scalars()] == [
        "athlete.created",
        "athlete.updated",
    ]

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

from app.persistence.athlete import Athlete, AthleteRepository

ANCHORS = "/api/v1/anchors"
ATHLETE = "/api/v1/athlete"
FTP = {"anchor_type": "ftp", "value": 250, "provenance": "estimated"}


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
            "INSERT INTO anchor_versions ...",
            {},
            Exception("deferred constraint violated"),
        )

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)

    response = await client.post(ANCHORS, json=FTP)

    _assert_error_envelope(response, 409)
    assert "deferred constraint violated" in response.json()["detail"]


async def test_race_losing_insert_conflicts_rather_than_500(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A blind lookup that *always* misses is the pathological version of the
    # bootstrap race: the insert conflicts and the recovery re-read finds
    # nothing either. The service re-raises rather than inventing a row, and
    # the conflict surfaces as a 409 envelope, not a 500. (The realistic
    # race, where the re-read finds the winner's row, recovers to a 200 —
    # see test_a_lost_bootstrap_race_recovers_with_the_winners_row.)
    assert (await client.get(ATHLETE)).status_code == 200

    async def blind_lookup(self: AthleteRepository) -> Athlete | None:
        return None

    monkeypatch.setattr(AthleteRepository, "get", blind_lookup)

    _assert_error_envelope(await client.get(ATHLETE), 409)


async def test_a_failed_write_leaves_the_session_usable(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The translation rolls back before raising; without that the connection
    # sits in a failed transaction and the next statement raises too.
    assert (await client.get(ATHLETE)).status_code == 200

    async def blind_lookup(self: AthleteRepository) -> Athlete | None:
        return None

    monkeypatch.setattr(AthleteRepository, "get", blind_lookup)
    assert (await client.get(ATHLETE)).status_code == 409

    monkeypatch.undo()
    assert (await client.get(ATHLETE)).status_code == 200
    assert (await client.post(ANCHORS, json=FTP)).status_code == 201


async def test_service_errors_still_use_the_envelope(client: AsyncClient) -> None:
    _assert_error_envelope(await client.get(f"{ANCHORS}/{uuid.uuid4()}"), 404)


async def test_the_405_refusals_use_the_envelope_too(client: AsyncClient) -> None:
    created = (await client.post(ANCHORS, json=FTP)).json()

    _assert_error_envelope(await client.delete(f"{ANCHORS}/{created['id']}"), 405)


async def test_a_lost_bootstrap_race_recovers_with_the_winners_row(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The realistic race: the loser's insert conflicts, but the winner's row
    # is right there on re-read — so the loser returns it instead of a 409.
    # First call sees no row (as both racers did); the retry sees the truth.
    assert (await client.get(ATHLETE)).status_code == 200

    real_lookup = AthleteRepository.get
    lookups = 0

    async def racing_lookup(self: AthleteRepository) -> Athlete | None:
        nonlocal lookups
        lookups += 1
        if lookups == 1:
            return None
        return await real_lookup(self)

    monkeypatch.setattr(AthleteRepository, "get", racing_lookup)

    response = await client.get(ATHLETE)

    assert response.status_code == 200
    assert lookups >= 2


# --- the malformed-body 400 is part of the contract, everywhere ---------------
#
# FastAPI answers 400, not 422, for a body it cannot parse as JSON at all, and
# that status has to be in the contract or the fuzz job fails on it — which is
# how it was found, twice now. Declaring it is one line per route (`BAD_BODY` in
# the route module), and the mistake is invisible in review because the route
# works: only the generated client and the fuzzer notice the gap. So the whole
# surface is swept rather than each new route being remembered individually.


async def test_every_json_body_operation_documents_a_bad_body(
    client: AsyncClient,
) -> None:
    spec = (await client.get("/openapi.json")).json()

    offenders = [
        f"{method.upper()} {path}"
        for path, methods in spec["paths"].items()
        for method, operation in methods.items()
        if "application/json" in operation.get("requestBody", {}).get("content", {})
        and "400" not in operation["responses"]
    ]

    assert offenders == []

"""AC-3 to AC-5, AC-8 and AC-9: every source arc collects from, over HTTP.

Asserted on the response body and on the rows behind it, never on a service
return value: the panel reads this JSON and the athlete reads the panel, so a
field that is right in Python and absent from the contract is not right.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors import dropbox
from app.domain.connections import ConnectionStatus
from app.domain.integrations import IntegrationKind
from app.persistence.activity import RecordingRow
from app.persistence.connections import ConnectionRow, FeedRow
from app.persistence.integrations import IntegrationRow
from tests.unit.dropbox_fake import FakeDropbox

pytestmark = pytest.mark.usefixtures("dropbox_env")

INTEGRATIONS = "/api/v1/integrations"
CATALOGUE_URL = "/api/v1/integration-catalogue"
AUTHORIZE = "/api/v1/connections/dropbox/authorize"
COMPLETE = "/api/v1/connections/dropbox/complete"


@pytest.fixture(autouse=True)
def fake() -> Iterator[FakeDropbox]:
    """Dropbox, faked, so nothing in this module can reach the internet."""
    upstream = FakeDropbox()
    dropbox.set_transport(upstream.transport)
    yield upstream
    dropbox.set_transport(None)


async def connect(api: httpx.AsyncClient) -> dict[str, Any]:
    """Run the paste-the-code ritual and return the connection."""
    started = await api.post(AUTHORIZE)
    assert started.status_code == 200, started.text
    completed = await api.post(COMPLETE, json={"code": "pasted-code"})
    assert completed.status_code == 201, completed.text
    return completed.json()


async def seed_feed(
    session: AsyncSession, connection_id: str, remote_path: str
) -> FeedRow:
    """An **unclassified** watched folder, written straight to the database.

    Direct, not through the API: `POST /feeds` is retired by this PR (AC-10),
    and these rows stand for the ones an installation already has and that
    `0017` deliberately did not guess at.
    """
    row = FeedRow(connection_id=uuid.UUID(connection_id), remote_path=remote_path)
    session.add(row)
    await session.commit()
    return row


async def seed_classified_feed(
    session: AsyncSession, connection_id: str, kind: IntegrationKind, remote_path: str
) -> IntegrationRow:
    """A folder already attached to an integration — what `0017` leaves behind.

    Seeded as two rows rather than derived from the path on read, and that is
    the model: the classification is stored, by the migration (AC-6) or by the
    add use-case (AC-8), never re-derived while rendering. A read that matched
    `remote_path` against the catalogue would silently re-file a folder the day
    a default path changed, and would report an integration for which no row —
    and therefore no id to pause or remove — exists.
    """
    row = IntegrationRow(kind=kind)
    row.feeds.append(
        FeedRow(connection_id=uuid.UUID(connection_id), remote_path=remote_path)
    )
    session.add(row)
    await session.commit()
    return row


async def count_of(session: AsyncSession, model: type[Any]) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


def entry(body: dict[str, Any], kind: str | None) -> dict[str, Any]:
    """The one item of a given kind. Fails loudly when there is not exactly one."""
    found = [item for item in body["items"] if item["kind"] == kind]
    assert len(found) == 1, f"expected one {kind!r} entry, got {found}"
    return found[0]


# --- AC-3: the local drop is always there ------------------------------------


async def test_the_local_drop_leads_the_list_with_its_path_and_interval(
    data_root: Path, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    assert await count_of(db_session, IntegrationRow) == 0

    response = await client.get(INTEGRATIONS)

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert items, "the local drop is always present"
    first = items[0]
    assert first["kind"] == "local_drop"
    assert first["data_kinds"] == ["recordings"]
    assert first["transport"] == "local_folder"
    assert first["local"]["inbox_path"] == str((data_root / "inbox").resolve())
    assert Path(first["local"]["inbox_path"]).is_absolute()
    assert first["local"]["scan_interval_seconds"] == 30
    # It is synthesized, so nothing was written to make it appear.
    assert await count_of(db_session, IntegrationRow) == 0


async def test_the_local_drop_cannot_be_deleted(
    data_root: Path, client: httpx.AsyncClient
) -> None:
    response = await client.delete(f"{INTEGRATIONS}/local_drop")

    # Never 204: an athlete who removed the local drop could not get it back,
    # and `data/inbox/` would keep sweeping with nothing in Settings saying so.
    assert response.status_code in {404, 405}, response.text
    assert (await client.get(INTEGRATIONS)).json()["items"][0]["kind"] == "local_drop"


async def test_the_local_drop_is_not_removable_in_the_contract(
    data_root: Path, client: httpx.AsyncClient
) -> None:
    body = await (await client.get(INTEGRATIONS)).aread()
    assert b'"removable":false' in body.replace(b" ", b"")


async def test_listing_integrations_needs_a_session(
    anon_client: httpx.AsyncClient,
) -> None:
    assert (await anon_client.get(INTEGRATIONS)).status_code == 401
    assert (await anon_client.get(CATALOGUE_URL)).status_code == 401
    assert (await anon_client.post(INTEGRATIONS, json={})).status_code == 401


# --- AC-4: existing folders, classified and not --------------------------------


async def test_a_catalogue_folder_reads_as_wahoo_and_the_rest_stays_unclassified(
    data_root: Path, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    connection = await connect(client)
    await seed_classified_feed(
        db_session, connection["id"], IntegrationKind.WAHOO, "/apps/wahoofitness"
    )
    await seed_feed(db_session, connection["id"], "/photos")

    response = await client.get(INTEGRATIONS)

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 3, items
    wahoo = entry(response.json(), "wahoo")
    assert [folder["remote_path"] for folder in wahoo["folders"]] == [
        "/apps/wahoofitness"
    ]
    assert wahoo["display_name"] == "Wahoo"
    assert wahoo["data_kinds"] == ["recordings"]
    assert wahoo["storage"] == "dropbox"
    assert wahoo["prompt"] is None

    unclassified = entry(response.json(), None)
    assert [folder["remote_path"] for folder in unclassified["folders"]] == ["/photos"]
    # Not invented from the other: `/photos` is not Wahoo, and Wahoo did not
    # acquire a second folder.
    assert isinstance(unclassified["prompt"], str)
    assert unclassified["prompt"].strip() != ""
    assert unclassified["data_kinds"] == []


async def test_an_integration_with_two_folders_lists_both_in_path_order(
    data_root: Path, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    connection = await connect(client)
    created = await client.post(
        INTEGRATIONS,
        json={
            "kind": "wahoo",
            "transport": "cloud_folder",
            "connection_id": connection["id"],
            "remote_path": "/apps/wahoofitness",
        },
    )
    assert created.status_code == 201, created.text
    second = await client.post(
        INTEGRATIONS,
        json={
            "kind": "wahoo",
            "transport": "cloud_folder",
            "connection_id": connection["id"],
            "remote_path": "/aaa-first",
        },
    )
    assert second.status_code == 200, second.text

    wahoo = entry((await client.get(INTEGRATIONS)).json(), "wahoo")

    assert [folder["remote_path"] for folder in wahoo["folders"]] == [
        "/aaa-first",
        "/apps/wahoofitness",
    ]


async def test_a_credential_that_will_not_decrypt_reports_the_connection_error(
    data_root: Path,
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings

    connection = await connect(client)
    await seed_classified_feed(
        db_session, connection["id"], IntegrationKind.WAHOO, "/apps/wahoofitness"
    )
    # The key moved: `_settle_readability` reports `error` on the read path,
    # and the folder carries it so the panel shows the athlete one fault.
    monkeypatch.setenv(
        "SECRETS__ENCRYPTION_KEY", "0Ck1YQNZBEmwXBv0Ku8mQxk-2yQq8B8Y6Gv1cQzYQ1o="
    )
    get_settings.cache_clear()

    wahoo = entry((await client.get(INTEGRATIONS)).json(), "wahoo")

    folder = wahoo["folders"][0]
    assert folder["connection_status"] == ConnectionStatus.ERROR.value
    assert "SECRETS__ENCRYPTION_KEY" in (folder["connection_error"] or "")


# --- AC-5: the catalogue -------------------------------------------------------


async def test_the_catalogue_offers_exactly_what_arc_can_deliver(
    data_root: Path, client: httpx.AsyncClient
) -> None:
    response = await client.get(CATALOGUE_URL)

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["kind"] for item in items] == ["local_drop", "wahoo"]
    by_kind = {item["kind"]: item for item in items}
    assert by_kind["wahoo"]["display_name"] == "Wahoo"
    assert by_kind["wahoo"]["data_kinds"] == ["recordings"]
    assert by_kind["wahoo"]["transports"] == [
        {
            "kind": "cloud_folder",
            "storage": "dropbox",
            "default_path": "/apps/wahoofitness",
        }
    ]
    assert by_kind["local_drop"]["transports"] == [
        {"kind": "local_folder", "storage": None, "default_path": None}
    ]


async def test_the_catalogue_flags_the_local_drop_as_not_addable(
    data_root: Path, client: httpx.AsyncClient
) -> None:
    items = {
        item["kind"]: item for item in (await client.get(CATALOGUE_URL)).json()["items"]
    }

    assert items["local_drop"]["addable"] is False
    assert items["wahoo"]["addable"] is True


async def test_the_catalogue_names_no_integration_arc_cannot_deliver(
    data_root: Path, client: httpx.AsyncClient
) -> None:
    body = (await client.get(CATALOGUE_URL)).text.lower()

    for absent in ("strava", "zwift", "garmin", "apple"):
        assert absent not in body, f"{absent} is offered and cannot be delivered"


async def test_posting_to_the_catalogue_is_a_405_not_a_uuid_complaint(
    data_root: Path, client: httpx.AsyncClient
) -> None:
    # `.claude/rules/api-collection-facets.md`: a facet of the collection lives
    # outside the id namespace, so Starlette's own 405 is correct for free.
    response = await client.post(CATALOGUE_URL, json={})

    assert response.status_code == 405, response.text


# --- AC-8: adding an integration -----------------------------------------------


async def test_adding_wahoo_creates_the_integration_and_the_folder_at_once(
    data_root: Path, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    connection = await connect(client)

    response = await client.post(
        INTEGRATIONS,
        json={
            "kind": "wahoo",
            "transport": "cloud_folder",
            "connection_id": connection["id"],
            "remote_path": "/Apps/WahooFitness/",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "wahoo"
    assert [folder["remote_path"] for folder in body["folders"]] == [
        "/apps/wahoofitness"
    ]
    assert await count_of(db_session, IntegrationRow) == 1
    feed = (await db_session.execute(select(FeedRow))).scalars().one()
    assert feed.remote_path == "/apps/wahoofitness"
    assert feed.integration_id is not None


async def test_a_kind_the_catalogue_does_not_hold_is_refused_naming_the_kinds(
    data_root: Path, client: httpx.AsyncClient
) -> None:
    connection = await connect(client)

    response = await client.post(
        INTEGRATIONS,
        json={
            "kind": "garmin",
            "transport": "cloud_folder",
            "connection_id": connection["id"],
            "remote_path": "/apps/garmin",
        },
    )

    assert response.status_code == 422, response.text
    said = response.text.lower()
    assert "local_drop" in said
    assert "wahoo" in said


async def test_the_local_drop_cannot_be_added(
    data_root: Path, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post(
        INTEGRATIONS, json={"kind": "local_drop", "transport": "local_folder"}
    )

    assert response.status_code == 422, response.text
    assert "always present" in response.text
    assert await count_of(db_session, IntegrationRow) == 0


async def test_a_transport_the_integration_does_not_support_names_the_ones_it_does(
    data_root: Path, client: httpx.AsyncClient
) -> None:
    connection = await connect(client)

    response = await client.post(
        INTEGRATIONS,
        json={
            "kind": "wahoo",
            "transport": "oauth_api",
            "connection_id": connection["id"],
        },
    )

    assert response.status_code == 422, response.text
    assert "cloud_folder" in response.text


async def test_a_folder_another_integration_holds_is_a_409_naming_the_holder(
    data_root: Path, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    connection = await connect(client)
    created = await client.post(
        INTEGRATIONS,
        json={
            "kind": "wahoo",
            "transport": "cloud_folder",
            "connection_id": connection["id"],
            "remote_path": "/apps/wahoofitness",
        },
    )
    assert created.status_code == 201, created.text
    # A second integration would have to be another kind; the catalogue has
    # only one addable kind today, so the clash is proven against a feed no
    # integration owns — which is the same normalised comparison.
    await seed_feed(db_session, connection["id"], "/somewhere-else")

    response = await client.post(
        INTEGRATIONS,
        json={
            "kind": "wahoo",
            "transport": "cloud_folder",
            "connection_id": connection["id"],
            "remote_path": "/SOMEWHERE-ELSE/",
        },
    )

    assert response.status_code == 409, response.text
    # Un-normalised in, and still refused: `normalise_remote_path` is Python,
    # so no unique constraint could have caught this.
    assert "/somewhere-else" in response.text
    assert await count_of(db_session, FeedRow) == 2


async def test_re_adding_a_folder_the_same_integration_watches_is_a_409(
    data_root: Path, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    connection = await connect(client)
    body = {
        "kind": "wahoo",
        "transport": "cloud_folder",
        "connection_id": connection["id"],
        "remote_path": "/apps/wahoofitness",
    }
    assert (await client.post(INTEGRATIONS, json=body)).status_code == 201

    response = await client.post(
        INTEGRATIONS, json=body | {"remote_path": "/APPS/WahooFitness"}
    )

    assert response.status_code == 409, response.text
    assert "Wahoo" in response.text
    assert await count_of(db_session, FeedRow) == 1


async def test_adding_a_cloud_folder_with_no_connection_says_to_connect_one(
    data_root: Path, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post(
        INTEGRATIONS,
        json={
            "kind": "wahoo",
            "transport": "cloud_folder",
            "remote_path": "/apps/wahoofitness",
        },
    )

    # Never a 500: nothing is connected yet, which is the ordinary state of a
    # fresh instance, and the answer names the step that is missing.
    assert response.status_code == 422, response.text
    assert "dropbox" in response.text.lower()
    assert "connect" in response.text.lower()
    assert await count_of(db_session, IntegrationRow) == 0


async def test_a_remote_path_over_the_column_length_is_refused(
    data_root: Path, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    from app.persistence.connections import MAX_REMOTE_PATH_LENGTH

    connection = await connect(client)

    response = await client.post(
        INTEGRATIONS,
        json={
            "kind": "wahoo",
            "transport": "cloud_folder",
            "connection_id": connection["id"],
            "remote_path": "/" + "a" * MAX_REMOTE_PATH_LENGTH,
        },
    )

    assert response.status_code == 422, response.text
    assert await count_of(db_session, FeedRow) == 0


async def test_adding_wahoo_again_with_another_folder_is_one_integration(
    data_root: Path, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    connection = await connect(client)
    first = await client.post(
        INTEGRATIONS,
        json={
            "kind": "wahoo",
            "transport": "cloud_folder",
            "connection_id": connection["id"],
            "remote_path": "/apps/wahoofitness",
        },
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        INTEGRATIONS,
        json={
            "kind": "wahoo",
            "transport": "cloud_folder",
            "connection_id": connection["id"],
            "remote_path": "/apps/wahoo-backup",
        },
    )

    # 200, not 201: nothing new was created — an existing integration grew a
    # second folder.
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]
    assert len(second.json()["folders"]) == 2
    assert await count_of(db_session, IntegrationRow) == 1


# --- AC-9: removing an integration ---------------------------------------------


async def test_removing_an_integration_keeps_the_connection_and_the_recordings(
    data_root: Path, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    connection = await connect(client)
    created = await client.post(
        INTEGRATIONS,
        json={
            "kind": "wahoo",
            "transport": "cloud_folder",
            "connection_id": connection["id"],
            "remote_path": "/apps/wahoofitness",
        },
    )
    assert created.status_code == 201, created.text
    recordings_before = await count_of(db_session, RecordingRow)

    response = await client.delete(f"{INTEGRATIONS}/{created.json()['id']}")

    assert response.status_code == 204, response.text
    assert await count_of(db_session, IntegrationRow) == 0
    assert await count_of(db_session, FeedRow) == 0
    # The credential and the rides it already brought in are not the
    # athlete's to lose for having stopped collecting.
    assert await count_of(db_session, ConnectionRow) == 1
    assert await count_of(db_session, RecordingRow) == recordings_before


async def test_removing_one_integration_leaves_another_untouched(
    data_root: Path, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    connection = await connect(client)
    created = await client.post(
        INTEGRATIONS,
        json={
            "kind": "wahoo",
            "transport": "cloud_folder",
            "connection_id": connection["id"],
            "remote_path": "/apps/wahoofitness",
        },
    )
    assert created.status_code == 201, created.text
    # The other entry is an unclassified folder: a second *stored* integration
    # needs a second addable kind, which the catalogue deliberately has not.
    survivor = await seed_feed(db_session, connection["id"], "/photos")

    response = await client.delete(f"{INTEGRATIONS}/{created.json()['id']}")

    assert response.status_code == 204, response.text
    remaining = (await db_session.execute(select(FeedRow))).scalars().all()
    assert [row.id for row in remaining] == [survivor.id]


async def test_removing_an_integration_that_does_not_exist_is_a_404(
    data_root: Path, client: httpx.AsyncClient
) -> None:
    response = await client.delete(f"{INTEGRATIONS}/{uuid.uuid7()}")

    assert response.status_code == 404, response.text


async def test_removing_an_integration_frees_its_folder_for_another(
    data_root: Path, client: httpx.AsyncClient
) -> None:
    connection = await connect(client)
    body = {
        "kind": "wahoo",
        "transport": "cloud_folder",
        "connection_id": connection["id"],
        "remote_path": "/apps/wahoofitness",
    }
    created = await client.post(INTEGRATIONS, json=body)
    assert created.status_code == 201, created.text
    assert (await client.post(INTEGRATIONS, json=body)).status_code == 409

    assert (
        await client.delete(f"{INTEGRATIONS}/{created.json()['id']}")
    ).status_code == 204

    again = await client.post(INTEGRATIONS, json=body)
    assert again.status_code == 201, again.text

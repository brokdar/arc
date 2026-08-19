"""AC-3 to AC-5, AC-8, AC-9, AC-21 and AC-22: every source, over HTTP.

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
from tests.unit.dropbox_fake import (
    LIST_FOLDER_PATH,
    FakeDropbox,
    file_entry,
    folder_entry,
    rate_limited,
)

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


async def test_a_stored_app_key_reaches_the_catalogue_without_a_restart(
    data_root: Path, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings

    # A fresh install: no `DROPBOX__APP_KEY` line, nothing stored yet. The add
    # flow reads `storage[].app_configured` off the catalogue to decide
    # whether the registration checklist is still owed.
    monkeypatch.delenv("DROPBOX__APP_KEY", raising=False)
    get_settings.cache_clear()
    before = (await client.get(CATALOGUE_URL)).json()["storage"]
    assert [row["app_configured"] for row in before] == [False]

    stored = await client.put(
        "/api/v1/connections/dropbox/app", json={"app_key": "abc123def456"}
    )
    assert stored.status_code == 200, stored.text

    # The very next read, in the same process: a catalogue still answering
    # from the `Settings` object frozen at boot would keep showing the
    # registration checklist after the athlete finished it.
    after = (await client.get(CATALOGUE_URL)).json()["storage"]
    assert [row["app_configured"] for row in after] == [True]


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


# --- AC-21: discovery proposes integrations, not paths -----------------------


def discover_url(connection: dict[str, Any]) -> str:
    return f"/api/v1/connections/{connection['id']}/discover"


def tree(**folders: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """A Dropbox laid out by keyword: `root=[...], apps_wahoofitness=[...]`.

    The keyword is the path with `/` written as `_`, so a test reads as the
    directory tree it is describing rather than as a dict of strings.
    """
    return {
        ("" if name == "root" else "/" + name.replace("_", "/")): entries
        for name, entries in folders.items()
    }


#: What AC-21's fixture holds: `/Apps/WahooFitness` with three rides.
WAHOO_RIDES = [
    file_entry(
        "2026-08-14-ride.fit",
        "/apps/wahoofitness/2026-08-14-ride.fit",
        client_modified="2026-08-14T05:30:00Z",
    ),
    file_entry(
        "2026-08-15-ride.fit",
        "/apps/wahoofitness/2026-08-15-ride.fit",
        client_modified="2026-08-15T06:00:00Z",
    ),
    file_entry(
        "2026-08-16-ride.fit",
        "/apps/wahoofitness/2026-08-16-ride.fit",
        client_modified="2026-08-16T06:12:00Z",
    ),
]


async def test_discovery_names_the_integration_behind_the_folder_it_finds(
    data_root: Path, client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    fake.tree = tree(
        root=[folder_entry("Apps", "/apps"), folder_entry("Documents", "/documents")],
        apps=[folder_entry("WahooFitness", "/apps/wahoofitness")],
        apps_wahoofitness=WAHOO_RIDES,
        documents=[file_entry("plan.pdf", "/documents/plan.pdf")],
    )

    response = await client.get(discover_url(connection))

    assert response.status_code == 200, response.text
    body = response.json()
    # The whole answer: the athlete is offered Wahoo, by name, and nothing else.
    assert body["proposals"] == [
        {
            "kind": "wahoo",
            "display_name": "Wahoo",
            "connection_id": connection["id"],
            "transport": "cloud_folder",
            "path": "/apps/wahoofitness",
            "activity_files": 3,
            "newest_at": "2026-08-16T06:12:00Z",
            "configured": False,
        }
    ]


async def test_a_folder_holding_no_activity_files_is_absent_not_zero(
    data_root: Path, client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    fake.tree = tree(
        root=[folder_entry("Apps", "/apps"), folder_entry("Documents", "/documents")],
        apps=[folder_entry("WahooFitness", "/apps/wahoofitness")],
        apps_wahoofitness=WAHOO_RIDES,
        documents=[file_entry("plan.pdf", "/documents/plan.pdf")],
    )

    body = (await client.get(discover_url(connection))).json()

    # Not "/documents — 0 files": a folder holding no rides is not an answer to
    # "where are your rides", and listing it as one costs the athlete a read.
    assert [proposal["path"] for proposal in body["proposals"]] == [
        "/apps/wahoofitness"
    ]


async def test_a_folder_matching_no_catalogue_path_is_proposed_unnamed(
    data_root: Path, client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    fake.tree = tree(
        root=[folder_entry("Rides", "/rides")],
        rides=[
            file_entry("a.fit", "/rides/a.fit", client_modified="2026-08-10T07:00:00Z"),
            file_entry("b.fit", "/rides/b.fit", client_modified="2026-08-11T07:00:00Z"),
        ],
    )

    body = (await client.get(discover_url(connection))).json()

    # Nothing found is silently dropped, and nothing is guessed: the folder is
    # offered with no kind, and the athlete says which source it is.
    assert len(body["proposals"]) == 1
    proposal = body["proposals"][0]
    assert proposal["kind"] is None
    assert proposal["display_name"] == "/rides"
    assert proposal["activity_files"] == 2


async def test_an_activity_file_is_matched_whatever_its_case(
    data_root: Path, client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    fake.tree = tree(
        root=[folder_entry("Rides", "/rides")],
        rides=[file_entry("RIDE.FIT", "/rides/ride.fit")],
    )

    body = (await client.get(discover_url(connection))).json()

    # A Garmin writes `RIDE.FIT` in capitals; a case-sensitive match would tell
    # that athlete arc found nothing in a folder full of rides.
    assert body["proposals"][0]["activity_files"] == 1


async def test_only_the_extensions_arc_can_read_are_counted(
    data_root: Path, client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    fake.tree = tree(
        root=[folder_entry("Rides", "/rides")],
        rides=[
            file_entry("a.fit", "/rides/a.fit"),
            file_entry("b.gpx", "/rides/b.gpx"),
            file_entry("c.tcx", "/rides/c.tcx"),
            file_entry("d.json", "/rides/d.json"),
            file_entry("README", "/rides/readme"),
        ],
    )

    body = (await client.get(discover_url(connection))).json()

    # `.json` is an export and a file with no extension has nothing to dispatch
    # on, so neither is evidence that this is where the rides are.
    assert body["proposals"][0]["activity_files"] == 3


async def test_a_folder_holding_exactly_one_activity_file_is_proposed(
    data_root: Path, client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    fake.tree = tree(
        root=[folder_entry("Apps", "/apps")],
        apps=[folder_entry("WahooFitness", "/apps/wahoofitness")],
        apps_wahoofitness=[
            file_entry(
                "first.fit",
                "/apps/wahoofitness/first.fit",
                client_modified="2026-08-16T06:12:00Z",
            )
        ],
    )

    body = (await client.get(discover_url(connection))).json()

    # The athlete who has ridden once is exactly the athlete setting arc up.
    assert [proposal["path"] for proposal in body["proposals"]] == [
        "/apps/wahoofitness"
    ]
    assert body["proposals"][0]["activity_files"] == 1


async def test_proposals_order_by_count_then_by_recency(
    data_root: Path, client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    fake.tree = tree(
        root=[
            folder_entry("Few", "/few"),
            folder_entry("Stale", "/stale"),
            folder_entry("Fresh", "/fresh"),
        ],
        few=[file_entry("a.fit", "/few/a.fit", client_modified="2026-08-18T06:00:00Z")],
        stale=[
            file_entry("a.fit", "/stale/a.fit", client_modified="2026-01-02T06:00:00Z"),
            file_entry("b.fit", "/stale/b.fit", client_modified="2026-01-03T06:00:00Z"),
        ],
        fresh=[
            file_entry("a.fit", "/fresh/a.fit", client_modified="2026-08-15T06:00:00Z"),
            file_entry("b.fit", "/fresh/b.fit", client_modified="2026-08-16T06:00:00Z"),
        ],
    )

    body = (await client.get(discover_url(connection))).json()

    # Most files first; the tie between two folders holding two rides each goes
    # to the one the head unit touched this week.
    assert [proposal["path"] for proposal in body["proposals"]] == [
        "/fresh",
        "/stale",
        "/few",
    ]


async def test_a_paged_listing_is_followed_to_the_end_before_counting(
    data_root: Path, client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    rides = [
        file_entry(f"ride-{index}.fit", f"/rides/ride-{index}.fit")
        for index in range(7)
    ]
    fake.tree = tree(root=[folder_entry("Rides", "/rides")], rides=rides)
    # Dropbox serves a big folder in pages; stopping at the first one reports a
    # fraction of what is there as if it were the total.
    fake.tree_page_size = 2

    body = (await client.get(discover_url(connection))).json()

    assert body["proposals"][0]["activity_files"] == 7


async def test_the_probed_app_container_is_listed_once_and_counted_once(
    data_root: Path, client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    # `/Apps` is reached twice over: once as the container discovery probes for
    # the access-type inference, once as a folder of the root that might hold
    # rides. Both arrive under a different spelling — `/Apps` and `/apps`.
    fake.tree = tree(
        root=[folder_entry("Apps", "/apps")],
        apps=[file_entry("ride.fit", "/apps/ride.fit")],
    )

    body = (await client.get(discover_url(connection))).json()

    # One proposal holding one file: two proposals, or one claiming two, would
    # be arc counting the same ride twice and ranking a folder on the
    # duplicate.
    assert body["proposals"] == [
        {
            "kind": None,
            "display_name": "/apps",
            "connection_id": connection["id"],
            "transport": "cloud_folder",
            "path": "/apps",
            "activity_files": 1,
            "newest_at": "2026-01-01T00:00:00Z",
            "configured": False,
        }
    ]
    # And listed once: the probe's answer is reused rather than fetched again
    # under the other spelling, which would spend a request the rate limit
    # wants later to learn what arc already knows.
    listings = [
        call
        for call in fake.calls_to(LIST_FOLDER_PATH)
        if str((call.body or {}).get("path", "")).lower() == "/apps"
    ]
    assert len(listings) == 1


async def test_discovery_on_a_connection_needing_reauth_is_a_409(
    data_root: Path,
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    fake: FakeDropbox,
) -> None:
    connection = await connect(client)
    row = (await db_session.execute(select(ConnectionRow))).scalars().one()
    row.status = ConnectionStatus.NEEDS_REAUTH
    await db_session.commit()

    response = await client.get(discover_url(connection))

    assert response.status_code == 409, response.text
    # Refused locally: no point spending a request on a credential arc knows
    # is dead.
    assert fake.calls_to(LIST_FOLDER_PATH) == []


async def test_a_folder_already_collected_is_flagged_as_configured(
    data_root: Path,
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    fake: FakeDropbox,
) -> None:
    connection = await connect(client)
    await seed_classified_feed(
        db_session, connection["id"], IntegrationKind.WAHOO, "/apps/wahoofitness"
    )
    fake.tree = tree(
        root=[folder_entry("Apps", "/apps")],
        apps=[folder_entry("WahooFitness", "/apps/wahoofitness")],
        apps_wahoofitness=WAHOO_RIDES,
    )

    body = (await client.get(discover_url(connection))).json()

    # Reported rather than hidden: the athlete who is looking for their rides
    # is told arc already has them, which is the answer to the question they
    # asked. The panel renders no create control for it.
    assert len(body["proposals"]) == 1
    assert body["proposals"][0]["configured"] is True
    assert body["proposals"][0]["kind"] == "wahoo"


async def test_finding_nothing_is_an_empty_list_and_a_200(
    data_root: Path, client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    fake.tree = tree(
        root=[folder_entry("Documents", "/documents")],
        documents=[file_entry("plan.pdf", "/documents/plan.pdf")],
    )

    response = await client.get(discover_url(connection))

    # "arc looked and found no activity files" is a real answer, not a 404.
    assert response.status_code == 200, response.text
    assert response.json()["proposals"] == []


# --- AC-22: the App-folder diagnosis, in place of an empty tree --------------


async def test_an_empty_root_and_an_absent_apps_folder_suspect_app_folder(
    data_root: Path, client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    # An App-folder app sees only its own directory, so arc sees a Dropbox with
    # nothing in it at all — `/Apps` included.
    fake.tree = tree(root=[])

    body = (await client.get(discover_url(connection))).json()

    assert body["access_type_suspect"] == "app_folder"
    assert body["proposals"] == []


async def test_an_empty_dropbox_holding_apps_is_not_a_misconfiguration(
    data_root: Path, client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    fake.tree = tree(root=[folder_entry("Apps", "/apps")], apps=[])

    body = (await client.get(discover_url(connection))).json()

    # Accusing this athlete would send them to re-register a working app.
    assert body["access_type_suspect"] is None


async def test_a_populated_root_without_apps_is_not_a_misconfiguration(
    data_root: Path, client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    fake.tree = tree(
        root=[folder_entry("Documents", "/documents")],
        documents=[],
    )

    body = (await client.get(discover_url(connection))).json()

    # arc can see the whole Dropbox; there is simply no `/Apps` in it.
    assert body["access_type_suspect"] is None


async def test_a_throttled_apps_probe_infers_nothing(
    data_root: Path, client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    fake.tree = tree(root=[])
    fake.list_failures["/Apps"] = rate_limited()

    response = await client.get(discover_url(connection))

    # A 429 is Dropbox being busy, not a statement about what arc may see.
    assert response.status_code == 200, response.text
    assert response.json()["access_type_suspect"] is None

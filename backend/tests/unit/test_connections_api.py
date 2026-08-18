"""AC-1, AC-3, AC-5, AC-6 and AC-7: the connect ritual over HTTP.

Everything here is asserted on the artifact the criterion names — the query
string arc renders, the row it wrote, the bytes in the credential column, the
status code — never on a helper's return value. The one thing that cannot be
seen from a response body, that the refresh token never reaches a log line, is
asserted with `structlog.testing.capture_logs`.
"""

import base64
import datetime as dt
import hashlib
import json
import uuid
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from app.connectors import dropbox
from app.connectors.dropbox import READ_SCOPES
from app.core.config import get_settings
from app.domain.connections import ConnectionStatus
from app.persistence.connections import (
    ConnectionRow,
    FeedRow,
    OAuthAuthorizationRow,
)
from tests.unit.dropbox_fake import (
    LIST_FOLDER_CONTINUE_PATH,
    LIST_FOLDER_PATH,
    REVOKE_PATH,
    TOKEN_PATH,
    FakeDropbox,
    file_entry,
    folder_entry,
    page,
    path_not_found,
    rate_limited,
)

pytestmark = pytest.mark.usefixtures("dropbox_env")

AUTHORIZE = "/api/v1/connections/dropbox/authorize"
COMPLETE = "/api/v1/connections/dropbox/complete"
CONNECTIONS = "/api/v1/connections"
FEEDS = "/api/v1/feeds"


@pytest.fixture(autouse=True)
def fake() -> Iterator[FakeDropbox]:
    """Dropbox, faked, for every test in this module.

    Autouse rather than opt-in: a test that forgot to ask for it would make a
    real request to dropbox.com from the unit suite, and the first sign of it
    is a suite that fails on a machine with no network.
    """
    upstream = FakeDropbox()
    dropbox.set_transport(upstream.transport)
    yield upstream
    dropbox.set_transport(None)


def query_of(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


def challenge_for(verifier: str) -> str:
    """base64url, unpadded, of the SHA-256 of the verifier — RFC 7636 S256."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


async def stored_verifier(session: AsyncSession) -> str:
    rows = (await session.execute(select(OAuthAuthorizationRow))).scalars().all()
    assert len(rows) == 1, f"expected one authorization row, found {len(rows)}"
    return rows[0].code_verifier


async def connect(api: httpx.AsyncClient) -> dict[str, Any]:
    """Run the whole paste-the-code ritual and return the connection."""
    started = await api.post(AUTHORIZE)
    assert started.status_code == 200, started.text
    completed = await api.post(COMPLETE, json={"code": "pasted-code"})
    assert completed.status_code == 201, completed.text
    return completed.json()


async def count_of(session: AsyncSession, model: type[Any]) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


# --- AC-1: the authorization URL ---------------------------------------------


async def test_the_authorize_url_carries_the_pkce_challenge_for_the_stored_verifier(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post(AUTHORIZE)

    assert response.status_code == 200, response.text
    query = query_of(response.json()["authorize_url"])
    assert query["client_id"] == ["test-app-key"]
    assert query["response_type"] == ["code"]
    assert query["token_access_type"] == ["offline"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [challenge_for(await stored_verifier(db_session))]


async def test_the_authorize_url_asks_for_no_redirect(
    client: httpx.AsyncClient,
) -> None:
    # PKCE with a pasted code is what lets arc live behind a home router with
    # no registered redirect URI. A `redirect_uri` appearing here would pin the
    # deployment to one origin and break the moment arc is reached by LAN IP.
    response = await client.post(AUTHORIZE)

    assert "redirect_uri" not in query_of(response.json()["authorize_url"])


async def test_the_verifier_is_never_in_the_response_body(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post(AUTHORIZE)

    assert (await stored_verifier(db_session)) not in response.text


async def test_an_empty_app_key_is_a_422_naming_the_setting(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DROPBOX__APP_KEY", "")
    get_settings.cache_clear()

    response = await client.post(AUTHORIZE)

    assert response.status_code == 422, response.text
    assert "DROPBOX__APP_KEY" in response.json()["detail"]


async def test_a_second_authorize_supersedes_the_first_verifier(
    client: httpx.AsyncClient, db_session: AsyncSession, fake: FakeDropbox
) -> None:
    await client.post(AUTHORIZE)
    first = await stored_verifier(db_session)
    await client.post(AUTHORIZE)
    second = await stored_verifier(db_session)
    assert second != first
    # Dropbox only accepts the verifier the code was minted against.
    fake.expected_verifier = first

    response = await client.post(COMPLETE, json={"code": "code-for-the-first-flow"})

    assert response.status_code == 422, response.text
    assert "already been used" in response.json()["detail"]
    assert await count_of(db_session, ConnectionRow) == 0


async def test_an_authorization_older_than_fifteen_minutes_is_refused_and_removed(
    client: httpx.AsyncClient, db_session: AsyncSession, fake: FakeDropbox
) -> None:
    await client.post(AUTHORIZE)
    row = (await db_session.execute(select(OAuthAuthorizationRow))).scalars().one()
    row.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=16)
    row.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    await db_session.commit()

    response = await client.post(COMPLETE, json={"code": "too-late"})

    assert response.status_code == 422, response.text
    assert "expired" in response.json()["detail"]
    assert await count_of(db_session, OAuthAuthorizationRow) == 0
    # Nothing was even offered to Dropbox: the flow is over locally.
    assert fake.calls_to(TOKEN_PATH) == []


# --- AC-3: exchanging the pasted code ----------------------------------------


async def test_completing_posts_the_pkce_exchange_and_stores_one_connection(
    client: httpx.AsyncClient, db_session: AsyncSession, fake: FakeDropbox
) -> None:
    started = await client.post(AUTHORIZE)
    assert started.status_code == 200
    verifier = await stored_verifier(db_session)

    response = await client.post(COMPLETE, json={"code": "pasted-code"})

    assert response.status_code == 201, response.text
    exchange = fake.calls_to(TOKEN_PATH)[0].form
    assert exchange["grant_type"] == "authorization_code"
    assert exchange["code"] == "pasted-code"
    assert exchange["code_verifier"] == verifier
    assert exchange["client_id"] == "test-app-key"
    assert "client_secret" not in exchange

    row = (await db_session.execute(select(ConnectionRow))).scalars().one()
    assert row.status is ConnectionStatus.CONNECTED
    assert row.account_label == "Ada Lovelace (ada@example.com)"
    assert set(row.scopes) == set(READ_SCOPES)
    body = response.json()
    assert body["status"] == "connected"
    assert body["account_label"] == "Ada Lovelace (ada@example.com)"
    assert sorted(body["scopes"]) == sorted(READ_SCOPES)
    assert body["feeds"] == []
    # The one-time authorization is spent.
    assert await count_of(db_session, OAuthAuthorizationRow) == 0


async def test_the_refresh_token_is_in_neither_the_column_nor_the_body_nor_a_log(
    client: httpx.AsyncClient, db_session: AsyncSession, fake: FakeDropbox
) -> None:
    fake.refresh_token = "s3cret-refresh-token"
    await client.post(AUTHORIZE)

    with capture_logs() as logs:
        response = await client.post(COMPLETE, json={"code": "pasted-code"})

    assert response.status_code == 201, response.text
    row = (await db_session.execute(select(ConnectionRow))).scalars().one()
    assert b"s3cret-refresh-token" not in row.credentials
    assert "s3cret-refresh-token" not in response.text
    assert "s3cret-refresh-token" not in json.dumps(logs)


async def test_a_pasted_code_with_whitespace_is_accepted(
    client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    await client.post(AUTHORIZE)

    response = await client.post(COMPLETE, json={"code": "  pasted-code\n"})

    assert response.status_code == 201, response.text
    assert fake.calls_to(TOKEN_PATH)[0].form["code"] == "pasted-code"


async def test_an_invalid_grant_is_a_422_and_writes_no_row(
    client: httpx.AsyncClient, db_session: AsyncSession, fake: FakeDropbox
) -> None:
    await client.post(AUTHORIZE)
    fake.token_error = "invalid_grant"

    response = await client.post(COMPLETE, json={"code": "stale-code"})

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "already been used" in detail
    assert "expired" in detail
    assert await count_of(db_session, ConnectionRow) == 0


async def test_a_second_connection_is_a_409_naming_disconnect(
    client: httpx.AsyncClient, db_session: AsyncSession, fake: FakeDropbox
) -> None:
    first = await connect(client)
    await client.post(AUTHORIZE)
    fake.display_name = "Someone Else"

    response = await client.post(COMPLETE, json={"code": "second-code"})

    assert response.status_code == 409, response.text
    assert "disconnect" in response.json()["detail"].lower()
    row = (await db_session.execute(select(ConnectionRow))).scalars().one()
    assert str(row.id) == first["id"]
    assert row.account_label == "Ada Lovelace (ada@example.com)"


async def test_a_grant_without_a_refresh_token_names_offline_access(
    client: httpx.AsyncClient, db_session: AsyncSession, fake: FakeDropbox
) -> None:
    await client.post(AUTHORIZE)
    fake.refresh_token = None

    response = await client.post(COMPLETE, json={"code": "online-only"})

    assert response.status_code == 422, response.text
    assert "token_access_type=offline" in response.json()["detail"]
    assert await count_of(db_session, ConnectionRow) == 0


# --- AC-5: the folder picker -------------------------------------------------


async def test_folders_lists_only_folders_with_their_path_and_name(
    client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)

    response = await client.get(f"{CONNECTIONS}/{connection['id']}/folders?path=")

    assert response.status_code == 200, response.text
    assert response.json()["items"] == [
        {"path_lower": "/apps", "name": "Apps"},
        {"path_lower": "/photos", "name": "Photos"},
    ]
    # `path=""` is the Dropbox root, and Dropbox spells that as an empty path.
    assert fake.calls_to(LIST_FOLDER_PATH)[0].body["path"] == ""


async def test_a_folder_holding_only_files_is_an_empty_list_not_a_404(
    client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    fake.pages = [page(file_entry("ride.fit", "/apps/ride.fit"))]

    response = await client.get(f"{CONNECTIONS}/{connection['id']}/folders?path=/apps")

    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


async def test_folders_follows_the_cursor_until_dropbox_is_exhausted(
    client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    fake.pages = [
        page(folder_entry("One", "/one"), cursor="page-2", has_more=True),
        page(folder_entry("Two", "/two"), cursor="page-2", has_more=False),
    ]

    response = await client.get(f"{CONNECTIONS}/{connection['id']}/folders?path=")

    assert [item["path_lower"] for item in response.json()["items"]] == ["/one", "/two"]
    assert len(fake.calls_to(LIST_FOLDER_CONTINUE_PATH)) == 1
    assert fake.calls_to(LIST_FOLDER_CONTINUE_PATH)[0].body["cursor"] == "page-2"


async def test_a_path_dropbox_does_not_have_is_a_404_naming_it(
    client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    fake.script(LIST_FOLDER_PATH, path_not_found("/nope"))

    response = await client.get(f"{CONNECTIONS}/{connection['id']}/folders?path=/nope")

    assert response.status_code == 404, response.text
    assert "/nope" in response.json()["detail"]


async def test_folders_on_a_connection_needing_reauth_is_a_409(
    client: httpx.AsyncClient, db_session: AsyncSession, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    row = (await db_session.execute(select(ConnectionRow))).scalars().one()
    row.status = ConnectionStatus.NEEDS_REAUTH
    await db_session.commit()

    response = await client.get(f"{CONNECTIONS}/{connection['id']}/folders?path=")

    assert response.status_code == 409, response.text
    assert "re-authoris" in response.json()["detail"].lower()
    # Refused locally: no point spending a request on a credential arc knows
    # is dead.
    assert fake.calls_to(LIST_FOLDER_PATH) == []


# --- folder discovery: ranking what is already there -------------------------
#
# `dropbox-setup-in-app` AC-5 and AC-6. Numbered against that plan, not against
# the AC-1..AC-7 sections above, which belong to the connector's own build.


def discovery_of(response: httpx.Response) -> list[dict[str, Any]]:
    assert response.status_code == 200, response.text
    return list(response.json()["candidates"])


async def discover(
    api: httpx.AsyncClient, connection: dict[str, Any]
) -> httpx.Response:
    return await api.get(f"{CONNECTIONS}/{connection['id']}/discover")


#: A Dropbox with the rides where a Wahoo head unit actually puts them.
WAHOO_TREE: dict[str, list[dict[str, Any]]] = {
    "": [folder_entry("Apps", "/apps"), folder_entry("Documents", "/documents")],
    "/apps": [folder_entry("WahooFitness", "/apps/wahoofitness")],
    "/apps/wahoofitness": [
        file_entry(
            "2026-08-14.fit",
            "/apps/wahoofitness/2026-08-14.fit",
            client_modified="2026-08-14T09:00:00Z",
        ),
        file_entry(
            "2026-08-15.fit",
            "/apps/wahoofitness/2026-08-15.fit",
            client_modified="2026-08-15T09:00:00Z",
        ),
        file_entry(
            "2026-08-16.fit",
            "/apps/wahoofitness/2026-08-16.fit",
            client_modified="2026-08-16T05:30:00Z",
        ),
    ],
    "/documents": [file_entry("taxes.pdf", "/documents/taxes.pdf")],
}


async def test_discovery_ranks_the_folder_the_activity_files_are_in_first(
    client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    fake.tree = WAHOO_TREE

    response = await discover(client, connection)

    candidates = discovery_of(response)
    assert candidates[0]["path"] == "/apps/wahoofitness"
    assert candidates[0]["activity_files"] == 3
    assert candidates[0]["newest_at"].startswith("2026-08-16T")
    # Absent, not present with a zero: a folder with nothing arc can read is
    # not a worse answer to "where are your rides", it is not an answer.
    assert "/documents" not in [candidate["path"] for candidate in candidates]


async def test_an_uppercase_extension_is_still_an_activity_file(
    client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    fake.tree = {
        "": [folder_entry("Rides", "/rides")],
        "/rides": [file_entry("RIDE.FIT", "/rides/ride.fit")],
    }

    response = await discover(client, connection)

    assert discovery_of(response)[0]["activity_files"] == 1


async def test_gpx_and_tcx_count_and_json_and_an_extensionless_file_do_not(
    client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    fake.tree = {
        "": [folder_entry("Rides", "/rides")],
        "/rides": [
            file_entry("ride.gpx", "/rides/ride.gpx"),
            file_entry("ride.tcx", "/rides/ride.tcx"),
            file_entry("summary.json", "/rides/summary.json"),
            file_entry("README", "/rides/readme"),
        ],
    }

    response = await discover(client, connection)

    assert discovery_of(response)[0]["activity_files"] == 2


async def test_one_activity_file_is_enough_to_be_a_candidate(
    client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    fake.tree = {
        "": [folder_entry("Rides", "/rides")],
        # One file is the boundary the filter is stated at: a folder that has
        # collected a single ride is the folder a new head unit writes to.
        "/rides": [file_entry("ride.fit", "/rides/ride.fit")],
    }

    response = await discover(client, connection)

    assert discovery_of(response) == [
        {
            "path": "/rides",
            "activity_files": 1,
            "newest_at": "2026-01-01T00:00:00Z",
        }
    ]


async def test_the_folder_holding_more_rides_outranks_the_one_holding_fewer(
    client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    fake.tree = {
        # Listed smallest-first, and the smaller folder also holds the newer
        # file: neither the order Dropbox answered in nor the tie-break can
        # produce the expected ranking on its own, so only a descending count
        # as the *primary* key puts `/archive` first.
        "": [folder_entry("Inbox", "/inbox"), folder_entry("Archive", "/archive")],
        "/inbox": [
            file_entry(
                "ride.fit", "/inbox/ride.fit", client_modified="2026-08-16T10:00:00Z"
            )
        ],
        "/archive": [
            file_entry(
                f"2024-03-0{day}.fit",
                f"/archive/2024-03-0{day}.fit",
                client_modified=f"2024-03-0{day}T10:00:00Z",
            )
            for day in (1, 2, 3)
        ],
    }

    response = await discover(client, connection)

    # Where the rides *are* is the question. A folder with three of them is the
    # answer even when a folder with one was written to more recently — the
    # head unit's folder is the one that has been filling up.
    assert discovery_of(response) == [
        {
            "path": "/archive",
            "activity_files": 3,
            "newest_at": "2024-03-03T10:00:00Z",
        },
        {
            "path": "/inbox",
            "activity_files": 1,
            "newest_at": "2026-08-16T10:00:00Z",
        },
    ]


async def test_folders_holding_as_much_are_ordered_by_the_newest_file(
    client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    fake.tree = {
        "": [folder_entry("Old", "/old"), folder_entry("Recent", "/recent")],
        "/old": [
            file_entry(
                "ride.fit", "/old/ride.fit", client_modified="2024-03-01T10:00:00Z"
            )
        ],
        "/recent": [
            file_entry(
                "ride.fit", "/recent/ride.fit", client_modified="2026-08-16T10:00:00Z"
            )
        ],
    }

    response = await discover(client, connection)

    # Same count, so the tie is broken by which folder is still in use — the
    # one holding a ride from two years ago is an archive, not a feed.
    assert [candidate["path"] for candidate in discovery_of(response)] == [
        "/recent",
        "/old",
    ]


async def test_a_paged_listing_is_followed_to_the_end_before_counting(
    client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    fake.tree = {
        "": [folder_entry("Rides", "/rides")],
        "/rides": [
            file_entry(f"ride-{index}.fit", f"/rides/ride-{index}.fit")
            for index in range(5)
        ],
    }
    fake.tree_page_size = 2

    response = await discover(client, connection)

    # Stopping at the first page would report 2 of 5 and rank a busy folder
    # below a quiet one that happened to fit in a single page.
    assert discovery_of(response)[0]["activity_files"] == 5
    assert fake.calls_to(LIST_FOLDER_CONTINUE_PATH) != []


async def test_a_dropbox_with_no_activity_files_is_an_empty_list_not_a_404(
    client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    fake.tree = {
        "": [folder_entry("Photos", "/photos")],
        "/photos": [file_entry("beach.jpg", "/photos/beach.jpg")],
        "/apps": [],
    }

    response = await discover(client, connection)

    assert response.status_code == 200, response.text
    assert response.json() == {"candidates": [], "access_type_suspect": None}


async def test_an_empty_root_beside_a_missing_apps_folder_names_the_app_type(
    client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    # What an App-folder app sees: its own folder, which arc is not in, and
    # therefore nothing at all — including `/Apps`, which certainly exists.
    fake.tree = {"": []}

    response = await discover(client, connection)

    assert response.status_code == 200, response.text
    assert response.json()["access_type_suspect"] == "app_folder"


async def test_empty_dropbox_is_not_diagnosed_as_app_folder(
    client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    # `/Apps` answers, so arc is not being walled off — this Dropbox is simply
    # empty, and accusing the athlete of a misconfiguration they do not have
    # sends them to delete a perfectly good Dropbox app.
    fake.tree = {"": [], "/apps": []}

    response = await discover(client, connection)

    assert response.json()["access_type_suspect"] is None


async def test_a_root_with_folders_and_no_apps_is_not_diagnosed(
    client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    # Plenty visible and no `/Apps` at all: an athlete who has never installed
    # a Dropbox-linked app. Full access, nothing wrong.
    fake.tree = {"": [folder_entry("Photos", "/photos")], "/photos": []}

    response = await discover(client, connection)

    assert response.json()["access_type_suspect"] is None


async def test_an_apps_probe_that_is_rate_limited_draws_no_inference(
    client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    fake.tree = {"": []}
    fake.list_failures["/apps"] = rate_limited()

    response = await discover(client, connection)

    # A 429 is Dropbox being busy. It says nothing about what arc is allowed
    # to see, and an inference drawn from an outage would tell the athlete to
    # delete their Dropbox app over a bad minute.
    assert response.status_code == 200, response.text
    assert response.json()["access_type_suspect"] is None


async def test_the_probed_app_container_is_listed_once_and_counted_once(
    client: httpx.AsyncClient, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    # `/Apps` is reached twice over: once as the container discovery probes for
    # the access-type inference, once as a folder of the root that might hold
    # rides. Both arrive under a different spelling — `/Apps` and `/apps`.
    fake.tree = {
        "": [folder_entry("Apps", "/apps")],
        "/apps": [file_entry("ride.fit", "/apps/ride.fit")],
    }

    response = await discover(client, connection)

    # One row holding one file: two rows, or one claiming two, would be arc
    # counting the same ride twice and ranking a folder on the duplicate.
    assert discovery_of(response) == [
        {"path": "/apps", "activity_files": 1, "newest_at": "2026-01-01T00:00:00Z"}
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
    client: httpx.AsyncClient, db_session: AsyncSession, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    row = (await db_session.execute(select(ConnectionRow))).scalars().one()
    row.status = ConnectionStatus.NEEDS_REAUTH
    await db_session.commit()

    response = await discover(client, connection)

    assert response.status_code == 409, response.text
    assert fake.calls_to(LIST_FOLDER_PATH) == []


# --- AC-6: feeds -------------------------------------------------------------


async def test_a_new_feed_starts_enabled_with_no_cursor_and_no_delivery(
    client: httpx.AsyncClient,
) -> None:
    connection = await connect(client)

    response = await client.post(
        FEEDS,
        json={"connection_id": connection["id"], "remote_path": "/Apps/WahooFitness"},
    )

    assert response.status_code == 201, response.text
    feed = response.json()
    assert feed["enabled"] is True
    assert feed["cursor"] is None
    assert feed["last_delivery_at"] is None
    assert feed["remote_path"] == "/apps/wahoofitness"


async def test_the_same_folder_in_another_spelling_is_one_feed_and_a_409(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    connection = await connect(client)
    first = await client.post(
        FEEDS,
        json={"connection_id": connection["id"], "remote_path": "/Apps/WahooFitness/"},
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        FEEDS,
        json={"connection_id": connection["id"], "remote_path": "/apps/wahoofitness"},
    )

    assert second.status_code == 409, second.text
    assert await count_of(db_session, FeedRow) == 1
    row = (await db_session.execute(select(FeedRow))).scalars().one()
    assert row.remote_path == "/apps/wahoofitness"


async def test_patch_flips_enabled_and_delete_removes_the_feed(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    connection = await connect(client)
    created = await client.post(
        FEEDS, json={"connection_id": connection["id"], "remote_path": "/apps"}
    )
    feed_id = created.json()["id"]

    patched = await client.patch(f"{FEEDS}/{feed_id}", json={"enabled": False})
    assert patched.status_code == 200, patched.text
    assert patched.json()["enabled"] is False

    deleted = await client.delete(f"{FEEDS}/{feed_id}")
    assert deleted.status_code == 204, deleted.text
    assert await count_of(db_session, FeedRow) == 0


async def test_a_feed_on_an_unknown_connection_is_a_404_and_writes_nothing(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post(
        FEEDS, json={"connection_id": str(uuid.uuid7()), "remote_path": "/apps"}
    )

    assert response.status_code == 404, response.text
    assert await count_of(db_session, FeedRow) == 0


async def test_the_dropbox_root_is_a_legal_feed_path(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    connection = await connect(client)

    response = await client.post(
        FEEDS, json={"connection_id": connection["id"], "remote_path": ""}
    )

    assert response.status_code == 201, response.text
    assert response.json()["remote_path"] == ""
    row = (await db_session.execute(select(FeedRow))).scalars().one()
    assert row.remote_path == ""


# --- AC-7: disconnecting -----------------------------------------------------


async def test_disconnecting_revokes_upstream_and_removes_the_row(
    client: httpx.AsyncClient, db_session: AsyncSession, fake: FakeDropbox
) -> None:
    connection = await connect(client)

    response = await client.delete(f"{CONNECTIONS}/{connection['id']}")

    assert response.status_code == 204, response.text
    assert len(fake.calls_to(REVOKE_PATH)) == 1
    assert await count_of(db_session, ConnectionRow) == 0
    gone = await client.get(f"{CONNECTIONS}/{connection['id']}")
    assert gone.status_code == 404, gone.text


async def test_a_revoke_that_never_reaches_dropbox_still_deletes_the_credential(
    client: httpx.AsyncClient, db_session: AsyncSession, fake: FakeDropbox
) -> None:
    connection = await connect(client)
    fake.raises[REVOKE_PATH] = httpx.ConnectError("dropbox is unreachable")

    with capture_logs() as logs:
        response = await client.delete(f"{CONNECTIONS}/{connection['id']}")

    assert response.status_code == 204, response.text
    assert await count_of(db_session, ConnectionRow) == 0
    assert any(entry.get("event") == "dropbox_revoke_failed" for entry in logs), logs


async def test_disconnecting_twice_is_a_404(client: httpx.AsyncClient) -> None:
    connection = await connect(client)
    assert (await client.delete(f"{CONNECTIONS}/{connection['id']}")).status_code == 204

    second = await client.delete(f"{CONNECTIONS}/{connection['id']}")

    assert second.status_code == 404, second.text


async def test_disconnecting_takes_every_feed_with_it(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    connection = await connect(client)
    for path in ("/apps/wahoo", "/apps/healthfit", "/apps/zwift"):
        created = await client.post(
            FEEDS, json={"connection_id": connection["id"], "remote_path": path}
        )
        assert created.status_code == 201, created.text
    assert await count_of(db_session, FeedRow) == 3

    response = await client.delete(f"{CONNECTIONS}/{connection['id']}")

    assert response.status_code == 204, response.text
    assert await count_of(db_session, FeedRow) == 0


# --- the collection read the panel is built on -------------------------------


async def test_connections_lists_the_connection_with_its_feeds(
    client: httpx.AsyncClient,
) -> None:
    connection = await connect(client)
    await client.post(
        FEEDS, json={"connection_id": connection["id"], "remote_path": "/apps/wahoo"}
    )

    response = await client.get(CONNECTIONS)

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["provider"] == "dropbox"
    assert [feed["remote_path"] for feed in items[0]["feeds"]] == ["/apps/wahoo"]


async def test_connections_is_empty_before_anything_is_connected(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(CONNECTIONS)

    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


async def test_every_connection_route_needs_a_session(
    anon_client: httpx.AsyncClient,
) -> None:
    assert (await anon_client.get(CONNECTIONS)).status_code == 401
    assert (await anon_client.post(AUTHORIZE)).status_code == 401
    assert (await anon_client.post(FEEDS, json={})).status_code == 401


# --- found by Schemathesis: a body that does not parse -----------------------
#
# FastAPI answers **400** — "There was an error parsing the body" — when a
# request body is not JSON at all, where a body that parses but breaks a rule
# is 422. The fuzzer sends both, and the three connect operations that take a
# body documented only the second, so the fuzz job failed on an undocumented
# status code. Every other body-taking route in this API already declares that
# 400 (`BAD_BODY`); the sweep that now holds the whole surface to it is
# `test_error_envelope.py::test_every_json_body_operation_documents_a_bad_body`.


async def test_a_body_that_is_not_json_is_a_documented_400(
    client: httpx.AsyncClient,
) -> None:
    connection = await connect(client)
    created = await client.post(
        FEEDS, json={"connection_id": connection["id"], "remote_path": "/apps/wahoo"}
    )
    assert created.status_code == 201, created.text
    feed_id = created.json()["id"]
    spec = (await client.get("/openapi.json")).json()

    for method, url, operation in (
        ("POST", COMPLETE, "/api/v1/connections/dropbox/complete"),
        ("POST", FEEDS, "/api/v1/feeds"),
        ("PATCH", f"{FEEDS}/{feed_id}", "/api/v1/feeds/{feed_id}"),
    ):
        response = await client.request(
            method,
            url,
            content=b"\xa0\xa1not json",
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 400, f"{method} {url}: {response.text}"
        assert isinstance(response.json()["detail"], str)
        documented = spec["paths"][operation][method.lower()]["responses"]
        assert "400" in documented, f"{method} {operation} documents {list(documented)}"

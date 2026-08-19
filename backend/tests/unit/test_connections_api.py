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
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import sqlalchemy as sa
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
from app.persistence.integrations import IntegrationRow
from app.persistence.types import JSONColumn
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
)

pytestmark = pytest.mark.usefixtures("dropbox_env")

AUTHORIZE = "/api/v1/connections/dropbox/authorize"
COMPLETE = "/api/v1/connections/dropbox/complete"
CONNECTIONS = "/api/v1/connections"
INTEGRATIONS = "/api/v1/integrations"
#: Retired by this PR. Kept as a constant so the tests that prove it is gone
#: name the same path the panel used to call.
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


# --- AC-10: `/feeds` is gone, and the integration owns its folders -----------
#
# `POST /api/v1/feeds` created a `FeedRow` with nothing recording what the
# folder brings in — the exact folder-shaped configuration the integrations
# surface exists to replace. Retired rather than deprecated: one write path
# that produces rows the panel cannot describe is one too many, and the pause,
# resume and remove it also carried are reachable through the integration.


async def test_the_retired_feed_routes_are_gone(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    connection = await connect(client)
    feed = FeedRow(
        connection_id=uuid.UUID(connection["id"]), remote_path="/apps/wahoofitness"
    )
    db_session.add(feed)
    await db_session.commit()

    created = await client.post(
        FEEDS,
        json={"connection_id": connection["id"], "remote_path": "/apps/wahoofitness"},
    )
    patched = await client.patch(f"{FEEDS}/{feed.id}", json={"enabled": False})
    deleted = await client.delete(f"{FEEDS}/{feed.id}")

    for response in (created, patched, deleted):
        assert response.status_code in {404, 405}, response.text
    # And nothing happened to the folder that was already there.
    await db_session.refresh(feed)
    assert feed.enabled is True
    assert await count_of(db_session, FeedRow) == 1


async def test_the_openapi_schema_no_longer_publishes_the_feed_operations(
    client: httpx.AsyncClient,
) -> None:
    spec = (await client.get("/openapi.json")).json()

    # The committed frontend types are generated from this document, so a
    # retired operation that lingers here is a hook the panel can still call.
    assert "/api/v1/feeds" not in spec["paths"]
    assert "/api/v1/feeds/{feed_id}" not in spec["paths"]


async def test_removing_an_integrations_last_folder_removes_the_integration(
    client: httpx.AsyncClient, db_session: AsyncSession
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
    integration_id = created.json()["id"]
    second = await client.post(
        INTEGRATIONS,
        json={
            "kind": "wahoo",
            "transport": "cloud_folder",
            "connection_id": connection["id"],
            "remote_path": "/apps/spare",
        },
    )
    assert second.status_code == 200, second.text
    folders = {row["remote_path"]: row["feed_id"] for row in second.json()["folders"]}

    first_removed = await client.delete(
        f"{INTEGRATIONS}/{integration_id}/folders/{folders['/apps/spare']}"
    )
    assert first_removed.status_code == 204, first_removed.text
    assert await count_of(db_session, IntegrationRow) == 1

    last_removed = await client.delete(
        f"{INTEGRATIONS}/{integration_id}/folders/{folders['/apps/wahoofitness']}"
    )

    # No integration ever exists with zero transports: an entry arc claims to
    # collect from and has no way to reach is worse than no entry at all.
    assert last_removed.status_code == 204, last_removed.text
    assert await count_of(db_session, IntegrationRow) == 0
    assert await count_of(db_session, FeedRow) == 0


async def test_pausing_and_resuming_a_folder_goes_through_the_integration(
    client: httpx.AsyncClient, db_session: AsyncSession
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
    integration_id = created.json()["id"]
    folder_id = created.json()["folders"][0]["feed_id"]
    url = f"{INTEGRATIONS}/{integration_id}/folders/{folder_id}"

    paused = await client.patch(url, json={"enabled": False})

    assert paused.status_code == 200, paused.text
    assert paused.json()["folders"][0]["enabled"] is False
    assert paused.json()["folders"][0]["state"] == "paused"
    resumed = await client.patch(url, json={"enabled": True})
    assert resumed.json()["folders"][0]["enabled"] is True
    # The cursor survived the pause, which is why this is a flag not a delete.
    row = (await db_session.execute(select(FeedRow))).scalars().one()
    assert row.enabled is True


async def test_a_folder_addressed_under_the_wrong_integration_is_a_404(
    client: httpx.AsyncClient,
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
    folder_id = created.json()["folders"][0]["feed_id"]

    response = await client.delete(f"{INTEGRATIONS}/{uuid.uuid7()}/folders/{folder_id}")

    assert response.status_code == 404, response.text


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
        db_session.add(
            FeedRow(connection_id=uuid.UUID(connection["id"]), remote_path=path)
        )
    await db_session.commit()
    assert await count_of(db_session, FeedRow) == 3

    response = await client.delete(f"{CONNECTIONS}/{connection['id']}")

    assert response.status_code == 204, response.text
    assert await count_of(db_session, FeedRow) == 0


# --- AC-11: disconnecting takes what only existed through the account --------


async def test_disconnecting_removes_an_integration_that_lived_only_there(
    client: httpx.AsyncClient, db_session: AsyncSession
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

    response = await client.delete(f"{CONNECTIONS}/{connection['id']}")

    assert response.status_code == 204, response.text
    # Not an entry in Settings with nothing behind it: the account it collected
    # through is gone, so the source it named is gone too.
    assert await count_of(db_session, IntegrationRow) == 0
    assert await count_of(db_session, FeedRow) == 0


async def test_disconnecting_leaves_the_local_drop_alone(
    data_root: Path, client: httpx.AsyncClient
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

    assert (await client.delete(f"{CONNECTIONS}/{connection['id']}")).status_code == 204

    items = (await client.get(INTEGRATIONS)).json()["items"]
    # The local drop is synthesized from settings and owes nothing to a
    # credential — it keeps sweeping and keeps its entry.
    assert [item["kind"] for item in items] == ["local_drop"]
    assert items[0]["local"]["inbox_path"] == str((data_root / "inbox").resolve())


async def test_an_integration_with_a_folder_on_another_account_survives(
    client: httpx.AsyncClient, db_session: AsyncSession
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
    integration_id = uuid.UUID(created.json()["id"])
    # A second storage account, written directly: `connections` is unique on
    # provider, so two accounts mean two providers, and the column is a plain
    # VARCHAR with no CHECK (`enum_column`). Six characters, because that
    # column is sized to the longest member value — seven today.
    elsewhere_id = uuid.uuid7()
    # Written through a bare Core table rather than the mapped class: the ORM
    # enum validates on the way in and `gdrive` is not a member yet, while
    # the column itself is a plain VARCHAR with no CHECK (`enum_column`).
    await db_session.execute(
        sa.table(
            "connections",
            sa.column("id", sa.Uuid),
            sa.column("provider", sa.String),
            sa.column("status", sa.String),
            sa.column("scopes", JSONColumn),
            sa.column("credentials", sa.LargeBinary),
        )
        .insert()
        .values(
            id=elsewhere_id,
            provider="gdrive",
            status="connected",
            scopes=[],
            credentials=b"\x00",
        )
    )
    db_session.add(
        FeedRow(
            connection_id=elsewhere_id,
            integration_id=integration_id,
            remote_path="/backup/wahoo",
        )
    )
    await db_session.commit()

    response = await client.delete(f"{CONNECTIONS}/{connection['id']}")

    assert response.status_code == 204, response.text
    assert await count_of(db_session, IntegrationRow) == 1
    remaining = (await db_session.execute(select(FeedRow))).scalars().all()
    assert [row.remote_path for row in remaining] == ["/backup/wahoo"]


# --- the collection read the panel is built on -------------------------------


async def test_connections_lists_the_connection_with_its_feeds(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    connection = await connect(client)
    db_session.add(
        FeedRow(connection_id=uuid.UUID(connection["id"]), remote_path="/apps/wahoo")
    )
    await db_session.commit()

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
    assert (await anon_client.post(COMPLETE, json={"code": "x"})).status_code == 401


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
        INTEGRATIONS,
        json={
            "kind": "wahoo",
            "transport": "cloud_folder",
            "connection_id": connection["id"],
            "remote_path": "/apps/wahoo",
        },
    )
    assert created.status_code == 201, created.text
    integration_id = created.json()["id"]
    folder_id = created.json()["folders"][0]["feed_id"]
    spec = (await client.get("/openapi.json")).json()

    for method, url, operation in (
        ("POST", COMPLETE, "/api/v1/connections/dropbox/complete"),
        ("POST", INTEGRATIONS, "/api/v1/integrations"),
        (
            "PATCH",
            f"{INTEGRATIONS}/{integration_id}/folders/{folder_id}",
            "/api/v1/integrations/{integration_id}/folders/{folder_id}",
        ),
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

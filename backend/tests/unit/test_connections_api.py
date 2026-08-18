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
    MAX_APP_KEY_LENGTH,
    ConnectionRow,
    FeedRow,
    OAuthAuthorizationRow,
    ProviderAppRow,
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
)

pytestmark = pytest.mark.usefixtures("dropbox_env")

AUTHORIZE = "/api/v1/connections/dropbox/authorize"
COMPLETE = "/api/v1/connections/dropbox/complete"
CONNECTIONS = "/api/v1/connections"
SETUP = "/api/v1/connections/dropbox/setup"
APP = "/api/v1/connections/dropbox/app"
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


def no_env_app_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset `DROPBOX__APP_KEY` for this test, cache cleared both ways.

    `delenv` rather than an empty string: "the operator never wrote the line"
    is the state a fresh install is in, and it is the one AC-1 is about. The
    empty-string spelling is its own edge case below.
    """
    monkeypatch.delenv("DROPBOX__APP_KEY", raising=False)
    get_settings.cache_clear()


async def app_keys_of(session: AsyncSession) -> list[str]:
    """Every stored provider app key, so "exactly one row" is provable."""
    rows = (await session.execute(select(ProviderAppRow))).scalars().all()
    return [row.app_key for row in rows]


# --- AC-1/AC-2/AC-3: the app key the athlete pastes in ------------------------


async def test_setup_reports_no_app_key_when_neither_source_has_one(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_env_app_key(monkeypatch)

    response = await client.get(SETUP)

    assert response.status_code == 200, response.text
    assert response.json() == {"app_key_set": False, "source": None}


async def test_an_app_key_set_to_the_empty_string_is_not_a_source(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `DROPBOX__APP_KEY=` in a .env file is a *set* variable holding nothing.
    # Reporting `environment` for it would send the panel to a connect button
    # that fails on Dropbox's error page with a blank `client_id`.
    monkeypatch.setenv("DROPBOX__APP_KEY", "")
    get_settings.cache_clear()

    response = await client.get(SETUP)

    assert response.status_code == 200, response.text
    assert response.json() == {"app_key_set": False, "source": None}


async def test_setup_reports_the_environment_when_only_env_has_a_key(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(SETUP)

    assert response.status_code == 200, response.text
    assert response.json() == {"app_key_set": True, "source": "environment"}


async def test_a_stored_app_key_authorizes_in_the_same_process(
    client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_env_app_key(monkeypatch)

    stored = await client.put(APP, json={"app_key": "abc123def456"})

    assert stored.status_code == 200, stored.text
    assert stored.json() == {"app_key_set": True, "source": "stored"}
    assert await app_keys_of(db_session) == ["abc123def456"]
    # No restart, no re-read of the environment: the very next authorize call
    # carries the key that was just pasted in.
    started = await client.post(AUTHORIZE)
    assert started.status_code == 200, started.text
    assert query_of(started.json()["authorize_url"])["client_id"] == ["abc123def456"]


async def test_a_second_put_overwrites_the_stored_key(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await client.put(APP, json={"app_key": "first-key"})

    response = await client.put(APP, json={"app_key": "second-key"})

    assert response.status_code == 200, response.text
    assert await app_keys_of(db_session) == ["second-key"]


async def test_a_blank_app_key_is_refused_and_stores_nothing(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.put(APP, json={"app_key": "   "})

    assert response.status_code == 422, response.text
    assert await app_keys_of(db_session) == []


async def test_an_over_long_app_key_is_refused_and_stores_nothing(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.put(APP, json={"app_key": "k" * (MAX_APP_KEY_LENGTH + 1)})

    assert response.status_code == 422, response.text
    assert await app_keys_of(db_session) == []


async def test_changing_the_app_key_under_a_live_connection_is_a_409(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await connect(client)

    response = await client.put(APP, json={"app_key": "another-app-entirely"})

    assert response.status_code == 409, response.text
    # The remedy is named, because a stored credential belongs to the app it
    # was granted to: pointing arc at a different app leaves a token that
    # refreshes against a client id that never issued it.
    assert "Disconnect it" in response.json()["detail"]
    assert await app_keys_of(db_session) == []


async def test_stored_app_key_overrides_environment(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DROPBOX__APP_KEY", "fromenv")
    get_settings.cache_clear()
    stored = await client.put(APP, json={"app_key": "fromdb"})
    assert stored.status_code == 200, stored.text

    setup = await client.get(SETUP)
    started = await client.post(AUTHORIZE)

    assert setup.json() == {"app_key_set": True, "source": "stored"}
    assert query_of(started.json()["authorize_url"])["client_id"] == ["fromdb"]

    cleared = await client.delete(APP)

    assert cleared.status_code == 204, cleared.text
    after = await client.get(SETUP)
    restarted = await client.post(AUTHORIZE)
    assert after.json() == {"app_key_set": True, "source": "environment"}
    assert query_of(restarted.json()["authorize_url"])["client_id"] == ["fromenv"]


async def test_clearing_a_key_that_was_never_stored_is_a_204(
    client: httpx.AsyncClient,
) -> None:
    # The desired state — arc holds no app key of its own — is already true.
    # A 404 would make the panel report a failure for a button that did
    # exactly what it promised.
    response = await client.delete(APP)

    assert response.status_code == 204, response.text


async def test_clearing_the_only_key_there_was_leaves_the_setup_unset(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_env_app_key(monkeypatch)
    await client.put(APP, json={"app_key": "abc123def456"})

    await client.delete(APP)

    assert (await client.get(SETUP)).json() == {"app_key_set": False, "source": None}


async def test_the_app_key_routes_need_a_session(
    anon_client: httpx.AsyncClient,
) -> None:
    assert (await anon_client.get(SETUP)).status_code == 401
    assert (await anon_client.put(APP, json={"app_key": "k"})).status_code == 401
    assert (await anon_client.delete(APP)).status_code == 401


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


async def test_authorizing_with_no_app_key_anywhere_is_a_422_naming_the_remedy(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The remedy is a paste into the panel, not an edit to `.env` and a
    # restart, so that is what the refusal says. The panel reads
    # `GET /connections/dropbox/setup` and never offers the button that gets
    # here — this is the guard for everything that is not the panel.
    monkeypatch.setenv("DROPBOX__APP_KEY", "")
    get_settings.cache_clear()

    response = await client.post(AUTHORIZE)

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "app key" in detail
    assert "Full Dropbox" in detail


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


async def test_a_token_exchange_that_never_reaches_dropbox_is_a_422(
    client: httpx.AsyncClient, db_session: AsyncSession, fake: FakeDropbox
) -> None:
    """A dead network during `complete` is the answer `complete` promises.

    Not a 500. Every other failure of this call is a sentence the athlete can
    act on, and "arc could not reach Dropbox — try again" is one too; a stack
    trace is not. It is asserted here rather than left to the connector's own
    suite because this is the status code the caller sees, and because the
    fuzz job can now reach this exchange for real: with the app key writable
    over the API, a generated `PUT` + `authorize` + `complete` is a token
    request to a host CI may not be able to resolve.
    """
    await client.post(AUTHORIZE)
    fake.raises[TOKEN_PATH] = httpx.ConnectError("dropbox is unreachable")

    response = await client.post(COMPLETE, json={"code": "pasted-code"})

    assert response.status_code == 422, response.text
    assert "could not be reached" in response.json()["detail"]
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

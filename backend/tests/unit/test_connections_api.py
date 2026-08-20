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
import unicodedata
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
from app.persistence.audit import AuditLogEntry
from app.persistence.connections import (
    MAX_APP_KEY_LENGTH,
    MAX_STATE_LENGTH,
    ConnectionRow,
    FeedRow,
    OAuthAuthorizationRow,
    ProviderAppRow,
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
SETUP = "/api/v1/connections/dropbox/setup"
APP = "/api/v1/connections/dropbox/app"
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


def no_env_app_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset `DROPBOX__APP_KEY` for this test, cache cleared both ways.

    `delenv` rather than an empty string: "the operator never wrote the line"
    is the state a fresh install is in, and it is the one the setup read is
    about. The empty-string spelling is its own edge case below.
    """
    monkeypatch.delenv("DROPBOX__APP_KEY", raising=False)
    get_settings.cache_clear()


async def app_keys_of(session: AsyncSession) -> list[str]:
    """Every stored provider app key, so "exactly one row" is provable."""
    rows = (await session.execute(select(ProviderAppRow))).scalars().all()
    return [row.app_key for row in rows]


# --- the app key the athlete pastes in -----------------------------------------


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
    # Reporting `environment` for it would send the add flow to a connect
    # button that fails on Dropbox's error page with a blank `client_id`.
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
    # A 404 would make the athlete read a failure off a button that did
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
    # The remedy is a paste into Settings, not an edit to `.env` and a
    # restart, so that is what the refusal says. The add-integration flow
    # reads `GET /connections/dropbox/setup` and never offers the button that
    # gets here — this is the guard for everything that is not that flow.
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


# --- AC-24 and AC-25: the redirect flow --------------------------------------
#
# The browser tells arc where it is, and arc decides whether Dropbox will
# redirect there — every assertion below is on the query string arc renders or
# on the `oauth_authorizations` row it wrote, never on a helper's return value.

#: The origin the athlete reaches arc at, as their browser reports it.
REDIRECT_URI = "https://arc.example.com/settings/dropbox/callback"


async def authorization_row(session: AsyncSession) -> OAuthAuthorizationRow:
    """The one pending flow, so "one row" is provable rather than assumed."""
    rows = (await session.execute(select(OAuthAuthorizationRow))).scalars().all()
    assert len(rows) == 1, f"expected one authorization row, found {len(rows)}"
    return rows[0]


async def test_a_redirect_start_carries_the_uri_and_a_state_and_stores_both(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post(AUTHORIZE, json={"redirect_uri": REDIRECT_URI})

    assert response.status_code == 200, response.text
    query = query_of(response.json()["authorize_url"])
    assert query["redirect_uri"] == [REDIRECT_URI]
    state = query["state"][0]
    # Long enough to be a nonce rather than a guess. The value itself is
    # arbitrary; its length and its unguessability are the contract.
    assert len(state) >= 32
    row = await authorization_row(db_session)
    assert row.state == state
    assert row.redirect_uri == REDIRECT_URI
    # The PKCE half is unchanged: the redirect adds a CSRF nonce, it does not
    # replace the thing that makes the code useless to anyone else.
    assert query["code_challenge"] == [challenge_for(row.code_verifier)]


@pytest.mark.parametrize(
    "uri",
    [
        "http://localhost:3000/settings/dropbox/callback",
        "http://127.0.0.1:3000/settings/dropbox/callback",
    ],
)
async def test_a_loopback_http_origin_still_gets_the_redirect_flow(
    client: httpx.AsyncClient, db_session: AsyncSession, uri: str
) -> None:
    # The developer's laptop, and the athlete running arc on the machine in
    # front of them. Dropbox exempts the loopback from its https rule, so
    # falling back to the paste here would be arc being stricter than Dropbox.
    response = await client.post(AUTHORIZE, json={"redirect_uri": uri})

    assert response.status_code == 200, response.text
    assert query_of(response.json()["authorize_url"])["redirect_uri"] == [uri]
    assert (await authorization_row(db_session)).redirect_uri == uri


async def test_a_plain_http_lan_origin_is_refused_naming_https_and_the_paste(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post(
        AUTHORIZE,
        json={"redirect_uri": "http://192.168.1.50/settings/dropbox/callback"},
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "https" in detail
    # The remedy, not just the refusal: this deployment connects by paste, and
    # the athlete has to be told that rather than left at a dead end.
    assert "paste" in detail.lower()
    # Nothing was started: a flow the athlete cannot finish is worse than none.
    assert await count_of(db_session, OAuthAuthorizationRow) == 0


async def test_omitting_the_redirect_uri_leaves_the_paste_flow_exactly_as_it_was(
    client: httpx.AsyncClient, db_session: AsyncSession, fake: FakeDropbox
) -> None:
    # The fallback the whole feature rests on. A body-less start is what the
    # step sends on a plain-HTTP LAN deployment, and it must still produce the
    # link Dropbox shows a code on.
    response = await client.post(AUTHORIZE)

    assert response.status_code == 200, response.text
    query = query_of(response.json()["authorize_url"])
    assert "redirect_uri" not in query
    assert "state" not in query
    row = await authorization_row(db_session)
    assert row.state is None
    assert row.redirect_uri is None

    completed = await client.post(COMPLETE, json={"code": "pasted-code"})

    assert completed.status_code == 201, completed.text
    assert "redirect_uri" not in fake.calls_to(TOKEN_PATH)[0].form


async def test_a_second_redirect_start_supersedes_the_first_state(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await client.post(AUTHORIZE, json={"redirect_uri": REDIRECT_URI})
    first = (await authorization_row(db_session)).state
    await client.post(AUTHORIZE, json={"redirect_uri": REDIRECT_URI})

    # One row, not two: an abandoned tab must not leave a second redeemable
    # flow behind it.
    second = await authorization_row(db_session)
    assert second.state != first

    response = await client.post(COMPLETE, json={"code": "code", "state": first})

    assert response.status_code == 422, response.text
    assert await count_of(db_session, ConnectionRow) == 0


async def test_a_redirect_flow_completes_and_repeats_the_uri_to_dropbox(
    client: httpx.AsyncClient, db_session: AsyncSession, fake: FakeDropbox
) -> None:
    started = await client.post(AUTHORIZE, json={"redirect_uri": REDIRECT_URI})
    state = query_of(started.json()["authorize_url"])["state"][0]

    response = await client.post(COMPLETE, json={"code": "pasted-code", "state": state})

    assert response.status_code == 201, response.text
    # RFC 6749 s4.1.3: the exchange repeats the redirect URI the code was
    # minted against, or Dropbox answers `invalid_grant`.
    assert fake.calls_to(TOKEN_PATH)[0].form["redirect_uri"] == REDIRECT_URI
    assert await count_of(db_session, OAuthAuthorizationRow) == 0


async def test_a_mismatched_state_is_refused_and_the_flow_is_deleted(
    client: httpx.AsyncClient, db_session: AsyncSession, fake: FakeDropbox
) -> None:
    started = await client.post(AUTHORIZE, json={"redirect_uri": REDIRECT_URI})
    state = query_of(started.json()["authorize_url"])["state"][0]

    response = await client.post(
        COMPLETE, json={"code": "pasted-code", "state": "not-the-state"}
    )

    assert response.status_code == 422, response.text
    assert await count_of(db_session, ConnectionRow) == 0
    # "No connection is created" in the record as well as in the table: a
    # `connection.connected` audit row would say arc believed it had one.
    assert await count_of(db_session, AuditLogEntry) == 0
    # Not offered to Dropbox at all: a code arriving with the wrong nonce is
    # not arc's code, and redeeming it is the attack this guards against.
    assert fake.calls_to(TOKEN_PATH) == []
    # Deleted, not merely refused. Leaving the row redeemable would let the
    # attacker who guessed wrong once simply try again.
    assert await count_of(db_session, OAuthAuthorizationRow) == 0

    retry = await client.post(COMPLETE, json={"code": "pasted-code", "state": state})

    assert retry.status_code == 422, retry.text
    assert "No Dropbox authorization is in progress" in retry.json()["detail"]
    assert await count_of(db_session, ConnectionRow) == 0


@pytest.mark.parametrize(
    "supplied",
    [
        # Not ASCII, and `secrets.compare_digest` refuses a `str` that is not:
        # the nonce arrives from a query string, so it is whatever the athlete's
        # browser was pointed at, not something arc minted.
        "nøt-the-state",
        # ASCII apart from its last character: what breaks the comparison is
        # one byte being outside ASCII, not the value looking exotic.
        "not-the-statė",
    ],
)
async def test_a_non_ascii_state_is_a_mismatch_and_still_deletes_the_flow(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    fake: FakeDropbox,
    supplied: str,
) -> None:
    # The comparison is over bytes, not characters. Comparing `str` here would
    # raise inside `secrets.compare_digest` and turn a wrong nonce — the exact
    # thing AC-25 is about — into a 500 that leaves the flow redeemable, so an
    # attacker could deny arc the deletion simply by sending one accented
    # character.
    await client.post(AUTHORIZE, json={"redirect_uri": REDIRECT_URI})

    response = await client.post(
        COMPLETE, json={"code": "pasted-code", "state": supplied}
    )

    assert response.status_code == 422, response.text
    assert "could not verify" in response.json()["detail"]
    assert await count_of(db_session, ConnectionRow) == 0
    assert fake.calls_to(TOKEN_PATH) == []
    # The deletion is the half a crash would have skipped.
    assert await count_of(db_session, OAuthAuthorizationRow) == 0


async def test_a_lone_surrogate_state_is_a_mismatch_and_still_deletes_the_flow(
    client: httpx.AsyncClient, db_session: AsyncSession, fake: FakeDropbox
) -> None:
    # Sent as raw bytes because no JSON encoder will emit one from a Python
    # string, and a browser's `JSON.stringify` will: a lone surrogate escapes
    # to ASCII on the way out and comes back a surrogate here. It has no plain
    # UTF-8 form, so the comparison has to say what it does with one instead of
    # raising on it.
    await client.post(AUTHORIZE, json={"redirect_uri": REDIRECT_URI})

    response = await client.post(
        COMPLETE,
        content=rb'{"code": "pasted-code", "state": "\ud800"}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422, response.text
    assert "could not verify" in response.json()["detail"]
    assert await count_of(db_session, ConnectionRow) == 0
    assert fake.calls_to(TOKEN_PATH) == []
    assert await count_of(db_session, OAuthAuthorizationRow) == 0


async def test_a_state_equal_only_after_unicode_normalisation_is_refused(
    client: httpx.AsyncClient, db_session: AsyncSession, fake: FakeDropbox
) -> None:
    # A nonce is compared as the bytes arc issued, never as text. This value
    # is the real state with one character swapped for its fullwidth twin, so
    # it normalises straight back to the state and is still a different value
    # — encoding first is what keeps it one, and any folding on the way in
    # would quietly widen what counts as a match.
    started = await client.post(AUTHORIZE, json={"redirect_uri": REDIRECT_URI})
    state = query_of(started.json()["authorize_url"])["state"][0]
    #: Every character `token_urlsafe` emits is ASCII punctuation-to-tilde,
    #: which is exactly the range with a fullwidth form 0xFEE0 above it.
    folded = chr(ord(state[0]) + 0xFEE0) + state[1:]
    assert folded != state
    assert unicodedata.normalize("NFKC", folded) == state

    response = await client.post(
        COMPLETE, json={"code": "pasted-code", "state": folded}
    )

    assert response.status_code == 422, response.text
    assert await count_of(db_session, ConnectionRow) == 0
    assert await count_of(db_session, OAuthAuthorizationRow) == 0


@pytest.mark.parametrize(
    ("label", "supplied"),
    [
        # Longer than any nonce arc mints. A bound in the request schema would
        # refuse this before the service ever saw it — 422, but with the flow
        # still sitting there redeemable.
        ("over-long", "x" * (MAX_STATE_LENGTH + 1)),
        # The empty string is a value the caller supplied, not an omission:
        # `state=` in the callback's query string arrives as one.
        ("empty", ""),
    ],
)
async def test_a_state_the_schema_could_have_rejected_still_deletes_the_flow(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    fake: FakeDropbox,
    label: str,
    supplied: str,
) -> None:
    # AC-25's two halves are one rule: a wrong nonce is refused *and* ends the
    # flow. A shape the request schema turns away is refused without ending
    # anything, which leaves the attacker who sent it free to keep guessing —
    # so the verdict on `state` belongs to the service, not to a length bound.
    await client.post(AUTHORIZE, json={"redirect_uri": REDIRECT_URI})

    response = await client.post(
        COMPLETE, json={"code": "pasted-code", "state": supplied}
    )

    assert response.status_code == 422, response.text
    assert "could not verify" in response.json()["detail"], label
    assert await count_of(db_session, ConnectionRow) == 0
    assert fake.calls_to(TOKEN_PATH) == []
    assert await count_of(db_session, OAuthAuthorizationRow) == 0


async def test_a_redirect_flow_completed_without_a_state_is_refused(
    client: httpx.AsyncClient, db_session: AsyncSession, fake: FakeDropbox
) -> None:
    # A code lifted out of a browser history and pasted into the form: it has
    # the code and not the nonce, and that is exactly what `state` is for.
    await client.post(AUTHORIZE, json={"redirect_uri": REDIRECT_URI})

    response = await client.post(COMPLETE, json={"code": "pasted-code"})

    assert response.status_code == 422, response.text
    assert await count_of(db_session, ConnectionRow) == 0
    assert await count_of(db_session, OAuthAuthorizationRow) == 0
    assert fake.calls_to(TOKEN_PATH) == []


async def test_a_paste_flow_completed_with_a_state_is_refused(
    client: httpx.AsyncClient, db_session: AsyncSession, fake: FakeDropbox
) -> None:
    # The other direction, and the reason the comparison is not "if the row
    # has a state": a flow arc started with no redirect has nothing to
    # round-trip, so a `state` arriving against it came from somewhere else.
    await client.post(AUTHORIZE)

    response = await client.post(
        COMPLETE, json={"code": "pasted-code", "state": "invented"}
    )

    assert response.status_code == 422, response.text
    assert await count_of(db_session, ConnectionRow) == 0
    assert await count_of(db_session, OAuthAuthorizationRow) == 0
    assert fake.calls_to(TOKEN_PATH) == []


async def test_an_expired_redirect_flow_says_it_expired_not_that_the_state_is_wrong(
    client: httpx.AsyncClient, db_session: AsyncSession, fake: FakeDropbox
) -> None:
    # Ordering, asserted: an athlete who left the Dropbox tab open over lunch
    # gets "start again", not a sentence about a security token. The state is
    # correct here; only the clock is against them.
    started = await client.post(AUTHORIZE, json={"redirect_uri": REDIRECT_URI})
    state = query_of(started.json()["authorize_url"])["state"][0]
    row = await authorization_row(db_session)
    row.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    await db_session.commit()

    response = await client.post(COMPLETE, json={"code": "pasted-code", "state": state})

    assert response.status_code == 422, response.text
    assert "expired" in response.json()["detail"]
    assert await count_of(db_session, OAuthAuthorizationRow) == 0
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

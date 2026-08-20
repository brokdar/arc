"""AC-2 and AC-4: the scope set arc asks for, and the refresh-and-retry rule.

These are the two things about the connector nobody can see from the outside:
what the athlete is being asked to grant, and how many times arc talks to
Dropbox to serve one call. Both are asserted against the fake transport's
recorded request list rather than against a return value, because the defect
each guards against — a wider scope, a refresh loop — is invisible in the
result and only visible in the traffic.
"""

import asyncio
import datetime as dt
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors import dropbox
from app.connectors.dropbox import (
    READ_SCOPES,
    DropboxAuthError,
    DropboxClient,
    DropboxRateLimitedError,
    DropboxUpstreamError,
    authorize_url,
    exchange_code,
    new_code_verifier,
    new_state,
    redirect_eligible,
)
from app.domain.connections import ConnectionProvider, ConnectionStatus
from app.persistence.connections import ConnectionRow, EncryptedCredentials
from tests.unit.dropbox_fake import (
    LIST_FOLDER_PATH,
    TOKEN_PATH,
    FakeDropbox,
    expired_access_token,
    rate_limited,
)

pytestmark = pytest.mark.usefixtures("dropbox_env")


@pytest.fixture(autouse=True)
def fake() -> Iterator[FakeDropbox]:
    """Dropbox, faked, for every test in this module. Autouse: see below.

    A test that forgot to ask for it would make a real request to dropbox.com
    from the unit suite.
    """
    upstream = FakeDropbox()
    dropbox.set_transport(upstream.transport)
    yield upstream
    dropbox.set_transport(None)


async def connection(
    session: AsyncSession,
    *,
    expires_in: int = 3_600,
    refresh_token: str | None = "refresh-token-0",  # noqa: S107 — a fixture, not a secret
    status: ConnectionStatus = ConnectionStatus.CONNECTED,
) -> ConnectionRow:
    """A stored Dropbox connection holding a sealed credential."""
    credential: dict[str, str] = {"access_token": "access-token-0"}
    if refresh_token is not None:
        credential["refresh_token"] = refresh_token
    row = ConnectionRow(
        provider=ConnectionProvider.DROPBOX,
        status=status,
        account_label="Ada Lovelace (ada@example.com)",
        scopes=sorted(READ_SCOPES),
        credentials=EncryptedCredentials.seal(credential),
        access_token_expires_at=dt.datetime.now(dt.UTC)
        + dt.timedelta(seconds=expires_in),
    )
    session.add(row)
    await session.commit()
    return row


def client(session: AsyncSession, row: ConnectionRow) -> DropboxClient:
    return DropboxClient(session, row, app_key="test-app-key")


# --- AC-2: the scope set is pinned -------------------------------------------


def test_the_connect_flow_asks_for_exactly_the_three_read_scopes() -> None:
    # Compared as a SET: reordering the constant is not a failure, adding a
    # member is. `files.content.write` arriving here would mean arc holds a
    # credential able to delete the athlete's Dropbox while the feature that
    # needs it is switched off.
    assert set(READ_SCOPES) == {
        "account_info.read",
        "files.metadata.read",
        "files.content.read",
    }


def test_the_authorize_url_requests_the_pinned_scope_set() -> None:
    query = parse_qs(
        urlparse(authorize_url(app_key="k", verifier=new_code_verifier())).query
    )

    assert set(query["scope"][0].split(" ")) == set(READ_SCOPES)


def test_the_authorize_url_asks_for_no_write_scope() -> None:
    query = parse_qs(
        urlparse(authorize_url(app_key="k", verifier=new_code_verifier())).query
    )

    assert "files.content.write" not in query["scope"][0]


# --- AC-24: which deployments Dropbox will redirect back to ------------------


@pytest.mark.parametrize(
    "uri",
    [
        "https://arc.example.com/settings/dropbox/callback",
        # A self-signed https on the LAN is still https, and Dropbox takes it.
        "https://arc.local/settings/dropbox/callback",
        "https://arc.example.com:8443/settings/dropbox/callback",
        "http://localhost:3000/settings/dropbox/callback",
        "http://127.0.0.1:3000/settings/dropbox/callback",
        "http://[::1]:3000/settings/dropbox/callback",
    ],
)
def test_dropbox_redirects_to_https_anywhere_and_to_http_on_the_loopback(
    uri: str,
) -> None:
    assert redirect_eligible(uri) is True


@pytest.mark.parametrize(
    "uri",
    [
        # The deployment this rule exists for: arc reached over plain HTTP by
        # LAN address. Dropbox refuses to register it, so arc must not offer
        # the redirect and must say why.
        "http://192.168.1.50/settings/dropbox/callback",
        "http://arc.local/settings/dropbox/callback",
        "http://10.0.0.4:3000/settings/dropbox/callback",
        # `localhost.evil.example` is not the loopback, and a prefix match
        # would have said it was.
        "http://localhost.evil.example/settings/dropbox/callback",
        "http://127.0.0.1.evil.example/settings/dropbox/callback",
        # Not a URL arc could ever be reached at.
        "ftp://arc.example.com/callback",
        "javascript:alert(1)",
        "/settings/dropbox/callback",
        "https:///settings/dropbox/callback",
        "",
    ],
)
def test_everything_else_is_ineligible_so_the_paste_flow_is_offered_instead(
    uri: str,
) -> None:
    assert redirect_eligible(uri) is False


def test_a_state_is_long_enough_to_be_a_nonce_and_never_repeats() -> None:
    # AC-24 asks for at least 32 characters, and the CSRF value is worthless
    # if two flows can be issued the same one.
    states = {new_state() for _ in range(64)}

    assert len(states) == 64
    assert all(len(state) >= 32 for state in states)


def test_the_authorize_url_carries_the_redirect_and_state_when_given_them() -> None:
    query = parse_qs(
        urlparse(
            authorize_url(
                app_key="k",
                verifier=new_code_verifier(),
                redirect_uri="https://arc.example.com/settings/dropbox/callback",
                state="a-nonce",
            )
        ).query
    )

    assert query["redirect_uri"] == [
        "https://arc.example.com/settings/dropbox/callback"
    ]
    assert query["state"] == ["a-nonce"]
    # The paste flow is the same URL minus two parameters, not a second one.
    assert query["code_challenge_method"] == ["S256"]
    assert query["token_access_type"] == ["offline"]


async def test_the_exchange_repeats_the_redirect_uri_dropbox_was_given(
    fake: FakeDropbox,
) -> None:
    await exchange_code(
        app_key="k",
        code="c",
        verifier=new_code_verifier(),
        redirect_uri="https://arc.example.com/settings/dropbox/callback",
    )

    # RFC 6749 s4.1.3: a code minted against a redirect URI is only redeemable
    # by repeating it, and Dropbox enforces it — omitting it here is an
    # `invalid_grant` the athlete would read as "the code expired".
    form = fake.calls_to(TOKEN_PATH)[0].form
    assert form["redirect_uri"] == "https://arc.example.com/settings/dropbox/callback"


async def test_the_paste_exchange_still_sends_no_redirect_uri(
    fake: FakeDropbox,
) -> None:
    await exchange_code(app_key="k", code="c", verifier=new_code_verifier())

    assert "redirect_uri" not in fake.calls_to(TOKEN_PATH)[0].form


# --- AC-4: one refresh, one retry --------------------------------------------


async def test_an_expired_access_token_is_refreshed_once_then_the_call_is_made(
    db_session: AsyncSession, fake: FakeDropbox
) -> None:
    row = await connection(db_session, expires_in=-60)

    await client(db_session, row).list_folders("")

    refreshes = fake.calls_to(TOKEN_PATH)
    assert len(refreshes) == 1
    assert refreshes[0].form["grant_type"] == "refresh_token"
    assert refreshes[0].form["client_id"] == "test-app-key"
    # PKCE public client: there is no secret, and sending one would mean arc
    # is holding a credential the deployment was never asked for.
    assert "client_secret" not in refreshes[0].form
    assert len(fake.calls_to(LIST_FOLDER_PATH)) == 1


async def test_a_refreshed_token_is_re_encrypted_and_stored(
    db_session: AsyncSession, fake: FakeDropbox
) -> None:
    fake.access_token = "access-token-2"
    fake.refresh_token = "rotated-refresh-token"
    row = await connection(db_session, expires_in=-60)

    await client(db_session, row).list_folders("")

    await db_session.refresh(row)
    stored = EncryptedCredentials.unseal(row.credentials)
    assert stored["access_token"] == "access-token-2"
    # A rotated refresh token replaces the old one, or the next refresh is the
    # last one that works.
    assert stored["refresh_token"] == "rotated-refresh-token"
    assert b"rotated-refresh-token" not in row.credentials
    assert row.access_token_expires_at is not None
    assert row.access_token_expires_at > dt.datetime.now(dt.UTC)


async def test_a_refresh_that_keeps_the_old_token_leaves_it_in_place(
    db_session: AsyncSession, fake: FakeDropbox
) -> None:
    # Dropbox normally omits `refresh_token` from a refresh response; dropping
    # the stored one on that would disconnect arc on its first token renewal.
    fake.refresh_token = None
    row = await connection(db_session, expires_in=-60)

    await client(db_session, row).list_folders("")

    await db_session.refresh(row)
    assert EncryptedCredentials.unseal(row.credentials)["refresh_token"] == (
        "refresh-token-0"
    )


async def test_a_refresh_refused_as_invalid_grant_needs_reauth_and_does_not_retry(
    db_session: AsyncSession, fake: FakeDropbox
) -> None:
    fake.token_error = "invalid_grant"
    row = await connection(db_session, expires_in=-60)

    with pytest.raises(DropboxAuthError):
        await client(db_session, row).list_folders("")

    await db_session.refresh(row)
    assert row.status is ConnectionStatus.NEEDS_REAUTH
    assert row.last_error
    assert len(fake.calls_to(TOKEN_PATH)) == 1
    assert fake.calls_to(LIST_FOLDER_PATH) == []


async def test_a_401_mid_call_triggers_one_refresh_and_one_retry(
    db_session: AsyncSession, fake: FakeDropbox
) -> None:
    fake.script(LIST_FOLDER_PATH, expired_access_token())
    row = await connection(db_session, expires_in=3_600)

    folders = await client(db_session, row).list_folders("")

    assert [folder.path_lower for folder in folders] == ["/apps", "/photos"]
    assert len(fake.calls_to(TOKEN_PATH)) == 1
    assert len(fake.calls_to(LIST_FOLDER_PATH)) == 2


async def test_a_second_401_after_refreshing_is_an_error_not_a_loop(
    db_session: AsyncSession, fake: FakeDropbox
) -> None:
    fake.script(LIST_FOLDER_PATH, expired_access_token(), expired_access_token())
    row = await connection(db_session, expires_in=3_600)

    with pytest.raises(DropboxAuthError):
        await client(db_session, row).list_folders("")

    assert len(fake.calls_to(TOKEN_PATH)) == 1
    assert len(fake.calls_to(LIST_FOLDER_PATH)) == 2


async def test_two_concurrent_calls_on_one_connection_refresh_once(
    db_session: AsyncSession, fake: FakeDropbox
) -> None:
    row = await connection(db_session, expires_in=-60)
    caller = client(db_session, row)

    await asyncio.gather(caller.list_folders(""), caller.list_folders(""))

    assert len(fake.calls_to(TOKEN_PATH)) == 1
    assert len(fake.calls_to(LIST_FOLDER_PATH)) == 2


async def test_a_429_raises_a_named_error_carrying_the_delay_and_refreshes_nothing(
    db_session: AsyncSession, fake: FakeDropbox
) -> None:
    fake.script(LIST_FOLDER_PATH, rate_limited("42"))
    row = await connection(db_session, expires_in=3_600)

    with pytest.raises(DropboxRateLimitedError) as raised:
        await client(db_session, row).list_folders("")

    assert raised.value.retry_after == pytest.approx(42.0)
    assert isinstance(raised.value, DropboxUpstreamError)
    assert fake.calls_to(TOKEN_PATH) == []

"""Dropbox: the PKCE handshake, and a client that renews its own access token.

**The app is registered as "Full Dropbox", not "App folder".** An app-folder
app is the least-privilege choice and it is the wrong one here, because it is
*structurally* unable to see any folder but its own — and the files this
feature exists to collect are already sitting in `/Apps/WahooFitness/` and
HealthFit's equivalent, written there by somebody else's app. An arc that could
only read `/Apps/arc/` would require the athlete to re-export every ride into
it by hand, which is the ritual this feature removes. The privilege given back
is bounded by the *scope* set instead (:data:`READ_SCOPES`), which is narrow,
read-only, and pinned by a test.

Everything in this module is written against `httpx` with the transport
injectable (:func:`set_transport`), because there is no vendor to test against:
`tests/unit/dropbox_fake.py` is the whole of Dropbox as far as the suite is
concerned, and the assertions that matter are about the *requests* arc makes.
"""

import asyncio
import base64
import datetime as dt
import hashlib
import json
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlencode

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.domain.connections import ConnectionStatus
from app.persistence.connections import ConnectionRow, EncryptedCredentials
from app.persistence.db import commit

logger = get_logger(__name__)

AUTHORIZE_ENDPOINT: Final = "https://www.dropbox.com/oauth2/authorize"
#: The OAuth token endpoint. (`S105` reads "token" in the name as a secret;
#: this is the URL a secret would be exchanged AT, and it is public.)
TOKEN_ENDPOINT: Final = "https://api.dropboxapi.com/oauth2/token"  # noqa: S105
API_BASE: Final = "https://api.dropboxapi.com"
#: File *contents* come from a different host than the RPC endpoints, and
#: Dropbox is strict about it: `/2/files/download` on `api.dropboxapi.com` is a
#: 400. The argument travels in a header there, and the body is the file.
CONTENT_BASE: Final = "https://content.dropboxapi.com"

#: The scopes arc asks for when the athlete connects an account.
#:
#: **Read-only, and `files.content.write` is deliberately absent.** The cleanup
#: feature (a later PR) needs write access to remove files arc has ingested,
#: and the tempting shortcut is to ask for it here so the setting can be
#: toggled freely afterwards. That would leave arc holding a credential able to
#: delete anything in the athlete's Dropbox for the entire period the feature
#: is switched off — permanently, for an athlete who never turns it on. Write
#: scope is requested by re-authorizing, at the moment it is granted.
#:
#: A frozenset, and asserted as a *set* by AC-2: reordering is not a failure,
#: adding a member is.
READ_SCOPES: Final = frozenset(
    {"account_info.read", "files.metadata.read", "files.content.read"}
)

#: Seconds of slack subtracted from a token's stated lifetime.
#:
#: A token that expires while a request is in flight costs a 401, a refresh and
#: a retry — correct, but three round trips where one would do. Renewing a
#: minute early costs nothing: Dropbox tokens last four hours.
EXPIRY_SKEW_SECONDS: Final = 60

#: How long arc waits on any one Dropbox request.
REQUEST_TIMEOUT_SECONDS: Final = 30.0


class DropboxError(Exception):
    """Anything that went wrong talking to Dropbox."""


class DropboxAuthError(DropboxError):
    """The credential is dead: Dropbox refused the refresh or the exchange.

    The remedy is always the athlete re-authorizing; nothing arc can do
    locally will fix it, which is why this is distinguished from every other
    upstream failure.
    """


class DropboxUpstreamError(DropboxError):
    """Dropbox answered, and the answer was a failure arc did not cause."""


class DropboxRateLimitedError(DropboxUpstreamError):
    """Dropbox asked arc to slow down, and said for how long."""

    def __init__(self, detail: str, *, retry_after: float) -> None:
        super().__init__(detail)
        #: Seconds Dropbox asked arc to wait, from the `Retry-After` header.
        self.retry_after = retry_after


class DropboxPathNotFoundError(DropboxUpstreamError):
    """The remote path does not exist."""

    def __init__(self, path: str) -> None:
        super().__init__(f"Dropbox has no folder at {path or '/'}")
        self.path = path


class DropboxCursorResetError(DropboxUpstreamError):
    """The stored listing cursor is too old, and Dropbox wants a fresh listing.

    Its own class rather than a generic upstream failure because the remedy is
    entirely local and entirely automatic: forget the cursor and list the
    folder from scratch. Treated as a failure it would count against the
    give-up budget in `app.ingest.feeds` and eventually skip a batch, over a
    condition that is arc's to fix without anybody being told.
    """


@dataclass(frozen=True, slots=True)
class TokenGrant:
    """What Dropbox's token endpoint hands back."""

    access_token: str
    #: Absent on a refresh that does not rotate; **null on an exchange means
    #: offline access was not granted**, which the service refuses.
    refresh_token: str | None
    expires_at: dt.datetime
    scopes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DropboxAccount:
    """The account behind a credential, as the athlete would recognise it."""

    account_id: str
    label: str


@dataclass(frozen=True, slots=True)
class DropboxFolder:
    """One folder in a listing."""

    path_lower: str
    name: str


@dataclass(frozen=True, slots=True)
class DropboxFile:
    """One *file* entry in a listing, as much of it as arc has a use for.

    ``size`` is here because it is what lets a 2 GB file be refused before it
    is pulled over the network (`app.ingest.feeds._should_take`), and ``rev``
    because it is how Dropbox says "the same file, edited" — arc does not key
    anything on it, but it belongs in the log line that explains a second
    session appearing for a file the athlete believes they only have one of.
    """

    #: Dropbox's stable id (``id:aBcD...``), which survives a rename and a
    #: move. Stored on the recording as its ``external_id``.
    id: str
    name: str
    path_lower: str
    size: int
    rev: str
    #: When the *device* wrote the file, as Dropbox reports it — not when
    #: Dropbox received it (`server_modified`). Folder discovery ranks by this
    #: because a folder synced from a backup last night would otherwise look
    #: like the one the head unit is writing to. ``None`` when Dropbox omits
    #: it or sends something unparseable, which is a missing fact rather than
    #: a failure: it costs a tie-break, not a listing.
    client_modified: dt.datetime | None = None


@dataclass(frozen=True, slots=True)
class DropboxListing:
    """One folder's contents, split the way every caller reads them.

    Both halves in one value because they arrive in one listing, and the two
    callers that want only folders would otherwise spend a second round trip
    each to ask the same question — and because "the listing was empty" is a
    fact about *entries*, which neither half can state on its own.
    """

    folders: tuple[DropboxFolder, ...]
    files: tuple[DropboxFile, ...]

    @property
    def is_empty(self) -> bool:
        """Whether Dropbox reported nothing at all under this path."""
        return not self.folders and not self.files


@dataclass(frozen=True, slots=True)
class DropboxChanges:
    """One page of `list_folder`: what changed, and where to resume.

    ``cursor`` is the position **after** these entries. It is not stored until
    every entry in the page has been resolved — see
    `app.ingest.feeds._poll_feed`, which owns that rule and the reason for it.
    """

    entries: tuple[DropboxFile, ...]
    cursor: str
    #: Whether Dropbox has more to say from ``cursor`` immediately.
    has_more: bool


# --- the transport seam ------------------------------------------------------

_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Install a transport for every client this module builds; None restores real HTTP.

    The same shape as `app.persistence.db.set_session_factory`, and for the
    same reason: the code under test reaches the network through a call it
    makes itself, with no dependency injection to override. A test installs an
    `httpx.MockTransport` and asserts on the requests it recorded.
    """
    global _transport  # noqa: PLW0603
    _transport = transport


class _ReachableTransport(httpx.AsyncBaseTransport):
    """Turns a network that is simply not there into a `DropboxError`.

    httpx raises its own hierarchy for a DNS failure, a refused connection or
    a timeout, and none of it descends from :class:`DropboxError` — so the one
    failure arc has no influence over at all was the one that escaped every
    caller's `except DropboxError` and reached the athlete as a 500, while
    every failure Dropbox *chose* (a spent code, a rate limit, a missing path)
    arrived as a sentence naming the remedy.

    Wrapped at the transport rather than at each call site so a request added
    later inherits it: there are several of them in this module and the next
    one would have been written without the `try`.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return await self._inner.handle_async_request(request)
        except httpx.TransportError as exc:
            # The class name is in the message because the three cases the
            # operator would act on differently — DNS, refused, timed out —
            # are otherwise indistinguishable in a log line.
            raise DropboxUpstreamError(
                f"{request.url.host} did not answer ({type(exc).__name__}: {exc})"
            ) from exc

    async def aclose(self) -> None:
        await self._inner.aclose()


def _client() -> httpx.AsyncClient:
    """An HTTP client for one exchange, honouring the installed transport."""
    return httpx.AsyncClient(
        # The default transport, named rather than left implicit, because the
        # wrapper has to have something to wrap in production too.
        transport=_ReachableTransport(_transport or httpx.AsyncHTTPTransport()),
        timeout=REQUEST_TIMEOUT_SECONDS,
        follow_redirects=False,
    )


# --- PKCE --------------------------------------------------------------------


def new_code_verifier() -> str:
    """A fresh PKCE code verifier (RFC 7636 §4.1).

    64 random bytes rendered urlsafe-base64, which lands inside the 43–128
    character window the spec allows.
    """
    return secrets.token_urlsafe(64)[:128]


def code_challenge(verifier: str) -> str:
    """The S256 challenge for a verifier: base64url of its SHA-256, unpadded."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def authorize_url(
    *, app_key: str, verifier: str, scopes: Iterable[str] = READ_SCOPES
) -> str:
    """The URL the athlete opens to authorize arc.

    **PKCE with a pasted code, and no `redirect_uri`.** The conventional OAuth
    flow registers a redirect back to the application, and it is unavailable
    here for a reason that is structural rather than aesthetic: a redirect URI
    is registered *per origin*, and a self-hosted arc has no stable one. It is
    `http://localhost:3000` on the developer's laptop, `http://arc.local` from
    the phone, `http://192.168.1.42` when the router hands out a different
    lease, and none of those is reachable from Dropbox's servers anyway without
    exposing the box to the internet. Registering one pins the deployment to a
    single hostname and breaks the day the athlete reaches arc by IP.

    Without a redirect URI Dropbox displays the authorization code on screen
    for the athlete to copy, and PKCE is what makes that safe: the code alone
    is useless, because redeeming it requires the verifier that never left this
    process (see :func:`code_challenge`).

    `token_access_type=offline` is what makes the grant carry a **refresh**
    token — without it arc holds a four-hour credential and the athlete
    re-connects twice a day.
    """
    query = {
        "client_id": app_key,
        "response_type": "code",
        "token_access_type": "offline",
        "code_challenge": code_challenge(verifier),
        "code_challenge_method": "S256",
        "scope": " ".join(sorted(scopes)),
    }
    return f"{AUTHORIZE_ENDPOINT}?{urlencode(query)}"


# --- token exchange ----------------------------------------------------------


def _grant_from(payload: dict[str, Any]) -> TokenGrant:
    """Read a token response, without ever logging what is in it."""
    expires_in = int(payload.get("expires_in", 14_400))
    return TokenGrant(
        access_token=str(payload["access_token"]),
        refresh_token=payload.get("refresh_token"),
        expires_at=dt.datetime.now(dt.UTC)
        + dt.timedelta(seconds=max(expires_in - EXPIRY_SKEW_SECONDS, 0)),
        scopes=tuple(str(payload.get("scope", "")).split()),
    )


def _token_failure(response: httpx.Response) -> DropboxError:
    """Classify a non-200 from the token endpoint."""
    try:
        body = response.json()
    except ValueError:
        body = {}
    error = str(body.get("error", "")) if isinstance(body, dict) else ""
    if error in {"invalid_grant", "unauthorized_client", "invalid_client"}:
        return DropboxAuthError(error or "invalid_grant")
    return DropboxUpstreamError(
        f"Dropbox refused the token request with {response.status_code} "
        f"({error or 'no error code'})"
    )


async def exchange_code(*, app_key: str, code: str, verifier: str) -> TokenGrant:
    """Redeem a pasted authorization code for a token pair.

    The form carries `client_id` and `code_verifier` and **no client secret**:
    arc is a public OAuth client (see the module docstring), and a deployment
    that never registered a secret must never be asked for one.

    Raises:
        DropboxAuthError: When Dropbox refuses the code (`invalid_grant` —
            spent, expired, or minted against a different verifier).
        DropboxUpstreamError: For any other failure.
    """
    async with _client() as http:
        response = await http.post(
            TOKEN_ENDPOINT,
            data={
                "code": code,
                "grant_type": "authorization_code",
                "code_verifier": verifier,
                "client_id": app_key,
            },
        )
    if response.status_code != 200:
        raise _token_failure(response)
    return _grant_from(response.json())


async def current_account(*, access_token: str) -> DropboxAccount:
    """Ask Dropbox whose account this credential belongs to.

    A module function rather than a :class:`DropboxClient` method because it is
    called during `complete`, before there is a connection row to hang a client
    off — and the label it returns is part of what that row is created with.
    """
    # No body at all: this RPC takes no arguments, and Dropbox rejects a
    # `Content-Type` on a request it expects to be empty.
    async with _client() as http:
        response = await http.post(
            f"{API_BASE}/2/users/get_current_account",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    return _account_from(response)


def _account_from(response: httpx.Response) -> DropboxAccount:
    """Project `/2/users/get_current_account` onto the label arc stores."""
    if response.status_code != 200:
        raise DropboxUpstreamError(
            f"Dropbox would not describe the account ({response.status_code})"
        )
    body = response.json()
    display = str((body.get("name") or {}).get("display_name") or "").strip()
    email = str(body.get("email") or "").strip()
    label = f"{display} ({email})" if display and email else display or email
    return DropboxAccount(account_id=str(body.get("account_id") or ""), label=label)


# --- the client --------------------------------------------------------------

#: One refresh lock per connection id.
#:
#: Two calls that both find the access token expired would otherwise both
#: refresh, and the second refresh invalidates the token the first just stored
#: — arc would spend a request to break its own credential. The lock is keyed
#: by connection rather than global so a second provider's calls are not
#: serialised behind Dropbox's.
#:
#: Never pruned, and that is fine rather than a leak waiting to happen: there
#: is at most one connection per provider and one provider, so this dictionary
#: holds one entry for the life of the process and gains another only when a
#: connection is deleted and remade.
_refresh_locks: dict[str, asyncio.Lock] = {}


def _lock_for(connection_id: Any) -> asyncio.Lock:
    return _refresh_locks.setdefault(str(connection_id), asyncio.Lock())


class DropboxClient:
    """Talks to Dropbox on behalf of one stored connection.

    Owns exactly one non-obvious behaviour: **the access token is renewed
    without the caller knowing**. A caller asks for folders; if the stored
    token has expired the client refreshes first, and if Dropbox rejects a
    token the client believed was live it refreshes once and retries the
    request once — never twice, because a second 401 after a fresh token is a
    real failure and retrying it is a loop.

    The refreshed credential is re-sealed and **committed** on the way past.
    That is a write on what is usually a read path, and it is deliberate: a
    token arc has already spent a round trip obtaining is worth keeping even if
    the request it was obtained for then fails.
    """

    def __init__(
        self, session: AsyncSession, connection: ConnectionRow, *, app_key: str
    ) -> None:
        self._session = session
        self._connection = connection
        self._app_key = app_key

    # --- public calls --------------------------------------------------------

    async def list_folders(self, path: str) -> list[DropboxFolder]:
        """Every folder directly under ``path`` (``""`` is the Dropbox root)."""
        return list((await self.list_entries(path)).folders)

    async def list_entries(self, path: str) -> DropboxListing:
        """Everything directly under ``path``, folders and files alike.

        Follows `has_more` to the end. A folder holding a thousand entries is
        served in pages, and stopping at the first one would silently hide the
        folder the athlete is looking for — or, for a count, report a fraction
        of what is there as if it were the total. This returns everything or
        raises, never a truncated listing that looks complete.
        """
        folders: list[DropboxFolder] = []
        files: list[DropboxFile] = []
        body = await self._call(
            "/2/files/list_folder",
            {"path": path, "recursive": False, "include_deleted": False},
            path=path,
        )
        while True:
            for entry in body.get("entries", []):
                if entry.get(".tag") == "folder":
                    folders.append(
                        DropboxFolder(
                            path_lower=str(entry.get("path_lower") or ""),
                            name=str(entry.get("name") or ""),
                        )
                    )
                elif entry.get(".tag") == "file":
                    files.append(_file_from(entry))
            if not body.get("has_more"):
                return DropboxListing(folders=tuple(folders), files=tuple(files))
            body = await self._call(
                "/2/files/list_folder/continue", {"cursor": body["cursor"]}, path=path
            )

    async def changes(self, *, path: str, cursor: str | None) -> DropboxChanges:
        """One page of what has changed in ``path`` since ``cursor``.

        With no cursor this opens a listing of the folder (`list_folder`);
        with one it continues from it (`list_folder/continue`). Either way it
        returns **one page** and the cursor that follows it — following
        ``has_more`` here would tie the size of a transaction to how far behind
        the feed had fallen, and the caller's batch rule (`app.ingest.feeds`)
        is stated per page.

        ``recursive`` is false: a feed watches *a folder*, and a recursive
        watch on `/Apps/WahooFitness` would silently take on every subfolder
        the athlete later files rides into — including ones they moved a ride
        out of the way into.

        Non-file entries (``folder``, ``deleted``) are dropped here rather than
        by the caller: they are Dropbox's vocabulary for "this listing is a
        change list", and nothing above this layer should have to know it.

        Raises:
            DropboxCursorResetError: When Dropbox will not continue from this
                cursor and wants a fresh listing.
            DropboxPathNotFoundError: When the folder is gone.
            DropboxAuthError / DropboxRateLimitedError / DropboxUpstreamError:
                As :meth:`_call` classifies them.
        """
        body = (
            await self._call(
                "/2/files/list_folder",
                {"path": path, "recursive": False, "include_deleted": False},
                path=path,
            )
            if cursor is None
            else await self._call(
                "/2/files/list_folder/continue", {"cursor": cursor}, path=path
            )
        )
        return DropboxChanges(
            entries=tuple(
                _file_from(entry)
                for entry in body.get("entries", [])
                if entry.get(".tag") == "file"
            ),
            cursor=str(body.get("cursor") or ""),
            has_more=bool(body.get("has_more")),
        )

    async def download(self, file_id: str) -> bytes:
        """The bytes of one file, fetched by its Dropbox **id**.

        By id and not by path, deliberately: between the listing and this call
        the athlete may have renamed or moved the file, and a path-keyed
        download would then 409 on a file that is right there. The id is
        stable across both, and it is what the recording stores as its
        ``external_id``.

        Raises:
            DropboxPathNotFoundError: When the id no longer resolves — the
                file was deleted between the listing and this call.
            DropboxAuthError / DropboxRateLimitedError / DropboxUpstreamError:
                As :meth:`_content_failure` classifies them.
        """
        token = await self._access_token()
        async with _client() as http:
            response = await http.post(
                f"{CONTENT_BASE}/2/files/download",
                headers={
                    "Authorization": f"Bearer {token}",
                    # The argument goes in a header, and the response body is
                    # the file: this endpoint has no JSON request at all.
                    "Dropbox-API-Arg": json.dumps({"path": file_id}),
                },
            )
        if response.status_code == 200:
            return response.content
        raise self._content_failure(response, file_id)

    def _content_failure(self, response: httpx.Response, file_id: str) -> DropboxError:
        """Classify a failed download.

        No refresh-and-retry, unlike :meth:`_call`: a download is issued from
        inside a batch the caller will replay in full on any failure, so a 401
        here costs one replayed page and the refresh happens on the next
        listing. Retrying inside the download would double the bytes moved for
        a case the batch rule already covers.
        """
        if response.status_code == 429:
            return DropboxRateLimitedError(
                "Dropbox is rate-limiting arc", retry_after=_retry_after(response)
            )
        if response.status_code == 401:
            return DropboxAuthError("Dropbox rejected arc's access token")
        # `Dropbox-API-Result` carries the metadata JSON on a 200; on a
        # failure the content endpoint answers exactly like an RPC one, with
        # the tagged-error JSON in the body.
        summary = str(_json_or_empty(response).get("error_summary", ""))
        if response.status_code == 409 and "not_found" in summary:
            return DropboxPathNotFoundError(file_id)
        return DropboxUpstreamError(
            f"Dropbox answered {response.status_code} downloading {file_id}"
        )

    async def revoke(self) -> None:
        """Ask Dropbox to invalidate the credential arc is holding."""
        await self._call("/2/auth/token/revoke", None)

    # --- internals -----------------------------------------------------------

    def _credentials(self) -> dict[str, Any]:
        return EncryptedCredentials.unseal(self._connection.credentials)

    async def _access_token(self) -> str:
        """The current access token, refreshed first if it has expired."""
        expires_at = self._connection.access_token_expires_at
        if expires_at is not None and expires_at > dt.datetime.now(dt.UTC):
            return str(self._credentials()["access_token"])
        return await self._refresh()

    async def _refresh(self) -> str:
        """Exchange the refresh token for a new access token, once.

        Under a per-connection lock, and re-checking the expiry after
        acquiring it: the caller that waited for the lock wants the token the
        holder just stored, not a second refresh that would invalidate it.
        """
        async with _lock_for(self._connection.id):
            expires_at = self._connection.access_token_expires_at
            if expires_at is not None and expires_at > dt.datetime.now(dt.UTC):
                return str(self._credentials()["access_token"])

            stored = self._credentials()
            refresh_token = stored.get("refresh_token")
            if not refresh_token:
                await self._mark_needs_reauth(
                    "arc holds no refresh token for this Dropbox account"
                )
                raise DropboxAuthError(
                    "arc holds no refresh token for this Dropbox account; reconnect it"
                )

            async with _client() as http:
                response = await http.post(
                    TOKEN_ENDPOINT,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": self._app_key,
                    },
                )
            if response.status_code != 200:
                failure = _token_failure(response)
                if isinstance(failure, DropboxAuthError):
                    await self._mark_needs_reauth(
                        "Dropbox refused arc's refresh token. Reconnect the "
                        "account to authorize it again."
                    )
                raise failure

            grant = _grant_from(response.json())
            # A rotated refresh token replaces the old one; an absent one
            # leaves it in place, which is Dropbox's usual answer and the case
            # where dropping it would disconnect arc on its first renewal.
            stored["access_token"] = grant.access_token
            if grant.refresh_token:
                stored["refresh_token"] = grant.refresh_token
            self._connection.credentials = EncryptedCredentials.seal(stored)
            self._connection.access_token_expires_at = grant.expires_at
            self._connection.status = ConnectionStatus.CONNECTED
            self._connection.last_error = None
            await commit(self._session)
            return grant.access_token

    async def _mark_needs_reauth(self, detail: str) -> None:
        """Record a dead credential on the row, so the panel can say so."""
        self._connection.status = ConnectionStatus.NEEDS_REAUTH
        self._connection.last_error = detail
        self._connection.access_token_expires_at = None
        await commit(self._session)

    async def _call(
        self, endpoint: str, payload: Any, *, path: str = "", retried: bool = False
    ) -> dict[str, Any]:
        """One RPC call, with at most one refresh-and-retry behind it."""
        token = await self._access_token()
        headers = {"Authorization": f"Bearer {token}"}
        async with _client() as http:
            response = await http.post(
                f"{API_BASE}{endpoint}",
                headers=headers,
                json=payload if payload is not None else None,
            )

        if response.status_code == 429:
            raise DropboxRateLimitedError(
                "Dropbox is rate-limiting arc",
                retry_after=_retry_after(response),
            )
        if response.status_code == 401:
            if retried:
                await self._mark_needs_reauth(
                    "Dropbox rejected a freshly refreshed access token. "
                    "Reconnect the account."
                )
                raise DropboxAuthError(
                    "Dropbox rejected a freshly refreshed access token"
                )
            # The stored expiry said the token was live and Dropbox disagrees.
            # Forget the expiry so `_access_token` refreshes, then retry once.
            self._connection.access_token_expires_at = None
            return await self._call(endpoint, payload, path=path, retried=True)
        if response.status_code == 409:
            summary = str(_json_or_empty(response).get("error_summary", ""))
            # `reset` before `not_found`: it is the one 409 with a local
            # remedy, and the caller must not confuse it with a missing folder.
            if summary.startswith("reset"):
                raise DropboxCursorResetError(
                    "Dropbox will not continue from this listing cursor"
                )
            if "not_found" in summary:
                raise DropboxPathNotFoundError(path)
            raise DropboxUpstreamError(f"Dropbox refused the request: {summary}")
        if response.status_code >= 400:
            raise DropboxUpstreamError(
                f"Dropbox answered {response.status_code} for {endpoint}"
            )
        return _json_or_empty(response)


def _file_from(entry: dict[str, Any]) -> DropboxFile:
    """One `file` entry of a listing, as arc stores it."""
    return DropboxFile(
        id=str(entry.get("id") or ""),
        name=str(entry.get("name") or ""),
        path_lower=str(entry.get("path_lower") or ""),
        size=int(entry.get("size") or 0),
        rev=str(entry.get("rev") or ""),
        client_modified=_stamp(entry.get("client_modified")),
    )


def _stamp(raw: Any) -> dt.datetime | None:
    """Dropbox's `2026-08-16T05:30:00Z` as an aware UTC datetime, or None.

    Never raises. A stamp arc cannot read is a missing fact — it costs a
    tie-break between two folders — and letting it abort a listing would turn
    one malformed entry into a folder picker that shows nothing.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    return (
        parsed.replace(tzinfo=dt.UTC)
        if parsed.tzinfo is None
        else parsed.astimezone(dt.UTC)
    )


def _json_or_empty(response: httpx.Response) -> dict[str, Any]:
    """The response body as a dict; `{}` when there is none (a 200 revoke)."""
    if not response.content:
        return {}
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _retry_after(response: httpx.Response) -> float:
    """The `Retry-After` delay in seconds, defaulting to a polite minute."""
    raw = response.headers.get("Retry-After")
    try:
        return float(raw) if raw is not None else 60.0
    except ValueError:
        return 60.0

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


def _client() -> httpx.AsyncClient:
    """An HTTP client for one exchange, honouring the installed transport."""
    return httpx.AsyncClient(
        transport=_transport, timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=False
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
        """Every folder directly under ``path`` (``""`` is the Dropbox root).

        Follows `has_more` to the end. A folder holding a thousand entries is
        served in pages, and stopping at the first one would silently hide the
        folder the athlete is looking for — so this returns everything or
        raises, never a truncated list that looks complete.
        """
        folders: list[DropboxFolder] = []
        body = await self._call(
            "/2/files/list_folder",
            {"path": path, "recursive": False, "include_deleted": False},
            path=path,
        )
        while True:
            folders.extend(
                DropboxFolder(
                    path_lower=str(entry.get("path_lower") or ""),
                    name=str(entry.get("name") or ""),
                )
                for entry in body.get("entries", [])
                if entry.get(".tag") == "folder"
            )
            if not body.get("has_more"):
                return folders
            body = await self._call(
                "/2/files/list_folder/continue", {"cursor": body["cursor"]}, path=path
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
            if "not_found" in summary:
                raise DropboxPathNotFoundError(path)
            raise DropboxUpstreamError(f"Dropbox refused the request: {summary}")
        if response.status_code >= 400:
            raise DropboxUpstreamError(
                f"Dropbox answered {response.status_code} for {endpoint}"
            )
        return _json_or_empty(response)


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

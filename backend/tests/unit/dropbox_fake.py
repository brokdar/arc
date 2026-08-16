"""A programmable stand-in for Dropbox's HTTP API.

There is no fake-upstream pattern anywhere else in this codebase, so this is
the first one: an `httpx.MockTransport` that answers the five endpoints the
connector talks to, records every request, and lets a test bend one answer at a
time without restating the other four.

Every assertion about "how many requests did arc make" — the refresh-once rule,
the follow-the-cursor rule, the no-refresh-on-429 rule — is an assertion about
:attr:`FakeDropbox.calls`, which is why the recording is the class's main job
and the canned bodies are only there to keep a test that is about something
else from having to write them.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl

import httpx

from app.connectors.dropbox import READ_SCOPES

#: Endpoint paths, as they appear on the recorded calls.
TOKEN_PATH = "/oauth2/token"
ACCOUNT_PATH = "/2/users/get_current_account"
LIST_FOLDER_PATH = "/2/files/list_folder"
LIST_FOLDER_CONTINUE_PATH = "/2/files/list_folder/continue"
REVOKE_PATH = "/2/auth/token/revoke"


@dataclass(frozen=True, slots=True)
class Call:
    """One request arc made, decomposed into what a test wants to assert on."""

    path: str
    #: Form fields, for the token endpoint (which is form-encoded, not JSON).
    form: dict[str, str]
    #: Parsed JSON body, for the RPC endpoints.
    body: Any
    headers: httpx.Headers


def folder_entry(name: str, path_lower: str) -> dict[str, Any]:
    """A `folder` entry as `list_folder` returns it."""
    return {
        ".tag": "folder",
        "name": name,
        "path_lower": path_lower,
        "path_display": path_lower,
        "id": f"id:{name}",
    }


def file_entry(name: str, path_lower: str) -> dict[str, Any]:
    """A `file` entry as `list_folder` returns it."""
    return {
        ".tag": "file",
        "name": name,
        "path_lower": path_lower,
        "id": f"id:{name}",
        "size": 1024,
        "rev": "0123456789abcdef",
    }


@dataclass
class FakeDropbox:
    """Dropbox as far as `app.connectors.dropbox` can tell."""

    calls: list[Call] = field(default_factory=list)

    # --- the credential the fake hands out -----------------------------------
    access_token: str = "access-token-1"
    refresh_token: str | None = "refresh-token-1"
    expires_in: int = 14_400
    granted_scopes: tuple[str, ...] = tuple(sorted(READ_SCOPES))
    #: When set, the token endpoint refuses any exchange presenting a different
    #: `code_verifier` — which is how a superseded PKCE flow is modelled.
    expected_verifier: str | None = None
    #: `invalid_grant`, `invalid_request`, … Applied to the next token call.
    token_error: str | None = None

    # --- the account -----------------------------------------------------
    display_name: str = "Ada Lovelace"
    email: str = "ada@example.com"

    # --- the folder listing -------------------------------------------------
    #: One dict per page, in the order `list_folder` / `…/continue` serve them.
    pages: list[dict[str, Any]] | None = None
    #: Status + body overrides, keyed by path, consumed one call at a time.
    scripted: dict[str, list[httpx.Response]] = field(default_factory=dict)
    #: Raised instead of answering, keyed by path — a network failure.
    raises: dict[str, Exception] = field(default_factory=dict)

    def script(self, path: str, *responses: httpx.Response) -> None:
        """Queue explicit responses for ``path``, used before the defaults."""
        self.scripted.setdefault(path, []).extend(responses)

    def calls_to(self, path: str) -> list[Call]:
        """Every recorded call to one endpoint."""
        return [call for call in self.calls if call.path == path]

    @property
    def transport(self) -> httpx.MockTransport:
        """The transport to install with `dropbox.set_transport`."""
        return httpx.MockTransport(self._respond)

    # --- internals -----------------------------------------------------------

    def _respond(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        raw = request.content
        form: dict[str, str] = {}
        body: Any = None
        if request.headers.get("content-type", "").startswith(
            "application/x-www-form-urlencoded"
        ):
            form = dict(parse_qsl(raw.decode()))
        elif raw:
            try:
                body = json.loads(raw)
            except ValueError:
                body = raw
        self.calls.append(Call(path, form, body, request.headers))

        if (failure := self.raises.get(path)) is not None:
            raise failure
        queued = self.scripted.get(path)
        if queued:
            return queued.pop(0)

        handler: Callable[[Call], httpx.Response] | None = {
            TOKEN_PATH: self._token,
            ACCOUNT_PATH: self._account,
            LIST_FOLDER_PATH: self._list_folder,
            LIST_FOLDER_CONTINUE_PATH: self._list_folder,
            REVOKE_PATH: self._revoke,
        }.get(path)
        if handler is None:
            return httpx.Response(404, json={"error_summary": f"no route {path}"})
        return handler(self.calls[-1])

    def _token(self, call: Call) -> httpx.Response:
        if self.token_error is not None:
            error, self.token_error = self.token_error, None
            return httpx.Response(
                400, json={"error": error, "error_description": "refused by the fake"}
            )
        if (
            self.expected_verifier is not None
            and call.form.get("grant_type") == "authorization_code"
            and call.form.get("code_verifier") != self.expected_verifier
        ):
            return httpx.Response(
                400,
                json={
                    "error": "invalid_grant",
                    "error_description": "code_verifier does not match",
                },
            )
        payload: dict[str, Any] = {
            "access_token": self.access_token,
            "token_type": "bearer",
            "expires_in": self.expires_in,
            "scope": " ".join(self.granted_scopes),
            "account_id": "dbid:ada",
        }
        if self.refresh_token is not None:
            payload["refresh_token"] = self.refresh_token
        return httpx.Response(200, json=payload)

    def _account(self, _call: Call) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "account_id": "dbid:ada",
                "name": {"display_name": self.display_name},
                "email": self.email,
            },
        )

    def _list_folder(self, _call: Call) -> httpx.Response:
        pages = self.pages if self.pages is not None else [_DEFAULT_PAGE]
        index = min(
            len(self.calls_to(LIST_FOLDER_CONTINUE_PATH))
            + len(self.calls_to(LIST_FOLDER_PATH))
            - 1,
            len(pages) - 1,
        )
        return httpx.Response(200, json=pages[index])

    def _revoke(self, _call: Call) -> httpx.Response:
        return httpx.Response(200, json={})


_DEFAULT_PAGE: dict[str, Any] = {
    "entries": [
        folder_entry("Apps", "/apps"),
        file_entry("ride.fit", "/ride.fit"),
        folder_entry("Photos", "/photos"),
    ],
    "cursor": "cursor-1",
    "has_more": False,
}


def page(
    *entries: dict[str, Any], cursor: str = "cursor", has_more: bool = False
) -> dict[str, Any]:
    """One `list_folder` page."""
    return {"entries": list(entries), "cursor": cursor, "has_more": has_more}


def rate_limited(retry_after: str = "42") -> httpx.Response:
    """Dropbox's 429, with the delay it wants arc to wait."""
    return httpx.Response(
        429,
        headers={"Retry-After": retry_after},
        json={
            "error_summary": "too_many_requests/...",
            "error": {".tag": "too_many_requests"},
        },
    )


def expired_access_token() -> httpx.Response:
    """Dropbox's 401 for an access token it considers dead."""
    return httpx.Response(
        401,
        json={
            "error_summary": "expired_access_token/",
            "error": {".tag": "expired_access_token"},
        },
    )


def path_not_found(path: str) -> httpx.Response:
    """Dropbox's 409 for a path that is not there."""
    return httpx.Response(
        409,
        json={
            "error_summary": f"path/not_found/{path}",
            "error": {".tag": "path", "path": {".tag": "not_found"}},
        },
    )

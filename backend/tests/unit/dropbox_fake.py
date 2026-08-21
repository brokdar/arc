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
DOWNLOAD_PATH = "/2/files/download"
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


def folder_entry(
    name: str, path_lower: str, *, path_display: str | None = None
) -> dict[str, Any]:
    """A `folder` entry as `list_folder` returns it.

    ``path_display`` defaults to ``path_lower`` because most tests here are
    not about casing and an all-lowercase Dropbox is a real one. A test that
    *is* about casing passes the athlete's own spelling, which is the only
    way to catch a reader that projected the wrong field.
    """
    return {
        ".tag": "folder",
        "name": name,
        "path_lower": path_lower,
        "path_display": path_display if path_display is not None else path_lower,
        "id": f"id:{name}",
    }


#: The `client_modified` a file entry carries unless a test says otherwise.
#:
#: Not omitted: Dropbox stamps every file in a listing, and a fake that left
#: the field out would let a reader that never parses it pass.
DEFAULT_CLIENT_MODIFIED = "2026-01-01T00:00:00Z"


def file_entry(
    name: str,
    path_lower: str,
    *,
    size: int = 1024,
    entry_id: str | None = None,
    rev: str = "0123456789abcdef",
    client_modified: str = DEFAULT_CLIENT_MODIFIED,
    path_display: str | None = None,
) -> dict[str, Any]:
    """A `file` entry as `list_folder` returns it."""
    return {
        ".tag": "file",
        "name": name,
        "path_lower": path_lower,
        "path_display": path_display if path_display is not None else path_lower,
        "id": entry_id or f"id:{name}",
        "size": size,
        "rev": rev,
        "client_modified": client_modified,
        # Dropbox returns both; arc reads `client_modified`, which is when the
        # head unit wrote the ride rather than when Dropbox received it.
        "server_modified": client_modified,
    }


def _is_probe(call: Call) -> bool:
    """Whether a call is `complete`'s read probe rather than a real listing.

    `limit` is what tells them apart, and only the probe sends it: arc's
    folder listings ask for everything under a path and follow `has_more` to
    the end, while the probe asks for one entry and ignores the rest.
    """
    return call.path == LIST_FOLDER_PATH and (call.body or {}).get("limit") is not None


def deleted_entry(name: str, path_lower: str) -> dict[str, Any]:
    """A `deleted` entry, which is what a change list says about a removal."""
    return {".tag": "deleted", "name": name, "path_lower": path_lower}


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
    #: Listing answers keyed by the cursor arc presented — ``None`` for the
    #: opening `list_folder` call.
    #:
    #: The feed poll's whole contract is about *which* cursor is presented next
    #: (a replayed batch presents the same one, a give-up presents the one
    #: after), and the call-counting :attr:`pages` list cannot express that: it
    #: walks forward on every call however the poll rewound. Non-destructive on
    #: purpose — presenting one cursor twice serves the same page twice, which
    #: is exactly what a replay is — and an **unknown** cursor is answered with
    #: Dropbox's `reset`, which is also what Dropbox does.
    by_cursor: dict[str | None, dict[str, Any]] | None = None
    #: A whole Dropbox: normalised folder path → the entries under it, root at
    #: ``""``. Set this instead of :attr:`pages` when a test needs *several*
    #: folders to answer differently — discovery lists one folder per candidate
    #: and the call-counting :attr:`pages` list cannot express that, because it
    #: walks forward on every call whatever path was asked for. A path that is
    #: not a key answers Dropbox's `path/not_found`.
    tree: dict[str, list[dict[str, Any]]] | None = None
    #: Entries per page when serving :attr:`tree`. ``None`` serves each folder
    #: in one page; a number makes Dropbox paginate, which is how the
    #: follow-`has_more` rule is exercised against a real listing.
    tree_page_size: int | None = None
    #: Responses that pre-empt an opening `list_folder`, keyed by its `path`.
    #: One feed failing while another succeeds is only expressible per path.
    #: Matched case-insensitively, as Dropbox matches paths.
    list_failures: dict[str, httpx.Response] = field(default_factory=dict)

    # --- the file contents ---------------------------------------------------
    #: Bytes served by `/2/files/download`, keyed by the entry's Dropbox id.
    files: dict[str, bytes] = field(default_factory=dict)
    #: Responses that pre-empt a download, keyed by the entry's Dropbox id.
    download_failures: dict[str, httpx.Response] = field(default_factory=dict)
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
            DOWNLOAD_PATH: self._download,
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

    def _list_folder(self, call: Call) -> httpx.Response:
        if _is_probe(call):
            return self._probe()
        if call.path == LIST_FOLDER_PATH:
            wanted = str((call.body or {}).get("path", ""))
            failure = self.list_failures.get(wanted) or self.list_failures.get(
                wanted.lower()
            )
            if failure is not None:
                return failure
        if self.tree is not None:
            return self._from_tree(call)
        if self.by_cursor is not None:
            presented = (
                str((call.body or {}).get("cursor"))
                if call.path == LIST_FOLDER_CONTINUE_PATH
                else None
            )
            answer = self.by_cursor.get(presented)
            if answer is None:
                return reset_cursor()
            return httpx.Response(200, json=answer)
        pages = self.pages if self.pages is not None else [_DEFAULT_PAGE]
        # Probes are excluded from the walk, not just from the answer: the
        # index counts calls, so leaving `complete`'s probe in it would serve
        # every test that connects and then lists one page too far.
        index = min(len(self._listings()) - 1, len(pages) - 1)
        return httpx.Response(200, json=pages[index])

    def _listings(self) -> list[Call]:
        """Recorded calls that walk a folder, probes excluded."""
        return [
            call
            for call in self.calls
            if call.path in {LIST_FOLDER_PATH, LIST_FOLDER_CONTINUE_PATH}
            and not _is_probe(call)
        ]

    def _probe(self) -> httpx.Response:
        """Answer `complete`'s `limit=1` probe without moving the listing on.

        Its own branch because a probe is not a listing. It asks for one entry
        of the root only to find out whether a scoped call succeeds at all,
        and serving it out of :attr:`pages` or :attr:`tree` would spend the
        page the *next* call is written against — every test that connects and
        then lists would read one page too far. Overridden the same way any
        other answer is, with `script(LIST_FOLDER_PATH, ...)` or
        :attr:`raises`, which is how a refused or unanswered probe is written.
        """
        return httpx.Response(
            200, json=page(folder_entry("Apps", "/apps"), has_more=True)
        )

    def _from_tree(self, call: Call) -> httpx.Response:
        """Serve one page of :attr:`tree`, paginating like Dropbox does.

        The cursor is ``"<path>#<offset>"`` and carries the folder with it, so
        a continuation is answered from the same directory the opening call
        opened — a caller that lost track of which listing it was walking would
        otherwise be handed the wrong folder's entries and never know.
        """
        assert self.tree is not None
        body = call.body or {}
        if call.path == LIST_FOLDER_CONTINUE_PATH:
            path, _, offset_text = str(body.get("cursor") or "").rpartition("#")
            offset = int(offset_text) if offset_text.isdigit() else -1
            if offset < 0 or path not in self.tree:
                return reset_cursor()
        else:
            path = str(body.get("path", "")).lower()
            offset = 0
            if path not in self.tree:
                return path_not_found(path)

        entries = self.tree[path]
        end = (
            len(entries)
            if self.tree_page_size is None
            else min(offset + self.tree_page_size, len(entries))
        )
        return httpx.Response(
            200,
            json={
                "entries": entries[offset:end],
                "cursor": f"{path}#{end}",
                "has_more": end < len(entries),
            },
        )

    def _download(self, call: Call) -> httpx.Response:
        # The content endpoint carries its argument in a header and answers
        # with raw bytes; nothing about it is JSON except the failures.
        argument = json.loads(call.headers.get("Dropbox-API-Arg") or "{}")
        wanted = str(argument.get("path", ""))
        if (failure := self.download_failures.get(wanted)) is not None:
            return failure
        content = self.files.get(wanted)
        if content is None:
            return path_not_found(wanted)
        return httpx.Response(200, content=content)

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


def missing_scope(required_scope: str = "files.metadata.read") -> httpx.Response:
    """Dropbox's 401 for a call the grant carries no scope for.

    The same status code as a dead access token, and a different body — which
    is the whole reason this exists as its own answer: a grant that lists no
    file scopes reaches every read endpoint and is refused here, not at the
    token exchange.
    """
    return httpx.Response(
        401,
        json={
            "error_summary": f"missing_scope/{required_scope}",
            "error": {".tag": "missing_scope", "required_scope": required_scope},
        },
    )


def reset_cursor() -> httpx.Response:
    """Dropbox's 409 for a cursor it will no longer continue from."""
    return httpx.Response(
        409,
        json={
            "error_summary": "reset/...",
            "error": {".tag": "reset"},
        },
    )


def server_error() -> httpx.Response:
    """Dropbox having a bad day: a 503 arc did not cause and cannot fix."""
    return httpx.Response(503, text="service unavailable")


def path_not_found(path: str) -> httpx.Response:
    """Dropbox's 409 for a path that is not there."""
    return httpx.Response(
        409,
        json={
            "error_summary": f"path/not_found/{path}",
            "error": {".tag": "path", "path": {".tag": "not_found"}},
        },
    )

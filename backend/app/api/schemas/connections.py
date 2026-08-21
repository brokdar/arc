"""Request/response schemas for cloud connections and the folders arc watches.

Two things are deliberately absent from every shape here, and their absence is
the contract: the **credential** in any form — access token, refresh token,
verifier, encryption key — and the **filesystem**. What a client gets is the
account label the athlete recognises, the scopes in the vocabulary Dropbox
grants them in, and the remote paths; what arc keeps is everything else.
"""

import datetime as dt
import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from app.core.config import SettingSource
from app.domain.connections import ConnectionProvider, ConnectionStatus
from app.persistence.connections import (
    MAX_APP_KEY_LENGTH,
    MAX_REDIRECT_URI_LENGTH,
)


class DropboxSetupRead(BaseModel):
    """Whether Dropbox can be connected yet, and on whose app key.

    Nullable `source` is load-bearing rather than an omission: `null` is a
    state the add-integration flow renders — the registration checklist — and
    not the absence of an answer.
    """

    #: False until an app key exists in either source. The flow offers the
    #: registration checklist rather than a connect button that would fail.
    app_key_set: bool
    #: Which key is in force. `stored` is removable from Settings;
    #: `environment` comes from `DROPBOX__APP_KEY` and is not.
    source: SettingSource | None


class DropboxAppKeySubmit(BaseModel):
    """The app key from the athlete's own Dropbox app registration.

    Not a secret: a Dropbox app key is a public OAuth client id, which is why
    it is stored in the clear (see `ProviderAppRow`) and may be echoed back as
    a source rather than a value.
    """

    #: Surrounding whitespace is stripped by the service — this arrives by
    #: paste from a console page. A key that is nothing but whitespace is
    #: refused there, with the registration steps named.
    app_key: str = Field(min_length=1, max_length=MAX_APP_KEY_LENGTH)


class DropboxAuthorizationStart(BaseModel):
    """Where Dropbox should send the athlete back to, if it can send them.

    The whole body is optional, and its absence is the **paste** flow — the
    one arc has always had, still reachable and still the only one that works
    on a deployment served over plain http at a LAN address.
    """

    #: The callback page in the browser that is asking, origin included:
    #: `https://arc.example.com/settings/dropbox/callback`. Supplied by the
    #: page rather than read from a proxy header, and validated server-side —
    #: see `ConnectionService.start_dropbox_authorization`.
    #:
    #: `SkipJsonSchema[None]` rather than a nullable field: omitting it is how
    #: a client says "paste flow", and `null` is not a second way to say the
    #: same thing (`.claude/rules/api-nullability.md`). The length bound is on
    #: the member, not the union, or pydantic measures `None`.
    redirect_uri: (
        Annotated[str, Field(min_length=1, max_length=MAX_REDIRECT_URI_LENGTH)]
        | SkipJsonSchema[None]
    ) = None


class DropboxAuthorizationRead(BaseModel):
    """The link the athlete opens, and the deadline on the code they bring back."""

    #: Carries the PKCE challenge, and — when the browser's origin is one
    #: Dropbox will redirect to — a `redirect_uri` and a `state`. Followed in
    #: this tab when it redirects, opened in a new one when it does not.
    authorize_url: str
    #: After this, the code is refused and the flow must be restarted.
    expires_at: dt.datetime


class DropboxCodeSubmit(BaseModel):
    """The authorization code Dropbox handed back, pasted or redirected."""

    #: Whitespace and a trailing newline are stripped by the service: a code
    #: copied off a web page normally carries them, and refusing it would be
    #: arc failing at the one manual step it asked for.
    code: str = Field(min_length=1, max_length=500)
    #: The nonce Dropbox round-tripped, from the callback page's query string.
    #: Omitted by the paste flow, which has none — and a paste flow completed
    #: *with* one is refused, so this is not a field a client may invent.
    #:
    #: Unbounded, unlike `code` and unlike the column arc stores its own nonce
    #: in: every verdict on a supplied `state` belongs to
    #: `ConnectionService._check_state`, because a wrong one does not merely
    #: fail — it **deletes** the pending authorization. A `min_length` or
    #: `max_length` here would answer 422 without that deletion, so an empty
    #: or over-long value would leave the flow sitting there redeemable and
    #: hand an attacker an unlimited supply of guesses. Omission still means
    #: "paste flow"; `null` is not a second way to say it.
    state: str | SkipJsonSchema[None] = None


class FeedRead(BaseModel):
    """One folder arc watches, and its polling state."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    connection_id: uuid.UUID
    #: Normalised: lower-cased, no trailing slash. `""` is the Dropbox root.
    remote_path: str
    enabled: bool
    #: Dropbox's listing cursor. Null until the first poll.
    cursor: str | None
    #: Consecutive failed attempts on the current cursor; back to zero after
    #: any resolved batch.
    cursor_attempts: int
    #: When arc last heard from Dropbox for this feed **at all** — not when a
    #: ride last arrived. A stale value is a broken pipe; a fresh one with no
    #: new sessions is a rest week, and the panel must not confuse the two.
    last_delivery_at: dt.datetime | None
    #: What the last poll could not do, in the athlete's words. Cleared by the
    #: next poll that succeeds.
    last_error: str | None
    created_at: dt.datetime


class ConnectionRead(BaseModel):
    """One connected account, with the folders arc watches on it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: ConnectionProvider
    #: `error` means arc cannot read its own stored credential (the encryption
    #: key moved); `needs_reauth` means Dropbox refused it. Different remedies,
    #: so different states — see `app.domain.connections.ConnectionStatus`.
    status: ConnectionStatus
    #: Display name and email, as the athlete would recognise the account.
    account_label: str | None
    #: What Dropbox **granted**, not what arc asked for.
    scopes: list[str]
    last_error: str | None
    #: When arc last saw this credential actually read the athlete's files —
    #: what makes `status` an observation rather than a claim. `null` means
    #: **nobody has checked yet**, which is a state a client renders as such;
    #: substituting `created_at` for it would report a verification that never
    #: happened. See `app.persistence.connections.ConnectionRow`.
    last_verified_at: dt.datetime | None
    created_at: dt.datetime
    updated_at: dt.datetime
    feeds: list[FeedRead]


class DropboxConnectionRead(ConnectionRead):
    """The connection a completion just stored, and what arc proved about it.

    A subclass rather than a field on `ConnectionRead`: the note is about one
    completion, not a property of the connection. A later `GET` has nothing to
    say about a check that ran once, minutes ago, and a nullable field on
    every connection read would invite the panel to render a stale answer as
    the current one.
    """

    #: `null` when arc listed the athlete's Dropbox during the connect and it
    #: worked — the ordinary case, and the one the confirmation is written
    #: for. A sentence when Dropbox could not answer that check at all: the
    #: authorization code was already spent, so the connection is stored
    #: unproven rather than thrown away, and this is what says so.
    verification_note: str | None


class ConnectionList(BaseModel):
    """Every connection arc holds.

    A bare list rather than a page: there is one athlete and at most one
    connection per provider, so an offset here would be ceremony with no
    collection large enough to need it.
    """

    items: list[ConnectionRead]


class FolderRead(BaseModel):
    """One folder in a remote listing, in both spellings Dropbox keeps.

    The two are not interchangeable and the difference is the whole point:
    `path_lower` is the **identity** — what a feed row stores, what
    `uq_feeds_connection_id_remote_path` is written against, and what a client
    sends back to watch this folder — while `path_display` and `name` are the
    only forms that belong on screen. A picker rendering `path_lower` shows
    `/apps/wahoofitness` to an athlete looking at `/Apps/WahooFitness` in
    Dropbox, which reads as a case bug in arc and once cost a real run an hour
    chasing a case-sensitivity fault that did not exist.
    """

    #: Dropbox's own canonical spelling, and what a feed stores.
    path_lower: str
    #: The same folder as the athlete capitalised it.
    path_display: str
    #: What to show the athlete — the folder as they named it.
    name: str


class FolderList(BaseModel):
    """What is directly under one remote path: the subfolders, and the files.

    Never a 404 for an empty folder: a directory holding only files is a
    legitimate answer with `items: []`, and the picker says so rather than
    drawing an empty box.

    The counts describe the **current** folder only, and describe all of it —
    `ConnectionService.folders` follows Dropbox's cursor to the end before
    counting, so a client may render them as a total. Per-subfolder counts are
    deliberately absent: they would cost one Dropbox call per row.
    """

    #: The listed folder itself, in the athlete's capitalisation, for a
    #: breadcrumb. `""` is the Dropbox root. Derived from the entries in the
    #: same listing, so an **empty** folder echoes the requested path back
    #: rather than arc inventing a capitalisation for it.
    path_display: str
    items: list[FolderRead]
    #: Every file directly in this folder, whatever kind it is.
    file_count: int
    #: How many of those arc can read as a ride. The gap between the two is
    #: what the athlete recognises their own folder by.
    supported_file_count: int

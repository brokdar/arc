"""Request/response schemas for cloud connections and the folders arc watches.

Two things are deliberately absent from every shape here, and their absence is
the contract: the **credential** in any form — access token, refresh token,
verifier, encryption key — and the **filesystem**. What a client gets is the
account label the athlete recognises, the scopes in the vocabulary Dropbox
grants them in, and the remote paths; what arc keeps is everything else.
"""

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.domain.connections import ConnectionProvider, ConnectionStatus


class DropboxAuthorizationRead(BaseModel):
    """The link the athlete opens, and the deadline on the code they bring back."""

    #: Opened in a new tab. Carries the PKCE challenge and no redirect URI.
    authorize_url: str
    #: After this, the pasted code is refused and the flow must be restarted.
    expires_at: dt.datetime


class DropboxCodeSubmit(BaseModel):
    """The authorization code Dropbox showed the athlete, pasted back."""

    #: Whitespace and a trailing newline are stripped by the service: a code
    #: copied off a web page normally carries them, and refusing it would be
    #: arc failing at the one manual step it asked for.
    code: str = Field(min_length=1, max_length=500)


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
    created_at: dt.datetime
    updated_at: dt.datetime
    feeds: list[FeedRead]


class ConnectionList(BaseModel):
    """Every connection arc holds.

    A bare list rather than a page: there is one athlete and at most one
    connection per provider, so an offset here would be ceremony with no
    collection large enough to need it.
    """

    items: list[ConnectionRead]


class FolderRead(BaseModel):
    """One folder in a remote listing."""

    #: Dropbox's own canonical spelling, and what a feed stores.
    path_lower: str
    #: What to show the athlete — the folder as they named it.
    name: str


class FolderList(BaseModel):
    """The folders directly under one remote path.

    Never a 404 for an empty folder: a directory holding only files is a
    legitimate answer with `items: []`, and the picker says so rather than
    drawing an empty box.
    """

    items: list[FolderRead]

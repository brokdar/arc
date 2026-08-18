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
from app.persistence.connections import MAX_REMOTE_PATH_LENGTH


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


class FolderCandidateRead(BaseModel):
    """A folder arc thinks the athlete's activity files are already in."""

    model_config = ConfigDict(from_attributes=True)

    #: Dropbox's own spelling, posted straight back as a feed's `remote_path`.
    path: str
    #: How many `.fit`, `.gpx` or `.tcx` files are directly in it. Always at
    #: least one — a folder holding none is left out, not reported as zero.
    activity_files: int
    #: When the newest of them was written by the device. Null when Dropbox
    #: reported no stamp arc could read, which costs a tie-break and nothing
    #: else.
    newest_at: dt.datetime | None


class FolderDiscoveryRead(BaseModel):
    """Where the rides look like they already are, and what may be in the way.

    A 200 with an empty `candidates` list is a real answer, not a 404: "arc
    looked and found no activity files anywhere it can see" is precisely what
    an athlete whose head unit has never uploaded should be told, and the
    manual browser is right there.
    """

    model_config = ConfigDict(from_attributes=True)

    #: Best first: most activity files, then most recently written.
    candidates: list[FolderCandidateRead]
    #: `"app_folder"` when an empty Dropbox *and* an absent `/Apps` together
    #: suggest the Dropbox app was registered with App-folder access; null when
    #: they do not. An inference, never a fact — no Dropbox API reports an
    #: app's access type — so the panel words it as a question the athlete can
    #: check rather than an accusation.
    access_type_suspect: str | None


class FeedCreate(BaseModel):
    """Start watching a folder on a connection."""

    connection_id: uuid.UUID
    #: Any spelling; stored normalised. `""` is the Dropbox root, which is
    #: legal — watching all of Dropbox is unwise, not forbidden.
    remote_path: str = Field(default="", max_length=MAX_REMOTE_PATH_LENGTH)


class FeedUpdate(BaseModel):
    """Turn a feed's polling on or off, keeping its cursor."""

    enabled: bool

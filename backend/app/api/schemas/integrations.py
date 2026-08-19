"""Request/response schemas for the sources arc collects from.

Every entry in `IntegrationList` answers the three questions Settings exists to
answer, and the field names say which is which: `data_kinds` is **what** it
brings in, `folders` / `local` is **where from**, and `prompt` is what the
athlete still has to do. A panel that had to infer any of the three from the
others would infer it differently the next time somebody edited it.

The credential never appears here, exactly as in
`app.api.schemas.connections`: what a client gets is the account label the
athlete recognises and the folder paths, and nothing that could be replayed
against Dropbox.
"""

import datetime as dt
import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from app.domain.connections import ConnectionStatus, FeedDeliveryState
from app.domain.integrations import (
    DataKind,
    IntegrationKind,
    StorageProvider,
    TransportKind,
)
from app.persistence.connections import MAX_REMOTE_PATH_LENGTH


class IntegrationFolderRead(BaseModel):
    """One folder an integration is collected through, and how it is doing."""

    model_config = ConfigDict(from_attributes=True)

    feed_id: uuid.UUID
    connection_id: uuid.UUID
    storage: StorageProvider
    #: Normalised: lower-cased, no trailing slash. `""` is the Dropbox root.
    remote_path: str
    enabled: bool
    #: One word for how this folder is doing — see `FeedDeliveryState`. A
    #: folder that has never delivered is *not* reported as zero deliveries.
    state: FeedDeliveryState
    #: When arc last heard from the provider for this folder **at all**, not
    #: when a ride last arrived.
    last_delivery_at: dt.datetime | None
    last_error: str | None
    #: The credential's own state, and its fault if it has one: a connection
    #: arc cannot read silences every folder under it, and the panel must show
    #: that once rather than as three quiet folders.
    connection_status: ConnectionStatus
    connection_error: str | None
    account_label: str | None


class LocalDropRead(BaseModel):
    """The local drop's whole configuration: where it looks, how often."""

    model_config = ConfigDict(from_attributes=True)

    #: Resolved and absolute — a relative `DATA__ROOT` tells the athlete
    #: nothing about where on the server to drop a file.
    inbox_path: str
    scan_interval_seconds: int


class IntegrationRead(BaseModel):
    """One source arc collects from, as the settings panel renders it."""

    model_config = ConfigDict(from_attributes=True)

    #: `"local_drop"` for the synthesized entry, otherwise a UUID string. A
    #: string rather than a UUID because the local drop has no row, and a UUID
    #: would promise one exists.
    id: str
    #: `null` for a folder configured before integrations existed. It is still
    #: collecting; only the athlete can say which source it is.
    kind: IntegrationKind | None
    display_name: str
    #: Which of arc's destinations this feeds — **never** a sport. See
    #: `app.domain.integrations.DataKind`.
    data_kinds: list[DataKind]
    transport: TransportKind
    storage: StorageProvider | None
    #: False for the local drop, which cannot be removed.
    removable: bool
    #: What the athlete still has to do, or `null` when nothing is pending.
    prompt: str | None
    #: Set only on the local drop.
    local: LocalDropRead | None
    folders: list[IntegrationFolderRead]


class IntegrationList(BaseModel):
    """Every source arc collects from, the local drop first.

    A bare list rather than a page: there is one athlete, and the number of
    sources is the number of devices they own.
    """

    items: list[IntegrationRead]


class TransportOffer(BaseModel):
    """One way the athlete may choose to collect an integration."""

    model_config = ConfigDict(from_attributes=True)

    kind: TransportKind
    #: Set iff `kind` is `cloud_folder`.
    storage: StorageProvider | None
    #: The folder this integration writes to by default, already in the
    #: spelling arc stores — the add flow offers it, and the athlete confirms.
    default_path: str | None


class CatalogueEntry(BaseModel):
    """One integration arc can actually deliver."""

    kind: IntegrationKind
    display_name: str
    data_kinds: list[DataKind]
    #: False for the local drop: it is always present, so offering it in the
    #: add flow would be offering something that cannot be done.
    addable: bool
    transports: list[TransportOffer]


class StorageStatusRead(BaseModel):
    """How ready a storage provider is to carry a cloud-folder transport.

    The add flow reads this to skip steps that are already done: no app key
    means the registration checklist, a key with no account means the connect
    ritual, and a connected account means going straight to the folder.
    """

    model_config = ConfigDict(from_attributes=True)

    provider: StorageProvider
    app_configured: bool
    connection_id: uuid.UUID | None
    account_label: str | None
    status: ConnectionStatus | None


class IntegrationCatalogue(BaseModel):
    """What arc can collect, and how ready each transport is to carry it."""

    items: list[CatalogueEntry]
    storage: list[StorageStatusRead]


class IntegrationCreate(BaseModel):
    """Add a source, and the first folder it is collected through."""

    kind: IntegrationKind
    transport: TransportKind
    #: Omitted means "the one account arc holds for this integration's storage
    #: provider". Typed `SkipJsonSchema[None]` rather than `| None` so the
    #: contract never advertises `null`: omission is the only way to say
    #: "pick it for me", and a literal null would be a second spelling of it
    #: (`.claude/rules/api-nullability.md`).
    connection_id: uuid.UUID | SkipJsonSchema[None] = None
    #: Any spelling; stored normalised. Omitted means the catalogue's default
    #: path for this transport.
    remote_path: (
        Annotated[str, Field(max_length=MAX_REMOTE_PATH_LENGTH)] | SkipJsonSchema[None]
    ) = None


class FolderUpdate(BaseModel):
    """Pause or resume one folder, keeping its cursor."""

    enabled: bool

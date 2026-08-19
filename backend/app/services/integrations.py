"""Use-cases for the sources arc collects from.

The question this layer answers is the one Settings asks: *what is arc
collecting, from where, and what does the athlete still have to do?* It reads
the catalogue (`app.domain.integrations`) for what a source is, the
`integrations` table for what the athlete added, and `feeds` for the folders
those integrations are collected through — and it hands the adapter one list in
which the local drop, a configured Wahoo and a folder nobody has classified all
look like the same kind of thing, because to the athlete they are.

Adding is a **single use-case**, not "create an integration, then create a
feed": those two rows are meaningless apart, and two calls would leave an
integration with no transport on the wire between them.
"""

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.domain.actor import Actor
from app.domain.connections import (
    ConnectionProvider,
    ConnectionStatus,
    FeedDeliveryState,
    normalise_remote_path,
)
from app.domain.integrations import (
    CATALOGUE,
    SYNTHESIZED_KINDS,
    DataKind,
    IntegrationKind,
    IntegrationSpec,
    StorageProvider,
    TransportKind,
    kind_for_default_path,
    ordered_data_kinds,
)
from app.persistence.audit import AuditRepository
from app.persistence.connections import ConnectionRepository, ConnectionRow, FeedRow
from app.persistence.db import commit
from app.persistence.integrations import IntegrationRepository, IntegrationRow
from app.services.connections import ConnectionService, delivery_state
from app.services.guardrails import check_write_cap
from app.services.ingest_settings import IngestSettingsService, LocalDropSettings

#: `entity_type` written on this use-case's audit rows.
INTEGRATION_ENTITY = "integration"

#: The id the synthesized local drop answers to.
#:
#: A string, not a UUID, and that is deliberate: nothing in the database has
#: this id, so a UUID would be a promise that a row exists somewhere. It is
#: also what makes `DELETE /integrations/local_drop` a 404 rather than a 422
#: about UUID syntax — the athlete asked to remove something that cannot be
#: removed, and the honest answer is "there is nothing there to delete".
LOCAL_DROP_ID = IntegrationKind.LOCAL_DROP.value


@dataclass(frozen=True, slots=True)
class FolderView:
    """One folder an integration is collected through."""

    feed_id: uuid.UUID
    connection_id: uuid.UUID
    storage: StorageProvider
    remote_path: str
    enabled: bool
    state: FeedDeliveryState
    last_delivery_at: dt.datetime | None
    last_error: str | None
    #: The credential's own state. A `needs_reauth` or `error` connection
    #: silences every folder under it for a reason no per-folder field can
    #: express, so the panel shows one fault instead of three silent folders.
    connection_status: ConnectionStatus
    connection_error: str | None
    account_label: str | None


@dataclass(frozen=True, slots=True)
class LocalDropView:
    """Where the local drop looks and how often — its whole configuration."""

    inbox_path: str
    scan_interval_seconds: int


@dataclass(frozen=True, slots=True)
class IntegrationView:
    """One entry in Settings: what it brings in, where from, what to configure."""

    id: str
    #: ``None`` for a folder configured before integrations existed. Not an
    #: error and not hidden: it is still collecting, and only the athlete can
    #: say which source it is.
    kind: IntegrationKind | None
    display_name: str
    data_kinds: tuple[DataKind, ...]
    transport: TransportKind
    storage: StorageProvider | None
    #: False for the local drop, which is synthesized and permanent.
    removable: bool
    #: What the athlete has left to do, or ``None`` when nothing is pending.
    prompt: str | None
    local: LocalDropView | None
    folders: tuple[FolderView, ...]


@dataclass(frozen=True, slots=True)
class StorageStatus:
    """Whether a storage provider is ready to carry an integration.

    Read by the add flow to decide which of its steps it may skip. Three states
    in two booleans, because they have three different remedies: no app key
    (register a Dropbox app), a key but no account (run the connect ritual), or
    connected (go straight to picking a folder).
    """

    provider: StorageProvider
    #: Whether an app key is configured in *either* source — stored in
    #: Settings or seeded by `DROPBOX__APP_KEY`. Read through
    #: `ConnectionService.app_key`, because a key the athlete just stored has
    #: to end the registration step without a restart.
    app_configured: bool
    connection_id: uuid.UUID | None
    account_label: str | None
    status: ConnectionStatus | None


@dataclass(frozen=True, slots=True)
class IntegrationProposal:
    """A folder arc found, named as the **integration** behind it.

    The point of the whole surface, and the reason this is not
    `FolderCandidate` with two more fields: what discovery hands the athlete is
    "Wahoo — 342 rides, newest 16.08 20:12", not a filesystem path they have to
    recognise. Every field here is either what to show or what to post back.
    """

    #: ``None`` when no catalogue integration writes to this folder. Nothing is
    #: guessed from the folder's name: the athlete picks the source, and a
    #: wrong guess would be stored as a fact and never questioned again.
    kind: IntegrationKind | None
    #: The integration's name, or — with no kind — the folder itself, because
    #: that is the only true thing arc can call it.
    display_name: str
    #: Posted back verbatim, which is why it is on the proposal rather than
    #: re-derived by the panel from whatever connection it happens to be
    #: rendering: accepting is one click and one write path.
    connection_id: uuid.UUID
    transport: TransportKind
    #: Normalised, so accepting stores the same spelling the folder-clash
    #: refusal compares against.
    path: str
    activity_files: int
    newest_at: dt.datetime | None
    #: ``True`` when arc is already collecting this folder on this account.
    #: Shown rather than hidden — "arc already has these" is the answer to the
    #: question the athlete asked — but with no control to add it again.
    configured: bool


@dataclass(frozen=True, slots=True)
class DiscoveredIntegrations:
    """What arc found on one connection, and what may be in the way."""

    #: Best first: most activity files, then most recently written.
    proposals: tuple[IntegrationProposal, ...]
    #: See `ConnectionService.discover_folders` — an inference about the
    #: Dropbox app's access type, never a fact, and ``None`` unless the
    #: evidence is unambiguous.
    access_type_suspect: str | None


@dataclass(frozen=True, slots=True)
class AddedIntegration:
    """An add's result, and whether it created the integration or grew one."""

    view: IntegrationView
    created: bool


class IntegrationService:
    """Use-cases for integrations. Raises AppError subclasses."""

    def __init__(
        self,
        session: AsyncSession,
        repository: IntegrationRepository,
        connections: ConnectionRepository,
        connection_service: ConnectionService,
        ingest_settings: IngestSettingsService,
        audit: AuditRepository,
    ) -> None:
        self._session = session
        self._repository = repository
        self._connections = connections
        self._connection_service = connection_service
        self._ingest_settings = ingest_settings
        self._audit = audit

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(
            session,
            IntegrationRepository(session),
            ConnectionRepository(session),
            ConnectionService.from_session(session),
            IngestSettingsService.from_session(session),
            AuditRepository(session),
        )

    # --- reads ---------------------------------------------------------------

    async def list(self) -> tuple[IntegrationView, ...]:
        """Every source arc collects from, local drop first.

        **The local drop is synthesized here, not stored.** It has no row in
        `integrations` and never will: `data/inbox/` has been swept since
        WP-4.3 whether or not anybody configured anything, so a row for it
        would be one the athlete could delete — leaving a running sweep with
        no entry in Settings describing it and no way to bring the entry back.
        Its id is :data:`LOCAL_DROP_ID`, which matches nothing a `DELETE` can
        find.

        A folder no integration owns gets an entry of its own with
        ``kind=None`` and a prompt. It is *not* folded into whichever
        integration looks closest: guessing would file a folder under a source
        the athlete never chose, and nothing afterwards would question it.
        """
        connections = {row.id: row for row in await self._connection_service.list()}
        stored = [
            self._stored(row, connections) for row in await self._repository.list()
        ]
        loose = [
            self._unclassified(feed, connections)
            for feed in await self._repository.unclassified_feeds()
        ]
        return (
            self._local_drop(await self._ingest_settings.read()),
            *stored,
            *loose,
        )

    async def storage_statuses(self) -> tuple[StorageStatus, ...]:
        """How ready each storage provider is to carry a cloud-folder transport."""
        connections = {
            row.provider: row for row in await self._connection_service.list()
        }
        app_key = await self._connection_service.app_key(ConnectionProvider.DROPBOX)
        statuses = []
        for provider in StorageProvider:
            row = connections.get(provider)
            statuses.append(
                StorageStatus(
                    provider=provider,
                    app_configured=bool(app_key),
                    connection_id=row.id if row else None,
                    account_label=row.account_label if row else None,
                    status=row.status if row else None,
                )
            )
        return tuple(statuses)

    async def propose(self, connection_id: uuid.UUID) -> DiscoveredIntegrations:
        """Name the integrations behind the folders on one connection.

        The read the operator's complaint was about. `ConnectionService`
        answers *where the activity files are* — it walks the root and `/Apps`,
        counts and ranks — and this layer answers *what that folder is*, which
        is the only half the athlete can act on. The split follows the module
        boundary the catalogue lives on: the connection layer knows Dropbox and
        nothing about Wahoo, and re-deriving a display name in the adapter
        would put the catalogue in three places.

        A proposal is accepted by posting it to `POST /api/v1/integrations` —
        the **same** call the manual path makes, so there is one write path and
        one set of refusals. Nothing here writes anything.

        Raises:
            NotFoundError: When the connection does not exist.
            ConflictError: When the credential needs re-authorizing.
            RateLimitedError: When Dropbox is throttling arc.
            ValidationError: When arc cannot read its own credential, or
                Dropbox failed in a way arc did not cause.
        """
        found = await self._connection_service.discover_folders(connection_id)
        connection = await self._connections.get(connection_id)
        held = {
            feed.remote_path: feed
            for feed in (connection.feeds if connection is not None else ())
        }
        proposals = []
        for candidate in found.candidates:
            path = normalise_remote_path(candidate.path)
            feed = held.get(path)
            kind = await self._kind_of(path, feed)
            proposals.append(
                IntegrationProposal(
                    kind=kind,
                    display_name=(
                        CATALOGUE[kind].display_name
                        if kind is not None
                        else (path or "the Dropbox root")
                    ),
                    connection_id=connection_id,
                    transport=TransportKind.CLOUD_FOLDER,
                    path=path,
                    activity_files=candidate.activity_files,
                    newest_at=candidate.newest_at,
                    configured=feed is not None,
                )
            )
        return DiscoveredIntegrations(
            proposals=tuple(proposals),
            access_type_suspect=found.access_type_suspect,
        )

    async def _kind_of(self, path: str, feed: FeedRow | None) -> IntegrationKind | None:
        """Which source a discovered folder belongs to, if arc can say.

        The **stored** classification wins over the catalogue's default path:
        an athlete who filed `/apps/wahoofitness` under something else has said
        so, and answering with the catalogue's guess would contradict a
        decision they already made.
        """
        if feed is not None and feed.integration_id is not None:
            owner = await self._repository.get(feed.integration_id)
            if owner is not None:
                return owner.kind
        return kind_for_default_path(path)

    def _local_drop(self, configured: LocalDropSettings) -> IntegrationView:
        """The always-present entry, built from settings rather than a row.

        `configured` is resolved by `IngestSettingsService`, not read from the
        environment here: the athlete sets the sweep interval in Settings, and
        a list that kept quoting `INGEST__SCAN_INTERVAL_SECONDS` would tell
        them their change had not taken while the sweep ran on the new one.
        """
        spec = CATALOGUE[IntegrationKind.LOCAL_DROP]
        return IntegrationView(
            id=LOCAL_DROP_ID,
            kind=IntegrationKind.LOCAL_DROP,
            display_name=spec.display_name,
            data_kinds=ordered_data_kinds(spec.provides),
            transport=TransportKind.LOCAL_FOLDER,
            storage=None,
            removable=False,
            prompt=None,
            local=LocalDropView(
                # Resolved and absolute: `DATA__ROOT` is normally relative, and
                # a relative path in Settings tells the athlete nothing about
                # where on the server (or in the container) to drop a file.
                inbox_path=configured.inbox_path,
                scan_interval_seconds=configured.scan_interval_seconds,
            ),
            folders=(),
        )

    def _stored(
        self, row: IntegrationRow, connections: dict[uuid.UUID, ConnectionRow]
    ) -> IntegrationView:
        spec = CATALOGUE[row.kind]
        return IntegrationView(
            id=str(row.id),
            kind=row.kind,
            display_name=spec.display_name,
            data_kinds=ordered_data_kinds(spec.provides),
            transport=TransportKind.CLOUD_FOLDER,
            storage=_storage_of(spec),
            removable=True,
            prompt=(
                None
                if row.feeds
                # Unreachable through this service — removing the last folder
                # removes the integration — but a row written by hand would
                # otherwise render as a source that is silently collecting
                # nothing.
                else f"{spec.display_name} has no folder yet. Add one to start "
                "collecting."
            ),
            local=None,
            folders=tuple(
                _folder(feed, connections)
                for feed in sorted(row.feeds, key=lambda feed: feed.remote_path)
            ),
        )

    def _unclassified(
        self, feed: FeedRow, connections: dict[uuid.UUID, ConnectionRow]
    ) -> IntegrationView:
        where = feed.remote_path or "the Dropbox root"
        return IntegrationView(
            id=str(feed.id),
            kind=None,
            display_name=where,
            data_kinds=(),
            transport=TransportKind.CLOUD_FOLDER,
            storage=_provider_of(feed, connections),
            removable=True,
            # UI convention 3: the prompt names the missing input *and* the
            # action that supplies it.
            prompt=(
                f"arc does not know which source {where} belongs to, so it "
                "cannot say what this folder brings in. Remove it here, then "
                "add the integration it belongs to."
            ),
            local=None,
            folders=(_folder(feed, connections),),
        )

    # --- writes --------------------------------------------------------------

    async def add(
        self,
        *,
        kind: IntegrationKind,
        transport: TransportKind,
        connection_id: uuid.UUID | None,
        remote_path: str | None,
        actor: Actor,
    ) -> AddedIntegration:
        """Add a source, and the first (or next) folder it is collected through.

        Returns the integration either way: adding Wahoo a second time with a
        different folder is one integration with two folders, reported as
        `created=False` so the adapter can answer 200 rather than 201.

        Raises:
            ValidationError: When the kind cannot be added (the local drop), the
                transport is not one this integration supports, or the storage
                provider has no connection yet.
            NotFoundError: When a named connection does not exist.
            ConflictError: When the folder is already held — compared on the
                **normalised** path, which is Python and therefore not
                something `uq_feeds_connection_id_remote_path` can enforce.
        """
        await check_write_cap(self._session, actor)
        spec = CATALOGUE[kind]
        if kind in SYNTHESIZED_KINDS:
            raise ValidationError(
                f"{spec.display_name} is always present and cannot be added: "
                "arc sweeps its inbox folder whether or not anything is "
                "configured. Change how often it sweeps instead."
            )
        transport_spec = spec.transport(transport)
        if transport_spec is None:
            supported = ", ".join(row.kind.value for row in spec.transports)
            raise ValidationError(
                f"{spec.display_name} cannot be collected over "
                f"{transport.value}. arc collects it over: {supported}."
            )
        if transport_spec.kind is not TransportKind.CLOUD_FOLDER:
            raise ValidationError(
                f"arc cannot yet set up a {transport.value} transport. "
                f"{spec.display_name} has to be added over a cloud folder."
            )

        # Guaranteed by `TransportSpec.__post_init__`: a cloud folder names
        # its storage provider or it does not construct.
        storage = transport_spec.storage or StorageProvider.DROPBOX
        connection = await self._connection_for(storage, connection_id)
        # An omitted path means the catalogue's default; an integration whose
        # transport declares none falls back to the provider root, which is
        # legal (`normalise_remote_path` spells it `""`) if unwise.
        proposed = (
            remote_path if remote_path is not None else transport_spec.default_path
        )
        path = normalise_remote_path(proposed or "")
        await self._refuse_a_folder_already_held(connection, path)

        integration = await self._repository.by_kind(kind)
        created = integration is None
        if integration is None:
            integration = await self._repository.add(IntegrationRow(kind=kind))
        # Appended to the loaded collection rather than `session.add`ed on its
        # own: the view returned below is built from `integration.feeds`, and a
        # relationship SQLAlchemy has already loaded is not re-read just
        # because a row was inserted behind it.
        integration.feeds.append(FeedRow(connection_id=connection.id, remote_path=path))
        await self._audit.record(
            actor=actor,
            action="integration.created" if created else "integration.folder_added",
            entity_type=INTEGRATION_ENTITY,
            entity_id=integration.id,
            payload={
                "kind": kind.value,
                "transport": transport.value,
                "storage": storage.value,
                "remote_path": path,
                "connection_id": str(connection.id),
            },
        )
        await commit(self._session)
        connections = {row.id: row for row in await self._connection_service.list()}
        return AddedIntegration(self._stored(integration, connections), created)

    async def remove(self, integration_id: str, *, actor: Actor) -> None:
        """Forget a source: the integration and every folder it collected through.

        **The connection stays, and so does every recording already ingested.**
        Stopping collection is not the same request as forgetting the credential
        or deleting the training history, and a remove that did either would be
        arc deciding one on the strength of the other.

        Raises:
            NotFoundError: When nothing has that id — including the local drop,
                which is synthesized and cannot be removed.
        """
        await check_write_cap(self._session, actor)
        parsed = _as_uuid(integration_id)
        row = None if parsed is None else await self._repository.get(parsed)
        if row is not None:
            kind = row.kind
            await self._repository.delete(row)
            await self._audit.record(
                actor=actor,
                action="integration.removed",
                entity_type=INTEGRATION_ENTITY,
                entity_id=row.id,
                payload={"kind": kind.value},
            )
            await commit(self._session)
            return
        # An unclassified folder is an entry in its own right, so removing it
        # is the same gesture — there is simply no integration row behind it.
        feed = None if parsed is None else await self._connections.get_feed(parsed)
        if feed is None or feed.integration_id is not None:
            raise NotFoundError(f"Integration {integration_id} not found")
        remote_path = feed.remote_path
        await self._connections.delete_feed(feed)
        await self._audit.record(
            actor=actor,
            action="integration.folder_removed",
            entity_type=INTEGRATION_ENTITY,
            entity_id=feed.id,
            payload={"kind": None, "remote_path": remote_path},
        )
        await commit(self._session)

    async def set_folder_enabled(
        self, integration_id: str, folder_id: uuid.UUID, *, enabled: bool, actor: Actor
    ) -> IntegrationView:
        """Pause or resume one folder, keeping its cursor.

        Keeping the cursor is why this is a flag and not a remove: a folder
        switched off for a week resumes where it stopped rather than re-listing
        from scratch.

        Raises:
            NotFoundError: When the integration or the folder does not exist.
        """
        await check_write_cap(self._session, actor)
        feed = await self._folder_of(integration_id, folder_id)
        feed.enabled = enabled
        await self._audit.record(
            actor=actor,
            action="integration.folder_resumed"
            if enabled
            else "integration.folder_paused",
            entity_type=INTEGRATION_ENTITY,
            entity_id=feed.id,
            payload={"remote_path": feed.remote_path, "enabled": enabled},
        )
        await commit(self._session)
        return await self._view_of(integration_id)

    async def remove_folder(
        self, integration_id: str, folder_id: uuid.UUID, *, actor: Actor
    ) -> None:
        """Stop collecting through one folder.

        Removing an integration's **last** folder removes the integration too:
        an entry with no transport is a source arc claims to collect from and
        has no way to reach, which is exactly the state the panel exists to
        make impossible.

        Raises:
            NotFoundError: When the integration or the folder does not exist.
        """
        await check_write_cap(self._session, actor)
        feed = await self._folder_of(integration_id, folder_id)
        remote_path = feed.remote_path
        integration = (
            None
            if feed.integration_id is None
            else await self._repository.get(feed.integration_id)
        )
        if integration is not None and len(integration.feeds) <= 1:
            # The integration goes and takes the folder with it by cascade.
            # Deleting the feed first and the integration after would ask
            # SQLAlchemy to delete the same row twice, through the
            # delete-orphan cascade that is still holding it.
            await self._repository.delete(integration)
        else:
            await self._connections.delete_feed(feed)
        await self._audit.record(
            actor=actor,
            action="integration.folder_removed",
            entity_type=INTEGRATION_ENTITY,
            entity_id=feed.id,
            payload={"remote_path": remote_path},
        )
        await commit(self._session)

    # --- internals -----------------------------------------------------------

    async def _view_of(self, integration_id: str) -> IntegrationView:
        for view in await self.list():
            if view.id == integration_id:
                return view
        raise NotFoundError(f"Integration {integration_id} not found")

    async def _folder_of(self, integration_id: str, folder_id: uuid.UUID) -> FeedRow:
        feed = await self._connections.get_feed(folder_id)
        owner = (
            ""
            if feed is None or feed.integration_id is None
            else str(feed.integration_id)
        )
        # An unclassified folder is addressed by its own id in both positions,
        # because that is the id its entry carries in the list.
        expected = owner or (str(feed.id) if feed is not None else "")
        if feed is None or expected != integration_id:
            raise NotFoundError(
                f"Folder {folder_id} is not part of integration {integration_id}"
            )
        return feed

    async def _connection_for(
        self, storage: StorageProvider, connection_id: uuid.UUID | None
    ) -> ConnectionRow:
        if connection_id is not None:
            row = await self._connections.get(connection_id)
            if row is None:
                raise NotFoundError(f"Connection {connection_id} not found")
            if row.provider is not storage:
                raise ValidationError(
                    f"That connection is {row.provider.value}, and this "
                    f"integration is collected from {storage.value}."
                )
            return row
        row = await self._connections.by_provider(storage)
        if row is None:
            raise ValidationError(
                f"No {storage.value.title()} account is connected, so arc has "
                f"nowhere to collect from. Connect {storage.value.title()} "
                "first, then add this integration."
            )
        return row

    async def _refuse_a_folder_already_held(
        self, connection: ConnectionRow, path: str
    ) -> None:
        """Refuse a folder some other entry is already collecting through.

        The service's job and not the database's: `uq_feeds_connection_id_remote_path`
        compares stored strings, and `normalise_remote_path` is Python — so
        `/Apps/WahooFitness/` and `/apps/wahoofitness` are one folder here and
        two rows as far as any constraint can tell.
        """
        existing = await self._connections.feed_for_path(connection.id, path)
        if existing is None:
            return
        holder = "A folder arc has not classified yet"
        if existing.integration_id is not None:
            owner = await self._repository.get(existing.integration_id)
            if owner is not None:
                holder = CATALOGUE[owner.kind].display_name
        raise ConflictError(
            f"{holder} is already collecting {path or 'the Dropbox root'} on "
            "this account. One folder feeds one integration."
        )


def _as_uuid(value: str) -> uuid.UUID | None:
    """Parse an id, or None — a malformed id is an absence, not a 422."""
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _storage_of(spec: IntegrationSpec) -> StorageProvider | None:
    transport = spec.transport(TransportKind.CLOUD_FOLDER)
    return None if transport is None else transport.storage


def _provider_of(
    feed: FeedRow, connections: dict[uuid.UUID, ConnectionRow]
) -> StorageProvider:
    row = connections.get(feed.connection_id)
    return row.provider if row is not None else ConnectionProvider.DROPBOX


def _folder(feed: FeedRow, connections: dict[uuid.UUID, ConnectionRow]) -> FolderView:
    connection = connections.get(feed.connection_id)
    return FolderView(
        feed_id=feed.id,
        connection_id=feed.connection_id,
        storage=_provider_of(feed, connections),
        remote_path=feed.remote_path,
        enabled=feed.enabled,
        state=delivery_state(feed),
        last_delivery_at=feed.last_delivery_at,
        last_error=feed.last_error,
        connection_status=(
            connection.status if connection is not None else ConnectionStatus.ERROR
        ),
        connection_error=connection.last_error if connection is not None else None,
        account_label=connection.account_label if connection is not None else None,
    )


def integration_names_using(
    integrations: Sequence[IntegrationRow], connection_id: uuid.UUID
) -> tuple[str, ...]:
    """Display names of the integrations that only exist through one connection.

    Used by `ConnectionService.disconnect` (AC-11): an integration whose every
    folder is on the account being forgotten has nothing behind it afterwards,
    so it goes with the account rather than lingering as an entry that collects
    nothing. One whose folders span two accounts survives, keeping the other.
    """
    return tuple(
        CATALOGUE[row.kind].display_name
        for row in integrations
        if row.feeds and all(feed.connection_id == connection_id for feed in row.feeds)
    )

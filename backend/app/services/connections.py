"""Use-cases for cloud connections and the folders arc watches.

A module of its own rather than a method on `IngestService`, and not by
preference: `app.services` may not import `app.ingest` (the layer contract
points inward), so the OAuth and feed use-cases cannot live beside the code
that will eventually consume them. What is here is configuration — hand arc a
credential, point it at a folder — and nothing in it fetches a file.
"""

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.dropbox import (
    READ_SCOPES,
    DropboxAuthError,
    DropboxClient,
    DropboxError,
    DropboxFolder,
    DropboxPathNotFoundError,
    DropboxRateLimitedError,
    authorize_url,
    current_account,
    exchange_code,
    new_code_verifier,
)
from app.core.config import get_settings
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    RateLimitedError,
    ValidationError,
)
from app.core.logging import get_logger
from app.domain.actor import Actor
from app.domain.connections import (
    FEED_DELIVERED_ACTION,
    ConnectionProvider,
    ConnectionStatus,
    FeedDeliveryState,
)
from app.domain.integrations import (
    CATALOGUE,
    DataKind,
    IntegrationKind,
    ordered_data_kinds,
)
from app.persistence.audit import AuditRepository
from app.persistence.connections import (
    KEY_SETTING,
    ConnectionRepository,
    ConnectionRow,
    CredentialDecryptionError,
    CredentialKeyError,
    EncryptedCredentials,
    FeedRow,
    OAuthAuthorizationRow,
)
from app.persistence.db import commit
from app.persistence.integrations import IntegrationRepository, IntegrationRow
from app.services.guardrails import check_write_cap

logger = get_logger(__name__)

#: `entity_type` written on this use-case's audit rows.
CONNECTION_ENTITY = "connection"

#: How long a started PKCE flow stays completable.
#:
#: Fifteen minutes is the athlete's round trip: open the link, log in to
#: Dropbox, approve, copy the code, come back. Longer, and an abandoned flow's
#: verifier sits in the database indefinitely beside a code that is sitting in
#: a browser history; shorter, and a two-factor prompt on a phone runs the
#: clock out.
AUTHORIZATION_TTL = dt.timedelta(minutes=15)


#: The window `ingest_status` counts deliveries over.
#:
#: Seven days because the question it answers is "is the week I am looking at
#: complete", and the coach reasons in weeks. A rolling window rather than the
#: current ISO week: a Monday-morning read would otherwise report near zero for
#: a perfectly healthy feed.
DELIVERY_WINDOW = dt.timedelta(days=7)


@dataclass(frozen=True, slots=True)
class FeedStatus:
    """One watched folder, as the coaching agent needs to see it."""

    feed_id: uuid.UUID
    #: The remote folder, in the spelling arc stores.
    folder: str
    enabled: bool
    state: FeedDeliveryState
    #: When arc last heard from Dropbox for this feed at all. ``None`` until
    #: the first successful poll — never rendered as a zero or an error.
    last_delivery_at: dt.datetime | None
    #: Files this feed turned into sessions in the last :data:`DELIVERY_WINDOW`.
    deliveries: int
    last_error: str | None
    #: The credential's own state, because a `needs_reauth` connection makes
    #: every feed under it silent for a reason no per-feed field can express.
    connection_status: ConnectionStatus
    account_label: str | None


@dataclass(frozen=True, slots=True)
class IntegrationIngestStatus:
    """One source arc collects from, and how each of its folders is doing.

    The grouping is the answer, not decoration. "`/apps/wahoofitness` has not
    delivered in five days" is a sentence about a path the coach has never
    seen; "Wahoo has not delivered in five days" names the thing the athlete
    can go and look at. The per-folder facts underneath are unchanged — a
    source with two folders has two of them, because one failing folder and
    one delivering folder is neither "Wahoo is broken" nor "Wahoo is fine",
    and no single word for the source could say which.
    """

    #: ``None`` for a folder configured before integrations existed. Still
    #: reported: it is still collecting, and a working pipe left out of the
    #: only tool that reports on pipes is a pipe nobody will notice breaking.
    kind: IntegrationKind | None
    display_name: str
    data_kinds: tuple[DataKind, ...]
    #: Empty for the **local drop**, which is a directory on the arc server:
    #: no credential to expire, no poll to fail, and therefore no per-folder
    #: delivery ledger to report from. It is listed all the same so the coach
    #: sees every source arc collects from rather than only the fragile ones.
    folders: tuple[FeedStatus, ...]


@dataclass(frozen=True, slots=True)
class IngestStatus:
    """Whether arc's supply of activity files is working, and through what."""

    #: Every source, the local drop first. Grouped rather than a flat list of
    #: folders — see :class:`IntegrationIngestStatus`.
    integrations: tuple[IntegrationIngestStatus, ...]
    #: True when no connection exists at all. **Not an error**: files arriving
    #: in `data/inbox/` is the supported baseline configuration, and answering
    #: a coaching agent with a failure would teach it that a perfectly healthy
    #: single-user install is broken.
    #:
    #: Deliberately still about **connections**, not about integrations: an
    #: account that is connected but collecting from nothing is a different
    #: fault from an athlete who never connected one, and the remedy for the
    #: first is to point it at a folder.
    local_inbox_only: bool


class AuthorizationStart:
    """The link the athlete opens, and when it stops being completable."""

    __slots__ = ("authorize_url", "expires_at")

    def __init__(self, authorize_url: str, expires_at: dt.datetime) -> None:
        self.authorize_url = authorize_url
        self.expires_at = expires_at


class ConnectionService:
    """Use-cases for connections and feeds. Raises AppError subclasses."""

    def __init__(
        self,
        session: AsyncSession,
        repository: ConnectionRepository,
        integrations: IntegrationRepository,
        audit: AuditRepository,
    ) -> None:
        self._session = session
        self._repository = repository
        self._integrations = integrations
        self._audit = audit

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(
            session,
            ConnectionRepository(session),
            IntegrationRepository(session),
            AuditRepository(session),
        )

    # --- reads ---------------------------------------------------------------

    async def list(self) -> Sequence[ConnectionRow]:
        """Every connection, with its feeds.

        Each row's *readable* status is settled here rather than in the
        adapter: a connection whose credential will not decrypt is reported
        `error` with the key named, never `connected`. See
        :meth:`_settle_readability`.
        """
        rows = await self._repository.list()
        for row in rows:
            self._settle_readability(row)
        return rows

    async def get(self, connection_id: uuid.UUID) -> ConnectionRow:
        """Return one connection.

        Raises:
            NotFoundError: When no connection has that id.
        """
        row = await self._repository.get(connection_id)
        if row is None:
            raise NotFoundError(f"Connection {connection_id} not found")
        self._settle_readability(row)
        return row

    async def ingest_status(self) -> IngestStatus:
        """How each source arc collects from is doing, and whether there are any.

        Grouped **by integration**, because that is the vocabulary the answer
        is acted on in: a coach told a path has gone quiet has nothing to say
        to the athlete, and a coach told Wahoo has gone quiet does.

        Read-only and cheap: one query for the connections, one for the
        integrations, one for the folders nobody has classified, and one count
        per folder over the audit trail. The delivery count comes from that
        trail (`app.ingest.feeds.DELIVERED_ACTION`) rather than from a column
        on the feed, because the trail already records every delivery with the
        feed it belonged to — see `AuditRepository.count_for_entity_since`.

        The local drop is **synthesized** here from the catalogue, exactly as
        `IntegrationService.list` synthesizes it for the athlete's own panel:
        it has no row, so it cannot be read from one. This service builds it
        rather than calling that one because `IntegrationService` is the layer
        *above* — it wires this service in — and reaching back up would be a
        cycle. What is duplicated is a name and two data kinds, both read from
        `CATALOGUE`; the shape of the answer is not.

        A connection whose credential will not open reports `error` here as it
        does everywhere else (:meth:`_settle_readability`), so a coach reading
        this sees the same fault the settings panel is showing the athlete.
        """
        since = dt.datetime.now(dt.UTC) - DELIVERY_WINDOW
        rows = await self._repository.list()
        for connection in rows:
            self._settle_readability(connection)
        connections = {connection.id: connection for connection in rows}
        return IngestStatus(
            integrations=(
                _local_drop_status(),
                *[
                    await self._integration_status(row, connections, since=since)
                    for row in await self._integrations.list()
                ],
                *[
                    await self._loose_folder_status(feed, connections, since=since)
                    for feed in await self._integrations.unclassified_feeds()
                ],
            ),
            local_inbox_only=not rows,
        )

    async def _integration_status(
        self,
        integration: IntegrationRow,
        connections: dict[uuid.UUID, ConnectionRow],
        *,
        since: dt.datetime,
    ) -> IntegrationIngestStatus:
        """One added source, with every folder it is collected through."""
        spec = CATALOGUE[integration.kind]
        return IntegrationIngestStatus(
            kind=integration.kind,
            display_name=spec.display_name,
            data_kinds=ordered_data_kinds(spec.provides),
            folders=tuple(
                [
                    await self._feed_status(
                        connections[feed.connection_id], feed, since=since
                    )
                    for feed in sorted(
                        integration.feeds, key=lambda feed: feed.remote_path
                    )
                ]
            ),
        )

    async def _loose_folder_status(
        self,
        feed: FeedRow,
        connections: dict[uuid.UUID, ConnectionRow],
        *,
        since: dt.datetime,
    ) -> IntegrationIngestStatus:
        """A folder no integration owns, reported as the source it stands in for.

        No kind, and none guessed: only the athlete can say which source a
        folder configured before integrations existed belongs to, and a guess
        here would reach the coach as a fact. Reported all the same — it is
        still collecting, and a working pipe left out of the only tool that
        reports on pipes is a pipe nobody will notice breaking.
        """
        return IntegrationIngestStatus(
            kind=None,
            display_name=feed.remote_path or "the Dropbox root",
            data_kinds=(),
            folders=(
                await self._feed_status(
                    connections[feed.connection_id], feed, since=since
                ),
            ),
        )

    async def _feed_status(
        self, connection: ConnectionRow, feed: FeedRow, *, since: dt.datetime
    ) -> FeedStatus:
        """One feed's line of the answer, with its delivery count."""
        return FeedStatus(
            feed_id=feed.id,
            folder=feed.remote_path,
            enabled=feed.enabled,
            state=delivery_state(feed),
            last_delivery_at=feed.last_delivery_at,
            deliveries=await self._audit.count_for_entity_since(
                action=FEED_DELIVERED_ACTION, entity_id=feed.id, since=since
            ),
            last_error=feed.last_error,
            connection_status=connection.status,
            account_label=connection.account_label,
        )

    def _settle_readability(self, row: ConnectionRow) -> None:
        """Downgrade a connection arc cannot read its own credential for.

        In memory only — no write, because this is a read path and the remedy
        is to restore `SECRETS__ENCRYPTION_KEY`, not to mutate rows on the way
        past. A GET that persisted `error` would make an operator who restarted
        with the wrong key lose the record of what the row used to be.
        """
        try:
            EncryptedCredentials.unseal(row.credentials)
        except (CredentialDecryptionError, CredentialKeyError) as exc:
            row.status = ConnectionStatus.ERROR
            row.last_error = str(exc)

    # --- the connect ritual --------------------------------------------------

    async def start_dropbox_authorization(self, *, actor: Actor) -> AuthorizationStart:
        """Mint a PKCE flow and return the URL the athlete opens.

        The verifier is stored (see the `oauth_authorizations` docstring) and
        never returned: the whole point of PKCE is that the code Dropbox shows
        the athlete is useless to anyone who does not also hold it.

        Raises:
            ValidationError: When `DROPBOX__APP_KEY` is not configured — a
                blank `client_id` would produce a link that fails on Dropbox's
                own error page, several minutes later, with nothing naming the
                setting that is missing.
        """
        await check_write_cap(self._session, actor)
        app_key = get_settings().dropbox.app_key.get_secret_value()
        if not app_key:
            raise ValidationError(
                "DROPBOX__APP_KEY is not set. Register an app at "
                "https://www.dropbox.com/developers/apps (type: Full Dropbox) "
                "and put its app key in DROPBOX__APP_KEY."
            )
        verifier = new_code_verifier()
        now = dt.datetime.now(dt.UTC)
        row = await self._repository.replace_authorization(
            OAuthAuthorizationRow(
                provider=ConnectionProvider.DROPBOX,
                code_verifier=verifier,
                created_at=now,
                expires_at=now + AUTHORIZATION_TTL,
            )
        )
        await commit(self._session)
        return AuthorizationStart(
            authorize_url(app_key=app_key, verifier=verifier), row.expires_at
        )

    async def complete_dropbox(self, *, code: str, actor: Actor) -> ConnectionRow:
        """Redeem the pasted code and store the connection.

        **One connection per provider.** A second connect is a 409 naming
        disconnect as the remedy rather than a silent replacement: the existing
        credential is the one every feed on the page is polling with, and
        overwriting it on the strength of a paste would move every feed to a
        different Dropbox account without saying so — including, plausibly, the
        wrong one, since the athlete may well have been logged in as somebody
        else in that browser tab.

        Raises:
            ConflictError: When a Dropbox connection already exists.
            ValidationError: When the flow was never started, has expired, the
                code is spent, or the grant carries no refresh token.
        """
        await check_write_cap(self._session, actor)
        existing = await self._repository.by_provider(ConnectionProvider.DROPBOX)
        if existing is not None:
            raise ConflictError(
                "A Dropbox account is already connected "
                f"({existing.account_label or 'unnamed'}). Disconnect it before "
                "connecting another."
            )

        pending = await self._pending_authorization()
        app_key = get_settings().dropbox.app_key.get_secret_value()
        # `.strip()`: the code is copied off a web page into a form field, and
        # a trailing newline is what a paste normally carries. Refusing it
        # would be arc failing at the one manual step it asked for.
        try:
            grant = await exchange_code(
                app_key=app_key, code=code.strip(), verifier=pending.code_verifier
            )
        except DropboxAuthError as exc:
            raise ValidationError(
                "Dropbox refused that authorization code: it has already been "
                "used, or it has expired. Start the connection again and paste "
                "the new code."
            ) from exc
        except DropboxError as exc:
            raise ValidationError(f"Dropbox could not be reached: {exc}") from exc

        if not grant.refresh_token:
            raise ValidationError(
                "Dropbox granted access without a refresh token, so arc could "
                "not renew it unattended. The authorization link must carry "
                "token_access_type=offline — start the connection again."
            )

        account = await current_account(access_token=grant.access_token)
        row = await self._repository.add(
            ConnectionRow(
                provider=ConnectionProvider.DROPBOX,
                status=ConnectionStatus.CONNECTED,
                account_label=account.label,
                scopes=sorted(grant.scopes or READ_SCOPES),
                credentials=EncryptedCredentials.seal(
                    {
                        "access_token": grant.access_token,
                        "refresh_token": grant.refresh_token,
                        "account_id": account.account_id,
                    }
                ),
                access_token_expires_at=grant.expires_at,
            )
        )
        await self._repository.delete_authorization(pending)
        await self._audit.record(
            actor=actor,
            action="connection.connected",
            entity_type=CONNECTION_ENTITY,
            entity_id=row.id,
            # The label and the scopes, never the credential: an audit row is
            # a permanent record, and a token in one outlives every rotation.
            payload={
                "provider": row.provider.value,
                "account_label": row.account_label,
                "scopes": list(row.scopes),
            },
        )
        await commit(self._session)
        return row

    async def _pending_authorization(self) -> OAuthAuthorizationRow:
        """The live PKCE flow, or a 422 explaining which way it failed."""
        rows = await self._repository.authorizations(ConnectionProvider.DROPBOX)
        if not rows:
            raise ValidationError(
                "No Dropbox authorization is in progress. Start the connection "
                "first, then paste the code Dropbox shows you."
            )
        pending = rows[0]
        if pending.expires_at <= dt.datetime.now(dt.UTC):
            # Deleted and committed before raising: an expired flow is over,
            # and leaving the row would let a code found in a browser history
            # be redeemed later against a verifier arc had already forgotten
            # about.
            for row in rows:
                await self._repository.delete_authorization(row)
            await commit(self._session)
            raise ValidationError(
                "That authorization expired — arc keeps a started connection "
                f"open for {int(AUTHORIZATION_TTL.total_seconds() // 60)} "
                "minutes. Start the connection again."
            )
        return pending

    async def disconnect(self, connection_id: uuid.UUID, *, actor: Actor) -> None:
        """Revoke the credential upstream and delete it locally.

        The local delete happens **whatever Dropbox says**. A revoke that fails
        leaves a token alive on Dropbox's side that arc no longer holds, which
        the athlete can finish off from Dropbox's own connected-apps page; a
        delete that was skipped because the revoke failed leaves arc holding a
        live credential the athlete has explicitly asked it to forget. Only one
        of those two failures is arc's to make.

        Raises:
            NotFoundError: When no connection has that id.
        """
        await check_write_cap(self._session, actor)
        row = await self.get(connection_id)
        feed_count = len(row.feeds)
        orphaned = await self._integrations_only_on(connection_id)
        try:
            await self._client_for(row).revoke()
        except Exception as exc:  # noqa: BLE001 — the local delete must happen
            # Every failure, not a named few: a network error, a dead
            # credential and a key that no longer decrypts all mean the same
            # thing here — arc could not tell Dropbox, and is deleting the
            # credential anyway. Logged with the exception *type*, never its
            # message, because an httpx error can quote the request it failed.
            logger.warning(
                "dropbox_revoke_failed",
                connection_id=str(row.id),
                error=type(exc).__name__,
            )
        # Before the connection, and deliberately: an integration whose every
        # folder lived on this account has nothing behind it once the account
        # is gone, and an entry in Settings that collects from nowhere is worse
        # than no entry at all. One with a folder on a *second* account keeps
        # that folder and survives.
        names = [CATALOGUE[integration.kind].display_name for integration in orphaned]
        for integration in orphaned:
            await self._integrations.delete(integration)
        if orphaned:
            # Re-read the connection's folders: the deletes above took some of
            # them by cascade, and the connection's own cascade would otherwise
            # issue a second DELETE for rows that are already gone.
            await self._session.refresh(row, ["feeds"])
        await self._repository.delete(row)
        await self._audit.record(
            actor=actor,
            action="connection.disconnected",
            entity_type=CONNECTION_ENTITY,
            entity_id=connection_id,
            payload={
                "provider": "dropbox",
                "feeds_removed": feed_count,
                "integrations_removed": names,
            },
        )
        await commit(self._session)

    async def _integrations_only_on(
        self, connection_id: uuid.UUID
    ) -> Sequence[IntegrationRow]:
        """Integrations with no folder anywhere but this connection.

        `Sequence`, not `list`: this class has a method called `list`, which
        shadows the builtin inside the class body and makes `list[...]` in an
        annotation a subscript of the method.
        """
        return [
            row
            for row in await self._integrations.list()
            if row.feeds
            and all(feed.connection_id == connection_id for feed in row.feeds)
        ]

    # --- browsing ------------------------------------------------------------

    async def folders(
        self, connection_id: uuid.UUID, *, path: str
    ) -> Sequence[DropboxFolder]:
        """List the folders directly under ``path`` on a connection.

        Raises:
            NotFoundError: When the connection, or the path, does not exist.
            ConflictError: When the credential needs re-authorizing — refused
                locally, because spending a request to be told what the row
                already says is a request the rate limit will want later.
            RateLimitedError: When Dropbox is throttling arc, carrying its own
                stated delay. Translated rather than left to escape, because an
                upstream 429 reaching the client as a 500 makes a transient
                condition look like a broken feature.
            ValidationError: When arc cannot read its own credential, or
                Dropbox failed in a way arc did not cause.
        """
        row = await self.get(connection_id)
        if row.status is ConnectionStatus.NEEDS_REAUTH:
            raise ConflictError(
                "This Dropbox connection needs re-authorising before arc can "
                "read folders from it. Reconnect the account."
            )
        if row.status is ConnectionStatus.ERROR:
            raise ValidationError(row.last_error or f"{KEY_SETTING} is not usable")
        try:
            return await self._client_for(row).list_folders(path)
        except DropboxPathNotFoundError as exc:
            raise NotFoundError(f"Dropbox has no folder at {exc.path or '/'}") from exc
        except DropboxAuthError as exc:
            raise ConflictError(
                "Dropbox refused arc's credential. Reconnect the account."
            ) from exc
        except DropboxRateLimitedError as exc:
            raise RateLimitedError(
                "Dropbox is rate-limiting arc. Try again in about "
                f"{int(exc.retry_after)} seconds."
            ) from exc
        except DropboxError as exc:
            raise ValidationError(f"Dropbox could not be reached: {exc}") from exc

    def _client_for(self, row: ConnectionRow) -> DropboxClient:
        return DropboxClient(
            self._session,
            row,
            app_key=get_settings().dropbox.app_key.get_secret_value(),
        )


def _local_drop_status() -> IntegrationIngestStatus:
    """The always-present source, built from the catalogue rather than a row.

    `data/inbox/` has been swept since WP-4.3 whether or not anybody
    configured anything, so there is nothing to read it from — see
    `app.domain.integrations.SYNTHESIZED_KINDS`. It reports no folders on
    purpose; :class:`IntegrationIngestStatus` says why an empty list there is
    not a fault.
    """
    spec = CATALOGUE[IntegrationKind.LOCAL_DROP]
    return IntegrationIngestStatus(
        kind=spec.kind,
        display_name=spec.display_name,
        data_kinds=ordered_data_kinds(spec.provides),
        folders=(),
    )


def delivery_state(feed: FeedRow) -> FeedDeliveryState:
    """One word for how a feed is doing. See :class:`FeedDeliveryState`.

    The order is the enum's and it is the point: a paused feed is silent
    because the athlete said so, and reporting it as `failing` or
    `never_delivered` would send a coach hunting a fault the athlete created
    on purpose.
    """
    if not feed.enabled:
        return FeedDeliveryState.PAUSED
    if feed.last_error:
        return FeedDeliveryState.FAILING
    if feed.last_delivery_at is None:
        return FeedDeliveryState.NEVER_DELIVERED
    return FeedDeliveryState.DELIVERING

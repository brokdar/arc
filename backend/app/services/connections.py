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
    AppKeySource,
    ConnectionProvider,
    ConnectionStatus,
    FeedDeliveryState,
    normalise_remote_path,
)
from app.persistence.audit import AuditRepository
from app.persistence.connections import (
    KEY_SETTING,
    MAX_APP_KEY_LENGTH,
    ConnectionRepository,
    ConnectionRow,
    CredentialDecryptionError,
    CredentialKeyError,
    EncryptedCredentials,
    FeedRow,
    OAuthAuthorizationRow,
)
from app.persistence.db import commit
from app.services.guardrails import check_write_cap

logger = get_logger(__name__)

#: `entity_type` written on this use-case's audit rows.
CONNECTION_ENTITY = "connection"
FEED_ENTITY = "feed"

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
class IngestStatus:
    """Whether arc's supply of activity files is working, and through what."""

    feeds: tuple[FeedStatus, ...]
    #: True when no connection exists at all. **Not an error**: files arriving
    #: in `data/inbox/` is the supported baseline configuration, and answering
    #: a coaching agent with a failure would teach it that a perfectly healthy
    #: single-user install is broken.
    local_inbox_only: bool


#: What the athlete is told to register, named in every refusal that needs it.
#:
#: The access type is in the sentence because it is the one choice Dropbox
#: will not let anybody change afterwards, and the failure it causes is silent:
#: an App-folder app connects perfectly and then cannot see `/Apps/WahooFitness`,
#: which belongs to *Wahoo's* app folder.
REGISTER_APP_REMEDY = (
    "Register an app at https://www.dropbox.com/developers/apps — "
    "Scoped access, access type Full Dropbox — and paste its app key into "
    "Settings, in the Dropbox panel."
)


@dataclass(frozen=True, slots=True)
class DropboxSetup:
    """Whether arc can start a Dropbox connection at all, and on whose key."""

    app_key_set: bool
    #: ``None`` exactly when no key is configured anywhere. See
    #: `app.domain.connections.AppKeySource`.
    source: AppKeySource | None


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
        audit: AuditRepository,
    ) -> None:
        self._session = session
        self._repository = repository
        self._audit = audit

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(session, ConnectionRepository(session), AuditRepository(session))

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
        """How each watched folder is doing, and whether there are any.

        Read-only and cheap: one query for the connections, one count per feed
        over the audit trail. The delivery count comes from that trail
        (`app.ingest.feeds.DELIVERED_ACTION`) rather than from a column on the
        feed, because the trail already records every delivery with the feed it
        belonged to — see `AuditRepository.count_for_entity_since`.

        A connection whose credential will not open reports `error` here as it
        does everywhere else (:meth:`_settle_readability`), so a coach reading
        this sees the same fault the settings panel is showing the athlete.
        """
        since = dt.datetime.now(dt.UTC) - DELIVERY_WINDOW
        rows = await self._repository.list()
        for connection in rows:
            self._settle_readability(connection)
        return IngestStatus(
            feeds=tuple(
                [
                    await self._feed_status(connection, feed, since=since)
                    for connection in rows
                    for feed in connection.feeds
                ]
            ),
            local_inbox_only=not rows,
        )

    async def _feed_status(
        self, connection: ConnectionRow, feed: FeedRow, *, since: dt.datetime
    ) -> FeedStatus:
        """One feed's line of the answer, with its delivery count."""
        return FeedStatus(
            feed_id=feed.id,
            folder=feed.remote_path,
            enabled=feed.enabled,
            state=_delivery_state(feed),
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

    # --- the app the athlete registered --------------------------------------

    async def app_key(self, provider: ConnectionProvider) -> str | None:
        """The app key arc connects with, or None if it has none.

        **A stored key wins over the environment.** The alternative readings
        were "the environment wins" and "refuse when the two disagree", and
        both fail the same athlete: the app key is the one value a
        self-hoster gets wrong on the first attempt — an App-folder app, a key
        from the wrong app, a truncated paste — and if `DROPBOX__APP_KEY`
        outranked the panel then fixing it would mean a text editor and a
        restart, which is the ritual this feature exists to delete. The
        environment keeps its job as a config-as-code *seed*; the panel is
        where the value is corrected, and it reports which source is in force
        (:meth:`dropbox_setup`) so the two can never disagree silently.

        Read on every call rather than cached: the panel writes a key and the
        very next authorize must carry it, in the same process.
        """
        stored = await self._repository.provider_app(provider)
        if stored is not None:
            return stored.app_key
        # `.strip()`: `DROPBOX__APP_KEY=` in a .env is a *set* variable holding
        # nothing, and so is a line with a stray space after the `=`.
        seeded = get_settings().dropbox.app_key.get_secret_value().strip()
        return seeded or None

    async def dropbox_setup(self) -> DropboxSetup:
        """Whether Dropbox can be connected, and on whose app key.

        Its own read rather than a field on the connection list because the
        list is *empty* before any of this happens — the panel would have
        nowhere to read it from at the only moment it needs it.
        """
        stored = await self._repository.provider_app(ConnectionProvider.DROPBOX)
        if stored is not None:
            return DropboxSetup(app_key_set=True, source=AppKeySource.STORED)
        if get_settings().dropbox.app_key.get_secret_value().strip():
            return DropboxSetup(app_key_set=True, source=AppKeySource.ENVIRONMENT)
        return DropboxSetup(app_key_set=False, source=None)

    async def set_dropbox_app_key(self, *, app_key: str, actor: Actor) -> DropboxSetup:
        """Store the app key from the athlete's own Dropbox app registration.

        Raises:
            ValidationError: When the key is blank or longer than
                `MAX_APP_KEY_LENGTH`.
            ConflictError: When a Dropbox connection already exists. The
                stored credential was granted *to a particular app*, and
                changing the key underneath it would leave arc refreshing a
                token against a client id that never issued it — a failure
                that surfaces hours later as a connection that stopped
                working. Disconnecting first is the remedy, and it is named.
        """
        await check_write_cap(self._session, actor)
        key = app_key.strip()
        if not key:
            raise ValidationError(
                f"That is not a Dropbox app key. {REGISTER_APP_REMEDY}"
            )
        if len(key) > MAX_APP_KEY_LENGTH:
            raise ValidationError(
                f"A Dropbox app key is short — at most {MAX_APP_KEY_LENGTH} "
                "characters. Paste the App key, not the app secret or the URL "
                "of the console page."
            )
        existing = await self._repository.by_provider(ConnectionProvider.DROPBOX)
        if existing is not None:
            raise ConflictError(
                "A Dropbox account is already connected with the current app "
                f"key ({existing.account_label or 'unnamed'}). Disconnect it "
                "before changing the app arc connects through."
            )
        await self._repository.replace_provider_app(
            ConnectionProvider.DROPBOX, app_key=key
        )
        await self._audit.record(
            actor=actor,
            action="connection.app_key_set",
            entity_type=CONNECTION_ENTITY,
            # The key is a public OAuth client id, so it is safe in an audit
            # row — but it is still not what the row is *for*: what happened
            # is that the app arc connects through changed.
            entity_id=None,
            payload={"provider": ConnectionProvider.DROPBOX.value},
        )
        await commit(self._session)
        return await self.dropbox_setup()

    async def clear_dropbox_app_key(self, *, actor: Actor) -> None:
        """Forget the stored app key, falling back to `DROPBOX__APP_KEY`.

        Idempotent: with nothing stored the desired state already holds, so
        this succeeds rather than reporting a 404 for a button that did
        exactly what it promised.
        """
        await check_write_cap(self._session, actor)
        stored = await self._repository.provider_app(ConnectionProvider.DROPBOX)
        if stored is None:
            return
        await self._repository.delete_provider_app(stored)
        await self._audit.record(
            actor=actor,
            action="connection.app_key_cleared",
            entity_type=CONNECTION_ENTITY,
            entity_id=None,
            payload={"provider": ConnectionProvider.DROPBOX.value},
        )
        await commit(self._session)

    # --- the connect ritual --------------------------------------------------

    async def start_dropbox_authorization(self, *, actor: Actor) -> AuthorizationStart:
        """Mint a PKCE flow and return the URL the athlete opens.

        The verifier is stored (see the `oauth_authorizations` docstring) and
        never returned: the whole point of PKCE is that the code Dropbox shows
        the athlete is useless to anyone who does not also hold it.

        Raises:
            ValidationError: When no app key is configured in either source —
                a blank `client_id` would produce a link that fails on
                Dropbox's own error page, several minutes later, naming
                nothing the athlete can act on. The panel reads
                :meth:`dropbox_setup` and never offers the control that gets
                here; this is the guard for everything that is not the panel.
        """
        await check_write_cap(self._session, actor)
        app_key = await self.app_key(ConnectionProvider.DROPBOX)
        if not app_key:
            raise ValidationError(
                f"arc has no Dropbox app key yet. {REGISTER_APP_REMEDY}"
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
        app_key = await self.app_key(ConnectionProvider.DROPBOX) or ""
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
        try:
            client = await self._client_for(row)
            await client.revoke()
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
        await self._repository.delete(row)
        await self._audit.record(
            actor=actor,
            action="connection.disconnected",
            entity_type=CONNECTION_ENTITY,
            entity_id=connection_id,
            payload={"provider": "dropbox", "feeds_removed": feed_count},
        )
        await commit(self._session)

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
            client = await self._client_for(row)
            return await client.list_folders(path)
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

    async def _client_for(self, row: ConnectionRow) -> DropboxClient:
        """A client for one connection, on the app key that connection uses.

        Async because the key may be stored (:meth:`app_key`) rather than in
        the environment — and a refresh attempted with the *other* app's key
        is refused by Dropbox with an error about the credential, not about
        the configuration that actually moved.
        """
        return DropboxClient(
            self._session,
            row,
            app_key=await self.app_key(row.provider) or "",
        )

    # --- feeds ---------------------------------------------------------------

    async def create_feed(
        self, *, connection_id: uuid.UUID, remote_path: str, actor: Actor
    ) -> FeedRow:
        """Start watching a folder.

        The path is normalised before anything is stored (see
        `app.domain.connections.normalise_remote_path`), so the same folder in
        another spelling is the *same* feed and a 409, not a second poll of one
        directory.

        Raises:
            NotFoundError: When the connection does not exist.
            ConflictError: When that folder is already watched.
        """
        await check_write_cap(self._session, actor)
        await self.get(connection_id)
        path = normalise_remote_path(remote_path)
        if await self._repository.feed_for_path(connection_id, path) is not None:
            raise ConflictError(
                f"arc is already watching {path or 'the Dropbox root'} on this "
                "connection."
            )
        row = await self._repository.add_feed(
            FeedRow(connection_id=connection_id, remote_path=path)
        )
        await self._audit.record(
            actor=actor,
            action="feed.created",
            entity_type=FEED_ENTITY,
            entity_id=row.id,
            payload={"connection_id": str(connection_id), "remote_path": path},
        )
        await commit(self._session)
        return row

    async def set_feed_enabled(
        self, feed_id: uuid.UUID, *, enabled: bool, actor: Actor
    ) -> FeedRow:
        """Turn a feed's polling on or off, keeping its cursor.

        Keeping the cursor is the point of a flag rather than a delete: a feed
        switched off for a week and back on resumes where it stopped instead of
        re-listing the folder from scratch.

        Raises:
            NotFoundError: When no feed has that id.
        """
        await check_write_cap(self._session, actor)
        row = await self._feed(feed_id)
        row.enabled = enabled
        await self._audit.record(
            actor=actor,
            action="feed.enabled" if enabled else "feed.disabled",
            entity_type=FEED_ENTITY,
            entity_id=row.id,
            payload={"remote_path": row.remote_path, "enabled": enabled},
        )
        await commit(self._session)
        return row

    async def delete_feed(self, feed_id: uuid.UUID, *, actor: Actor) -> None:
        """Stop watching a folder and forget its polling state.

        Raises:
            NotFoundError: When no feed has that id.
        """
        await check_write_cap(self._session, actor)
        row = await self._feed(feed_id)
        remote_path = row.remote_path
        await self._repository.delete_feed(row)
        await self._audit.record(
            actor=actor,
            action="feed.removed",
            entity_type=FEED_ENTITY,
            entity_id=feed_id,
            payload={"remote_path": remote_path},
        )
        await commit(self._session)

    async def _feed(self, feed_id: uuid.UUID) -> FeedRow:
        row = await self._repository.get_feed(feed_id)
        if row is None:
            raise NotFoundError(f"Feed {feed_id} not found")
        return row


def _delivery_state(feed: FeedRow) -> FeedDeliveryState:
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

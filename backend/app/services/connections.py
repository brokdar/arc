"""Use-cases for cloud connections and the folders arc watches.

A module of its own rather than a method on `IngestService`, and not by
preference: `app.services` may not import `app.ingest` (the layer contract
points inward), so the OAuth and feed use-cases cannot live beside the code
that will eventually consume them. What is here is configuration — hand arc a
credential, point it at a folder — and nothing in it fetches a file.
"""

import contextlib
import datetime as dt
import secrets
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.dropbox import (
    PERMISSION_LOST,
    READ_SCOPES,
    DropboxAuthError,
    DropboxClient,
    DropboxError,
    DropboxFolder,
    DropboxListing,
    DropboxPathNotFoundError,
    DropboxRateLimitedError,
    DropboxScopeError,
    DropboxUnreachableError,
    authorize_url,
    current_account,
    exchange_code,
    new_code_verifier,
    new_state,
    probe_readable,
    redirect_eligible,
)
from app.core.config import SettingSource, get_settings
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    RateLimitedError,
    UpstreamError,
    ValidationError,
)
from app.core.logging import get_logger
from app.domain.actor import Actor
from app.domain.connections import (
    FEED_DELIVERED_ACTION,
    ConnectionProvider,
    ConnectionStatus,
    FeedDeliveryState,
    is_activity_file,
    normalise_remote_path,
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
    MAX_APP_KEY_LENGTH,
    MAX_REDIRECT_URI_LENGTH,
    ConnectionRepository,
    ConnectionRow,
    CredentialDecryptionError,
    CredentialKeyError,
    EncryptedCredentials,
    FeedRow,
    OAuthAuthorizationRow,
)
from app.persistence.db import commit, refresh
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

#: Where Dropbox keeps the folders other apps write into.
#:
#: Spelled the way Dropbox displays it rather than lower-cased: it is sent as a
#: path in a request, and the athlete may see it quoted back in an explanation.
#: Dropbox matches paths case-insensitively, so the casing is cosmetic upstream
#: and load-bearing on screen.
APP_CONTAINER = "/Apps"

#: What `access_type_suspect` says when the evidence points at an App-folder
#: Dropbox app. One word, because the panel has to branch on it; a sentence
#: would put the athlete-facing wording in two places at once.
APP_FOLDER_SUSPECT = "app_folder"

#: The stamp a folder with no readable dates sorts as. Older than any file.
_NEVER = dt.datetime.min.replace(tzinfo=dt.UTC)

#: What the athlete is told to register, named in every refusal that needs it.
#:
#: The access type is in the sentence because it is the one choice Dropbox
#: will not let anybody change afterwards, and the failure it causes is silent:
#: an App-folder app connects perfectly and then cannot see `/Apps/WahooFitness`,
#: which belongs to *Wahoo's* app folder.
REGISTER_APP_REMEDY = (
    "Register an app at https://www.dropbox.com/developers/apps — "
    "Scoped access, access type Full Dropbox — and paste its app key into "
    "Settings, in the steps for adding an integration."
)


#: What the athlete reads when arc could not prove the connection at all.
#:
#: The connection **is** stored in this case, and the sentence says so rather
#: than apologising: the authorization code was spent redeeming the grant, so
#: throwing the connection away over a Dropbox that did not answer would cost
#: the athlete the whole trip to dropbox.com for a failure that says nothing
#: about their credential. What it owes them instead is the knowledge that the
#: check is still outstanding, and when it happens.
VERIFICATION_DEFERRED = (
    "Dropbox did not answer when arc checked that it can read your files. The "
    "account is connected, and arc checks again the first time it looks for "
    "new rides."
)


def _permission_refusal(
    missing: Sequence[str],
    *,
    opening: str,
    reconnect: str = "start the connection again",
) -> str:
    """The refusal an athlete can act on without leaving the sentence.

    Every missing scope is named, not the first one: the remedy is a round
    trip to dropbox.com and back through the whole authorize flow, and a
    refusal naming one of three costs three of them.

    The four console moves are spelled out — Permissions tab, tick, Submit,
    connect again — because the last one is the counter-intuitive half:
    Dropbox applies a newly-submitted scope only to a grant issued **after**
    it, so an athlete who ticks the box and reloads arc is still refused.

    ``reconnect`` is that last move, and it differs by *where the athlete is
    standing*. Mid-connect there is nothing stored, so the remedy is to start
    again. Browsing folders on a connection arc already holds, "start the
    connection again" is an instruction that fails: a second connect is
    refused with a 409 until the first is removed, so this path says to
    disconnect first. One refusal wording either way, one clause that moves.
    """
    return (
        f"{opening} Open your app at https://www.dropbox.com/developers/apps, "
        f"tick {', '.join(missing)} on its Permissions tab, choose Submit, "
        f"then {reconnect} — Dropbox gives arc a newly ticked permission only "
        "on a connection made after you submit it."
    )


def scope_refusal(required_scope: str) -> str:
    """The sentence a **stored** connection gets when Dropbox refuses a scope.

    One function because two subsystems now write it: the folder browse, which
    answers it to the screen (:func:`_dropbox_failures_translated`), and the
    feed poll, which writes it onto `connections.last_error` where the settings
    panel renders it (`app.ingest.feeds`). The athlete meets the same failure
    from two directions and must not be given two different accounts of it —
    especially not two different remedies, when the remedy is four console
    moves they have to get exactly right.

    Not shared with the *connect* refusal, which is a different sentence on
    purpose: mid-connect nothing is stored, so the last move is "start again",
    while here a second connect is refused with a 409 until the stored one is
    removed. :func:`_permission_refusal` holds the part that is common.

    Args:
        required_scope: What Dropbox named, or ``""`` when it named nothing —
            read as "all of them", because a grant that can name none is a
            grant that carries none.
    """
    return _permission_refusal(
        [required_scope] if required_scope else sorted(READ_SCOPES),
        opening="Dropbox will not let arc read your files.",
        reconnect="disconnect this Dropbox account here and connect it again",
    )


@dataclass(frozen=True, slots=True)
class DropboxConnected:
    """A stored Dropbox connection, and what arc could prove about it."""

    connection: ConnectionRow
    #: ``None`` when arc read the athlete's Dropbox during the connect and it
    #: worked. A sentence when Dropbox could not answer that read at all —
    #: see :data:`VERIFICATION_DEFERRED`.
    verification_note: str | None


@dataclass(frozen=True, slots=True)
class DropboxSetup:
    """Whether arc can start a Dropbox connection at all, and on whose key."""

    app_key_set: bool
    #: ``None`` exactly when no key is configured anywhere. See
    #: `app.core.config.SettingSource`.
    source: SettingSource | None


@dataclass(frozen=True, slots=True)
class FolderCandidate:
    """A folder discovery thinks the athlete's rides are already in."""

    #: Dropbox's own spelling, ready to be posted straight back as a feed's
    #: `remote_path` — the panel never rebuilds it from the display name.
    path: str
    #: How many `.fit`/`.gpx`/`.tcx` files are directly in it. Never zero: a
    #: folder with none is not a candidate at all.
    activity_files: int
    #: The newest `client_modified` among them, or ``None`` when Dropbox
    #: reported none arc could read.
    newest_at: dt.datetime | None


@dataclass(frozen=True, slots=True)
class FolderDiscovery:
    """What arc found looking for the folder the ride files are in."""

    #: Best first: most activity files, then most recently written.
    candidates: tuple[FolderCandidate, ...]
    #: :data:`APP_FOLDER_SUSPECT` when the evidence says the Dropbox app was
    #: registered with App-folder access, ``None`` when it does not. Never a
    #: statement of fact — no Dropbox API reports an app's access type, so this
    #: is an inference, and the panel words it as one.
    access_type_suspect: str | None


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

    # --- the app the athlete registered --------------------------------------

    async def app_key(self, provider: ConnectionProvider) -> str | None:
        """The app key arc connects with, or None if it has none.

        **A stored key wins over the environment.** The alternative readings
        were "the environment wins" and "refuse when the two disagree", and
        both fail the same athlete: the app key is the one value a
        self-hoster gets wrong on the first attempt — an App-folder app, a key
        from the wrong app, a truncated paste — and if `DROPBOX__APP_KEY`
        outranked the stored row then fixing it would mean a text editor and a
        restart, which is the ritual this feature exists to delete. The
        environment keeps its job as a config-as-code *seed*; Settings is
        where the value is corrected, and it reports which source is in force
        (:meth:`dropbox_setup`) so the two can never disagree silently.

        Read on every call rather than cached: the athlete stores a key and
        the very next authorize must carry it, in the same process.
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
        list is *empty* before any of this happens — the add-integration flow
        would have nowhere to read it from at the only moment it needs it.
        """
        stored = await self._repository.provider_app(ConnectionProvider.DROPBOX)
        if stored is not None:
            return DropboxSetup(app_key_set=True, source=SettingSource.STORED)
        if get_settings().dropbox.app_key.get_secret_value().strip():
            return DropboxSetup(app_key_set=True, source=SettingSource.ENVIRONMENT)
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

    async def start_dropbox_authorization(
        self, *, actor: Actor, redirect_uri: str | None = None
    ) -> AuthorizationStart:
        """Mint a PKCE flow and return the URL the athlete opens.

        **The redirect origin comes from the browser, and is validated here.**
        arc has no trustworthy view of its own origin: it sits behind Caddy,
        which fronts the API, the MCP server and the frontend as one host, and
        the only header that claims to answer the question — `X-Forwarded-Host`
        — is written by whatever spoke to the proxy. Trusting it would let a
        request choose the URI Dropbox sends an authorization code to. So the
        page that is *already open in the athlete's browser* says where it is,
        and this method decides whether Dropbox will redirect there at all
        (:func:`redirect_eligible`). Nothing is stored that did not pass.

        Omitting `redirect_uri` is the paste flow, unchanged: Dropbox shows
        the athlete a code instead of sending them anywhere, which is what
        lets arc be connected from a deployment Dropbox will not redirect to.

        The verifier is stored (see the `oauth_authorizations` docstring) and
        never returned: the whole point of PKCE is that the code Dropbox shows
        the athlete is useless to anyone who does not also hold it. The
        `state` beside it *is* returned — in the URL — because Dropbox has to
        hand it back for the comparison in :meth:`complete_dropbox` to mean
        anything.

        Raises:
            ValidationError: When no app key is configured in either source —
                a blank `client_id` would produce a link that fails on
                Dropbox's own error page, several minutes later, naming
                nothing the athlete can act on. The add-integration flow reads
                :meth:`dropbox_setup` and never offers the control that gets
                here; this is the guard for everything that is not that flow.
                Also when the browser's origin is one Dropbox refuses to
                redirect to, which names the paste flow as the way through.
        """
        await check_write_cap(self._session, actor)
        app_key = await self.app_key(ConnectionProvider.DROPBOX)
        if not app_key:
            raise ValidationError(
                f"arc has no Dropbox app key yet. {REGISTER_APP_REMEDY}"
            )
        redirect_uri = self._checked_redirect(redirect_uri)
        verifier = new_code_verifier()
        state = None if redirect_uri is None else new_state()
        now = dt.datetime.now(dt.UTC)
        row = await self._repository.replace_authorization(
            OAuthAuthorizationRow(
                provider=ConnectionProvider.DROPBOX,
                code_verifier=verifier,
                state=state,
                redirect_uri=redirect_uri,
                created_at=now,
                expires_at=now + AUTHORIZATION_TTL,
            )
        )
        await commit(self._session)
        return AuthorizationStart(
            authorize_url(
                app_key=app_key,
                verifier=verifier,
                redirect_uri=redirect_uri,
                state=state,
            ),
            row.expires_at,
        )

    @staticmethod
    def _checked_redirect(redirect_uri: str | None) -> str | None:
        """The redirect URI to register with Dropbox, or `None` for the paste.

        Refused *before* a flow is written rather than after: a started
        authorization the athlete cannot finish is worse than none at all,
        because the next paste-flow attempt would find it and use its
        verifier.
        """
        if redirect_uri is None:
            return None
        candidate = redirect_uri.strip()
        if not candidate:
            return None
        if len(candidate) > MAX_REDIRECT_URI_LENGTH:
            raise ValidationError(
                f"That address is longer than the {MAX_REDIRECT_URI_LENGTH} "
                "characters arc can store for a Dropbox redirect. Connect by "
                "pasting the code Dropbox shows you instead."
            )
        if not redirect_eligible(candidate):
            raise ValidationError(
                f"Dropbox will not redirect back to {candidate}: it only "
                "redirects to https addresses, or to http on localhost. arc "
                "reached over plain http at a LAN address is connected by "
                "pasting the code Dropbox shows you — start the connection "
                "again and paste the code."
            )
        return candidate

    async def complete_dropbox(
        self, *, code: str, actor: Actor, state: str | None = None
    ) -> DropboxConnected:
        """Redeem the code — pasted or redirected — and store a proven connection.

        **Nothing is stored until arc has read the athlete's Dropbox.** The
        proof is two checks, in this order. First the grant's own scope string
        must cover :data:`READ_SCOPES`; second, a `limit=1` `list_folder("")`
        with the fresh access token must succeed. Neither is redundant: the
        scope string is what Dropbox says it gave, and it is free to check,
        while the read is what arc will actually do — a grant can name a scope
        the app registration no longer carries. What this displaces is a
        connect that asked `get_current_account` and nothing else, an endpoint
        that answers 200 for a grant holding no file scopes at all, so a
        credential unable to list a single folder was stored and labelled
        `connected`. The athlete met the consequence two screens later, on the
        folder picker, as a sentence about a folder path.

        **A scope-shaped refusal is fatal; an unanswered probe is not.** A 401
        or a 403 means the athlete has to change their app registration and
        authorize again regardless, so the connection is refused and no row is
        written — storing one would only mean disconnecting it first. A 429, a
        5xx or a dead socket says nothing about the credential, and the
        authorization code has already been spent by then: refusing there
        would burn the whole trip to dropbox.com over a bad minute upstream.
        Those store the connection and return
        :data:`VERIFICATION_DEFERRED` for the caller to show, so an unproven
        connection is one the athlete knows is unproven.

        **One connection per provider.** A second connect is a 409 naming
        disconnect as the remedy rather than a silent replacement: the existing
        credential is the one every feed on the page is polling with, and
        overwriting it on the strength of a paste would move every feed to a
        different Dropbox account without saying so — including, plausibly, the
        wrong one, since the athlete may well have been logged in as somebody
        else in that browser tab.

        **A `state` that does not match deletes the pending authorization.**
        Refusing but leaving the row redeemable would make the nonce a speed
        bump: whoever delivered a code arc never asked for could simply try
        again, and again, against a verifier arc is still holding. The flow is
        cheap to restart and the athlete is told to; the attacker gets one
        attempt at a value they cannot guess. The comparison runs **before**
        anything is offered to Dropbox, so a code that arrived through some
        other page is never redeemed at all.

        The match is exact in both directions. A row that stored a state wants
        one back; a row that stored none — the paste flow — must be completed
        without one, because there is nothing to round-trip and a `state`
        arriving against it came from somewhere arc did not send the athlete.

        Expiry is judged first (:meth:`_pending_authorization`): an athlete
        who left the Dropbox tab open over lunch is told the flow expired, not
        told about a security token.

        Raises:
            ConflictError: When a Dropbox connection already exists.
            ValidationError: When the flow was never started, has expired, the
                state does not match, the code is spent, the grant carries no
                refresh token, the grant cannot read the athlete's files, or
                Dropbox could not be reached at all.
            UpstreamError: When Dropbox answered one of the three calls with a
                failure of its own. Its own status because there is nothing
                wrong with the request, the setup or the code — see
                :func:`_dropbox_failures_translated`.
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
        await self._check_state(pending, state)
        app_key = await self.app_key(ConnectionProvider.DROPBOX) or ""
        # `.strip()`: the code is copied off a web page into a form field, and
        # a trailing newline is what a paste normally carries. Refusing it
        # would be arc failing at the one manual step it asked for.
        # Every leg that talks to Dropbox is inside one translation, so an
        # outage at any of them — exchange, account, probe — is the same
        # honest 422 rather than a 500 from whichever leg nobody wrapped. The
        # `except` clauses below pre-empt it where this use-case has something
        # more specific to say; an `AppError` raised in there is not a
        # `DropboxError` and passes straight out.
        with _dropbox_failures_translated():
            try:
                grant = await exchange_code(
                    app_key=app_key,
                    code=code.strip(),
                    verifier=pending.code_verifier,
                    redirect_uri=pending.redirect_uri,
                )
            except DropboxAuthError as exc:
                raise ValidationError(
                    "Dropbox refused that authorization code: it has already "
                    "been used, or it has expired. Start the connection again "
                    "and paste the new code."
                ) from exc

            if not grant.refresh_token:
                raise ValidationError(
                    "Dropbox granted access without a refresh token, so arc "
                    "could not renew it unattended. The authorization link must "
                    "carry token_access_type=offline — start the connection "
                    "again."
                )

            granted = frozenset(grant.scopes)
            # No fallback to `READ_SCOPES` for a grant that named nothing: an
            # app with every permission unticked comes back with `scope: ""`,
            # and reading that as "Dropbox did not say" recorded the scopes arc
            # wanted as though they had been given.
            if missing := sorted(READ_SCOPES - granted):
                raise ValidationError(
                    _permission_refusal(
                        missing,
                        opening=(
                            "Dropbox did not give arc permission to read your files."
                        ),
                    )
                )

            account = await current_account(access_token=grant.access_token)
            verification_note: str | None = None
            #: The connect probe **is** a `list_folder` call, so a probe that
            #: succeeded is the first observation of this credential working
            #: and the row is born with a stamp — see
            #: `ConnectionRow.last_verified_at`. A connection stored under the
            #: transient-probe rule below has none, which is the truth: nobody
            #: has checked it yet, and the first poll is when somebody does.
            verified_at: dt.datetime | None = None
            try:
                await probe_readable(access_token=grant.access_token)
                verified_at = dt.datetime.now(dt.UTC)
            except DropboxScopeError as exc:
                # Dropbox named one scope; naming the other two back would send
                # the athlete to tick permissions they already granted.
                raise ValidationError(
                    _permission_refusal(
                        [exc.required_scope]
                        if exc.required_scope
                        else sorted(READ_SCOPES),
                        opening=(
                            "Dropbox would not let arc read your files, although "
                            "the permission looked granted."
                        ),
                    )
                ) from exc
            except DropboxAuthError as exc:
                raise ValidationError(
                    _permission_refusal(
                        sorted(READ_SCOPES),
                        opening=(
                            "Dropbox would not let arc read your files, although "
                            "the permission looked granted."
                        ),
                    )
                ) from exc
            except DropboxError as exc:
                # Every scope arc needs was granted and Dropbox simply did not
                # answer. Logged rather than silent, because a deployment where
                # this happens on every connect is an operator's problem.
                logger.info("dropbox_connect_probe_unanswered", error=str(exc))
                verification_note = VERIFICATION_DEFERRED

        row = await self._repository.add(
            ConnectionRow(
                provider=ConnectionProvider.DROPBOX,
                status=ConnectionStatus.CONNECTED,
                account_label=account.label,
                scopes=sorted(granted),
                credentials=EncryptedCredentials.seal(
                    {
                        "access_token": grant.access_token,
                        "refresh_token": grant.refresh_token,
                        "account_id": account.account_id,
                    }
                ),
                access_token_expires_at=grant.expires_at,
                last_verified_at=verified_at,
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
        return DropboxConnected(connection=row, verification_note=verification_note)

    async def _check_state(
        self, pending: OAuthAuthorizationRow, supplied: str | None
    ) -> None:
        """Verify the CSRF nonce, and end the flow when it does not match.

        `compare_digest` rather than `==`: the value is a secret arc issued,
        and the timing of a byte-by-byte comparison is the one thing an
        attacker holding a code can measure.

        Over **bytes** rather than `str`: `compare_digest` raises on a `str`
        holding anything outside ASCII, and the supplied half is whatever the
        athlete's browser was pointed at. Comparing text would turn the
        wrong-nonce case — the one this method exists for — into a `TypeError`
        that never reaches the deletion below, so one accented character would
        be enough to keep a refused flow redeemable. `surrogatepass` because a
        lone surrogate survives a JSON body and has no plain UTF-8 form; the
        encoding is injective either way, so no two distinct nonces collide.
        """
        stored = pending.state
        if stored is not None and supplied is not None:
            if secrets.compare_digest(
                stored.encode("utf-8", "surrogatepass"),
                supplied.encode("utf-8", "surrogatepass"),
            ):
                return
        elif stored is None and supplied is None:
            return
        # Deleted and committed before raising — see the docstring on
        # `complete_dropbox`: a refused flow that stays redeemable is a nonce
        # nobody has to guess right the first time.
        for row in await self._repository.authorizations(ConnectionProvider.DROPBOX):
            await self._repository.delete_authorization(row)
        await commit(self._session)
        raise ValidationError(
            "arc could not verify that connection came back from the link it "
            "sent you, so it has been stopped. Start the connection again."
        )

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
            await refresh(self._session, row, ["feeds"])
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
                already says is a request the rate limit will want later — or
                when Dropbox refused the read for want of a scope.
            RateLimitedError: When Dropbox is throttling arc, carrying its own
                stated delay. Translated rather than left to escape, because an
                upstream 429 reaching the client as a 500 makes a transient
                condition look like a broken feature.
            UpstreamError: When Dropbox answered with a failure arc did not
                cause.
            ValidationError: When arc cannot read its own credential, or
                Dropbox could not be reached at all.
        """
        client = await self._readable_client(connection_id)
        with _dropbox_failures_translated():
            return await client.list_folders(path)

    async def discover_folders(self, connection_id: uuid.UUID) -> FolderDiscovery:
        """Name the folders the athlete's activity files are already in.

        **The search is the root's folders plus one level under `/Apps`, not a
        recursive sweep.** `list_folder(recursive=True)` from the root would be
        the obvious implementation and it is unbounded: a real Dropbox holds
        photo libraries and project archives with six-figure entry counts, and
        the listing arc would have to walk to the end before ranking anything
        is the athlete's whole cloud drive. Two levels is enough because every
        producer this exists for — Wahoo, HealthFit — writes *flat* into its
        own folder under the app container, so the ride files are exactly one
        level below `/Apps` or sitting in a folder at the top. Anything deeper
        is what the manual browser is for, and it is untouched.

        **The App-folder diagnosis needs an empty root *and* an absent
        `/Apps`.** An App-folder Dropbox app can only ever see its own
        directory, so arc — which is not that directory — sees a Dropbox with
        nothing in it, `/Apps` included, and that pair is the signature. The
        empty root alone is not: a genuinely empty Dropbox produces it too, and
        accusing that athlete of a misconfiguration sends them to delete a
        Dropbox app that was working. The remedy carries a cost (Dropbox cannot
        change an app's access type; the app has to be re-registered), which is
        exactly why it is not offered on a guess. Nothing is inferred from a
        `/Apps` probe that failed for any *other* reason — a 429 is Dropbox
        being busy, not a statement about what arc may see.

        Raises:
            NotFoundError: When the connection does not exist.
            ConflictError: When the credential needs re-authorizing, or
                Dropbox refused the read for want of a scope.
            RateLimitedError: When Dropbox is throttling arc.
            UpstreamError: When Dropbox answered with a failure arc did not
                cause.
            ValidationError: When arc cannot read its own credential, or
                Dropbox could not be reached at all.
        """
        client = await self._readable_client(connection_id)
        with _dropbox_failures_translated():
            root = await client.list_entries("")
            listings = {"": root}
            apps, apps_missing = await self._probe_app_container(client)
            if apps is not None:
                listings[normalise_remote_path(APP_CONTAINER)] = apps
            search = [folder.path_lower for folder in root.folders] + [
                folder.path_lower for folder in (apps.folders if apps else ())
            ]
            candidates: list[FolderCandidate] = []
            # `dict.fromkeys` de-duplicates while keeping the order: `/Apps` is
            # both a folder of the root and the container that was probed, and
            # listing it twice would double a count as well as a request.
            for path in dict.fromkeys(search):
                listing = await self._listing(client, path, listings)
                counted = _count_activity(path, listing)
                # A folder with nothing arc can read is left out rather than
                # reported as zero: this list is an answer to "where are your
                # rides", and a folder that holds none is not an answer to it.
                if counted.activity_files:
                    candidates.append(counted)
        return FolderDiscovery(
            # Most files first, then the folder still being written to: two
            # folders holding one ride each are told apart by which one the
            # head unit touched this week.
            candidates=tuple(
                sorted(
                    candidates,
                    key=lambda entry: (
                        -entry.activity_files,
                        -(entry.newest_at or _NEVER).timestamp(),
                    ),
                )
            ),
            access_type_suspect=APP_FOLDER_SUSPECT
            if root.is_empty and apps_missing
            else None,
        )

    async def _probe_app_container(
        self, client: DropboxClient
    ) -> tuple[DropboxListing | None, bool]:
        """List `/Apps`, and say whether Dropbox stated it is not there.

        The second half of the answer is deliberately narrower than "the probe
        failed": only `path/not_found` is evidence about what this credential
        can see. Every other failure — a 429, a 503, a dead socket — is
        answered with "no listing, and no inference", so a bad minute upstream
        cannot produce a diagnosis telling the athlete to delete their app.
        """
        try:
            return await client.list_entries(APP_CONTAINER), False
        except DropboxPathNotFoundError:
            return None, True
        except DropboxError as exc:
            logger.info("dropbox_app_container_probe_failed", error=type(exc).__name__)
            return None, False

    async def _listing(
        self,
        client: DropboxClient,
        path: str,
        listings: dict[str, DropboxListing],
    ) -> DropboxListing:
        """One candidate folder's contents, reusing the `/Apps` probe's answer.

        A folder that has gone between the root listing and this call counts as
        empty rather than aborting the discovery: the athlete is being offered
        the folders that *are* there, and one that vanished mid-read is simply
        not one of them.
        """
        if path in listings:
            return listings[path]
        try:
            listings[path] = await client.list_entries(path)
        except DropboxPathNotFoundError:
            listings[path] = DropboxListing(folders=(), files=())
        return listings[path]

    async def _readable_client(self, connection_id: uuid.UUID) -> DropboxClient:
        """A client for a connection arc can actually read Dropbox with.

        Refused locally rather than upstream: spending a request to be told
        what the row already says is a request the rate limit will want later.

        Raises:
            NotFoundError: When the connection does not exist.
            ConflictError: When the credential needs re-authorizing.
            ValidationError: When arc cannot read its own credential.
        """
        row = await self.get(connection_id)
        if row.status is ConnectionStatus.NEEDS_REAUTH:
            raise ConflictError(
                "This Dropbox connection needs re-authorising before arc can "
                "read folders from it. Reconnect the account."
            )
        if row.status is ConnectionStatus.ERROR:
            raise ValidationError(row.last_error or f"{KEY_SETTING} is not usable")
        return await self._client_for(row)

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


@contextlib.contextmanager
def _dropbox_failures_translated() -> Iterator[None]:
    """Turn a Dropbox failure into the `AppError` the adapter can answer with.

    One place rather than one per read, because the mapping is the *contract*
    the folder picker, discovery and the connect all publish: an upstream 429
    reaching the client as a 500 makes a transient condition look like a broken
    feature, and a refused credential has to arrive as the one status the panel
    offers a reconnect for.

    **"could not be reached" is reserved for a request nothing answered.**
    Every other Dropbox failure used to be re-labelled with it — an answered
    409, an answered 503, a missing scope — which is arc discarding what it was
    told and substituting a guess about the network. The audited run-through
    began exactly there: Dropbox had said which permission was missing and
    which console tab fixes it, and the athlete was shown a question about
    reachability. So a *transport* failure (:class:`DropboxUnreachableError`)
    keeps that sentence and nothing else does; an answered failure quotes
    Dropbox's own summary into a 502, whose status is what tells the frontend
    this is not a refusal the athlete can act on
    (`frontend/lib/api-errors.ts`).

    The order of the clauses is the order of specificity, and it is load
    bearing: :class:`DropboxScopeError` is a :class:`DropboxAuthError`, and
    :class:`DropboxUnreachableError` a :class:`DropboxUpstreamError`, so a
    reordering would silently answer the precise case with the general
    sentence.
    """
    try:
        yield
    except DropboxPathNotFoundError as exc:
        raise NotFoundError(f"Dropbox has no folder at {exc.path or '/'}") from exc
    except DropboxScopeError as exc:
        # Dropbox named the permission. Naming it back — with the four console
        # moves that grant it — is the difference between a refusal the
        # athlete resolves in two minutes and one they file a bug about.
        raise ConflictError(scope_refusal(exc.required_scope)) from exc
    except DropboxAuthError as exc:
        raise ConflictError(PERMISSION_LOST) from exc
    except DropboxRateLimitedError as exc:
        raise RateLimitedError(
            "Dropbox is rate-limiting arc. Try again in about "
            f"{int(exc.retry_after)} seconds."
        ) from exc
    except DropboxUnreachableError as exc:
        raise ValidationError(f"Dropbox could not be reached: {exc}") from exc
    except DropboxError as exc:
        raise UpstreamError(
            f"Dropbox answered arc with an error it cannot fix ({exc}). "
            "Nothing is wrong with your setup — try again in a few minutes."
        ) from exc


def _count_activity(path: str, listing: DropboxListing) -> FolderCandidate:
    """How much of a folder is activity data, and how recent the newest is."""
    stamps = [
        file.client_modified
        for file in listing.files
        if is_activity_file(file.name) and file.client_modified is not None
    ]
    return FolderCandidate(
        path=path,
        activity_files=sum(1 for file in listing.files if is_activity_file(file.name)),
        newest_at=max(stamps, default=None),
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

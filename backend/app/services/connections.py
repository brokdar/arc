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
    ConnectionProvider,
    ConnectionStatus,
    normalise_remote_path,
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

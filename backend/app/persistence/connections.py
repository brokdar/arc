"""Cloud connections, the folders arc watches, and the PKCE flows in progress.

Four tables, one aggregate:

* ``provider_apps`` — the OAuth app the athlete registered, which has to exist
  before a connection can be made at all;
* ``connections`` — one row per provider arc holds a credential for, with the
  credential itself sealed in :class:`EncryptedCredentials`;
* ``feeds`` — one row per folder arc watches on a connection, carrying its
  whole polling state from the day it is created;
* ``oauth_authorizations`` — a PKCE flow the athlete has started and not yet
  finished.

``oauth_authorizations`` is a **table** rather than a dict in the API process,
and that is the non-obvious one. The athlete's paste crosses a browser tab and
a trip to dropbox.com, so the verifier has to survive minutes of wall-clock and
a restart of the container in between; an in-memory dict loses the flow on a
redeploy and says nothing about it — the athlete pastes a perfectly good code
and is told it is invalid. Each row carries its own ``expires_at`` (15 minutes,
`app.services.connections.AUTHORIZATION_TTL`), so a flow that was abandoned
cannot be completed later by whoever finds the code in a browser history.
"""

import datetime as dt
import json
import uuid
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Final

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship, selectinload

from app.core.config import get_settings
from app.domain.connections import ConnectionProvider, ConnectionStatus
from app.persistence.db import Base, flush, refresh
from app.persistence.types import JSONColumn, UtcDateTime, enum_column

if TYPE_CHECKING:  # pragma: no cover - import cycle broken for the annotation
    # `app.persistence.integrations` imports `FeedRow` from here, so the
    # reverse edge exists only for the type checker; SQLAlchemy resolves the
    # relationship through its own class registry, which `load_models()` fills.
    from app.persistence.integrations import IntegrationRow

#: Longest account label kept — a display name plus an email address.
MAX_ACCOUNT_LABEL_LENGTH = 300

#: Longest remote folder path. Dropbox's own limit is well under this.
MAX_REMOTE_PATH_LENGTH = 1_000

#: Longest stored failure sentence, on a connection or a feed.
MAX_ERROR_LENGTH = 1_000

#: Longest PKCE verifier RFC 7636 allows.
MAX_VERIFIER_LENGTH = 128

#: Longest OAuth `state` nonce stored. Generous: the value is arc's own, and
#: 128 characters is far more entropy than the flow needs.
MAX_STATE_LENGTH = 128

#: Longest redirect URI stored — an origin plus arc's own callback path.
MAX_REDIRECT_URI_LENGTH = 500

#: Longest app key accepted. A Dropbox app key is 15 characters; the headroom
#: is for the next provider, not for a paste of something that is not a key.
MAX_APP_KEY_LENGTH = 128

#: Named in every message this module raises, so the operator is never left
#: guessing between a rotated key and a corrupted column.
KEY_SETTING: Final = "SECRETS__ENCRYPTION_KEY"


class CredentialKeyError(RuntimeError):
    """`SECRETS__ENCRYPTION_KEY` is missing or is not a usable Fernet key."""


class CredentialDecryptionError(RuntimeError):
    """A stored credential will not open under the key this process holds."""


class EncryptedCredentials:
    """Fernet-sealed OAuth credentials, as stored in ``connections.credentials``.

    Encrypted rather than stored in the clear, which is what
    `AUTH__PASSWORD_HASH` does one table over — and the difference is the whole
    argument. A bcrypt hash is one-way: an attacker reading it gains nothing
    they can present anywhere. A Dropbox refresh token is a **live key to the
    athlete's own file store**, valid until it is revoked, and anything that can
    read the database can otherwise use it: a backup on a NAS, a `pg_dump` in a
    home directory, a screenshot of a psql session.

    Fernet (AES-128-CBC + HMAC-SHA256, from `cryptography`) rather than a
    hand-rolled construction: it is authenticated, so a blob written under a
    different key fails loudly instead of decrypting to plausible garbage —
    which is exactly the behaviour AC-8 pins, and what lets a connection whose
    key has moved report ``error`` rather than silently failing to refresh.

    The key is read from settings on **every** call rather than cached: it is
    the seam a test rotates, and an operator restoring a key should not have to
    guess whether a process has memoised the old one.
    """

    @staticmethod
    def _cipher() -> Fernet:
        key = get_settings().secrets.encryption_key.get_secret_value()
        if not key:
            raise CredentialKeyError(
                f"{KEY_SETTING} is not set, so arc cannot store or read a "
                "cloud credential. Generate one with "
                '`python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"`.'
            )
        try:
            return Fernet(key)
        except (ValueError, TypeError) as exc:
            raise CredentialKeyError(
                f"{KEY_SETTING} is not a valid Fernet key (32 random bytes, "
                f"urlsafe-base64 encoded): {exc}"
            ) from exc

    @staticmethod
    def seal(credentials: Mapping[str, Any]) -> bytes:
        """Encrypt a credential document for storage.

        Raises:
            CredentialKeyError: When `SECRETS__ENCRYPTION_KEY` is unusable.
        """
        return EncryptedCredentials._cipher().encrypt(
            json.dumps(dict(credentials), separators=(",", ":")).encode()
        )

    @staticmethod
    def unseal(blob: bytes) -> dict[str, Any]:
        """Decrypt a stored credential document.

        Raises:
            CredentialKeyError: When `SECRETS__ENCRYPTION_KEY` is unusable.
            CredentialDecryptionError: When the blob was sealed under another
                key (or was tampered with) — never a partial or empty result.
        """
        try:
            plaintext = EncryptedCredentials._cipher().decrypt(blob)
        except InvalidToken as exc:
            raise CredentialDecryptionError(
                f"The stored credential cannot be decrypted with the current "
                f"{KEY_SETTING}. Restore the key this connection was created "
                "under, or disconnect and connect again."
            ) from exc
        return json.loads(plaintext)


class ConnectionRow(Base):
    """One provider arc holds a credential for. At most one per provider."""

    __tablename__ = "connections"
    __table_args__ = (
        # One connection per provider, held by the database. The service
        # answers a second connect with a 409 naming disconnect as the remedy;
        # this is what makes that true for a race and for anything writing
        # outside the service.
        UniqueConstraint("provider", name="uq_connections_provider"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    provider: Mapped[ConnectionProvider] = mapped_column(
        enum_column(ConnectionProvider)
    )
    status: Mapped[ConnectionStatus] = mapped_column(
        enum_column(ConnectionStatus), default=ConnectionStatus.CONNECTED, index=True
    )
    #: How the athlete recognises the account — display name and email, as
    #: `/2/users/get_current_account` reports them. Nullable because a
    #: credential that works is still usable if that read failed.
    account_label: Mapped[str | None] = mapped_column(String(MAX_ACCOUNT_LABEL_LENGTH))
    #: The scopes Dropbox actually **granted**, not the ones arc asked for.
    #: The two differ when a permission was never added in the app console,
    #: and believing the request over the grant is how a feature discovers it
    #: has no permission at the moment it tries to use it.
    scopes: Mapped[list[str]] = mapped_column(JSONColumn, default=list)
    #: Fernet ciphertext; see :class:`EncryptedCredentials`.
    credentials: Mapped[bytes] = mapped_column(LargeBinary)
    #: When the stored access token stops working, so the client can refresh
    #: before spending a request to be told. Null means "assume expired".
    access_token_expires_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    #: Why the connection is not `connected`, in the athlete's words.
    last_error: Mapped[str | None] = mapped_column(String(MAX_ERROR_LENGTH))
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now()
    )

    feeds: Mapped[list[FeedRow]] = relationship(
        back_populates="connection",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="FeedRow.remote_path",
    )


class FeedRow(Base):
    """One folder arc watches on a connection, and everything the poll needs.

    The four polling columns — ``cursor``, ``cursor_attempts``,
    ``last_delivery_at``, ``last_error`` — are written by `app.ingest.feeds`,
    which owns the rules they encode: the cursor moves only once a whole batch
    is resolved, the attempt counter is consecutive and resets on any success,
    and ``last_delivery_at`` records hearing from Dropbox at all rather than a
    ride arriving. Read them there before changing anything here.

    There is deliberately **no delivery counter**. "How many rides has this
    folder brought in this week" is answered by counting the audit trail
    (`app.domain.connections.FEED_DELIVERED_ACTION`), which already records
    every delivery against the feed it belonged to; a column beside it would be
    a second answer that can drift from the first.
    """

    __tablename__ = "feeds"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "remote_path", name="uq_feeds_connection_id_remote_path"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connections.id", ondelete="CASCADE"), index=True
    )
    #: Which source the athlete asked arc to collect through this folder.
    #:
    #: **Nullable, and it means something**: "configured before integrations
    #: existed, not yet classified". A folder watched since WP-4.3 keeps
    #: collecting — `0017` classifies only the ones whose path *is* a catalogue
    #: default and guesses at nothing else, because a wrong guess here would
    #: put a folder under a name the athlete never chose and no later edit
    #: would question it. Deleting the integration takes the feed with it.
    integration_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("integrations.id", ondelete="CASCADE"), index=True
    )
    #: Normalised by `app.domain.connections.normalise_remote_path`. The empty
    #: string is the Dropbox root, which is legal if unwise.
    remote_path: Mapped[str] = mapped_column(String(MAX_REMOTE_PATH_LENGTH))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Dropbox's `list_folder` cursor. Null until the first poll.
    cursor: Mapped[str | None] = mapped_column(Text)
    #: Consecutive failed attempts on the current cursor.
    cursor_attempts: Mapped[int] = mapped_column(Integer, default=0)
    #: When this feed last heard from Dropbox at all — the silence signal.
    last_delivery_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    last_error: Mapped[str | None] = mapped_column(String(MAX_ERROR_LENGTH))
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )

    connection: Mapped[ConnectionRow] = relationship(back_populates="feeds")
    integration: Mapped[IntegrationRow | None] = relationship(back_populates="feeds")


class OAuthAuthorizationRow(Base):
    """A PKCE flow the athlete has started and not yet pasted a code for.

    The verifier is stored in the clear, deliberately: it is worthless without
    the one-time authorization code Dropbox shows the athlete, it lives for
    fifteen minutes, and encrypting it would put the connect flow behind
    `SECRETS__ENCRYPTION_KEY` before there is anything to protect — an
    operator who has not set the key yet would be told about it by a
    decryption failure instead of by the thing that actually needs it.
    """

    __tablename__ = "oauth_authorizations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    provider: Mapped[ConnectionProvider] = mapped_column(
        enum_column(ConnectionProvider), index=True
    )
    code_verifier: Mapped[str] = mapped_column(String(MAX_VERIFIER_LENGTH))
    #: The CSRF nonce a redirect flow round-trips through Dropbox, and the URI
    #: Dropbox is told to send the athlete back to.
    #:
    #: Both **nullable, and null together**: a paste flow has neither, because
    #: Dropbox shows the athlete a code instead of redirecting anywhere and
    #: there is nothing to round-trip. `ConnectionService.complete_dropbox`
    #: reads that as a rule in both directions — a row holding a state wants
    #: one back, a row holding none must be completed without one — so "null"
    #: here is a state the service asserts on, not a value that was not filled
    #: in yet.
    #:
    #: They shipped inert one release ahead of the flow that uses them, so
    #: adding the redirect was a behaviour change and not a schema change on a
    #: database already holding live credentials.
    state: Mapped[str | None] = mapped_column(String(MAX_STATE_LENGTH))
    redirect_uri: Mapped[str | None] = mapped_column(String(MAX_REDIRECT_URI_LENGTH))
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )
    expires_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)


class ProviderAppRow(Base):
    """The OAuth app the athlete registered, as pasted into the add flow.

    One row per provider, and it exists *before* any connection does — which
    is the whole reason it is a table rather than a column on `ConnectionRow`.
    The app key is what the connect flow needs in order to produce a link at
    all, so a nullable column on a row that does not yet exist could not hold
    it.

    **The key is stored in plaintext**, beside a `connections.credentials`
    column that is Fernet-sealed, and the asymmetry is deliberate. A Dropbox
    app key is a *public* OAuth client id: PKCE is what protects this flow,
    there is no app secret, and the key travels in a query string to
    dropbox.com in every athlete's browser. Sealing it would buy no secrecy
    and would cost the one thing that matters here — an instance whose
    `SECRETS__ENCRYPTION_KEY` was lost would lose the ability to *re-connect*
    as well as the ability to read the old credential, i.e. the single remedy
    for the failure that key loss causes.
    """

    __tablename__ = "provider_apps"
    __table_args__ = (
        # One app per provider, held by the database: a second key for Dropbox
        # would leave arc guessing which app a stored credential belongs to.
        UniqueConstraint("provider", name="uq_provider_apps_provider"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    provider: Mapped[ConnectionProvider] = mapped_column(
        enum_column(ConnectionProvider)
    )
    app_key: Mapped[str] = mapped_column(String(MAX_APP_KEY_LENGTH))
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now()
    )


class ConnectionRepository:
    """SQLAlchemy repository for apps, connections, feeds and authorizations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- registered apps -------------------------------------------------------

    async def provider_app(self, provider: ConnectionProvider) -> ProviderAppRow | None:
        """The app the athlete registered for a provider, or None."""
        result = await self._session.execute(
            select(ProviderAppRow).where(ProviderAppRow.provider == provider)
        )
        return result.scalars().first()

    async def replace_provider_app(
        self, provider: ConnectionProvider, *, app_key: str
    ) -> ProviderAppRow:
        """Store the app key for a provider, replacing any earlier one.

        Written as an update-or-insert on the existing row rather than a
        delete-and-add, so `created_at` keeps saying when the athlete first
        set arc up while `updated_at` moves — the shape
        :meth:`replace_authorization` uses for a value that supersedes rather
        than accumulates.
        """
        row = await self.provider_app(provider)
        if row is None:
            row = ProviderAppRow(provider=provider, app_key=app_key)
            self._session.add(row)
        else:
            row.app_key = app_key
        await flush(self._session)
        await refresh(self._session, row)
        return row

    async def delete_provider_app(self, row: ProviderAppRow) -> None:
        """Forget a registered app, leaving whatever the environment says."""
        await self._session.delete(row)
        await flush(self._session)

    # --- connections ---------------------------------------------------------

    async def list(self) -> Sequence[ConnectionRow]:
        """Every connection, feeds eagerly loaded.

        Eager, because the response embeds them and an async session cannot
        lazy-load: a plain attribute access on `feeds` would raise
        `MissingGreenlet` at serialisation time rather than at the query.
        """
        result = await self._session.execute(
            select(ConnectionRow)
            .options(selectinload(ConnectionRow.feeds))
            .order_by(ConnectionRow.created_at)
        )
        return list(result.scalars())

    async def get(self, connection_id: uuid.UUID) -> ConnectionRow | None:
        """Return one connection with its feeds, or None."""
        result = await self._session.execute(
            select(ConnectionRow)
            .options(selectinload(ConnectionRow.feeds))
            .where(ConnectionRow.id == connection_id)
        )
        return result.scalars().first()

    async def by_provider(self, provider: ConnectionProvider) -> ConnectionRow | None:
        """Return the connection for a provider, or None."""
        result = await self._session.execute(
            select(ConnectionRow)
            .options(selectinload(ConnectionRow.feeds))
            .where(ConnectionRow.provider == provider)
        )
        return result.scalars().first()

    async def add(self, row: ConnectionRow) -> ConnectionRow:
        """Persist a connection and refresh it."""
        self._session.add(row)
        await flush(self._session)
        await refresh(self._session, row, ["feeds"])
        return row

    async def delete(self, row: ConnectionRow) -> None:
        """Delete a connection; its feeds go with it by cascade."""
        await self._session.delete(row)
        await flush(self._session)

    # --- feeds ---------------------------------------------------------------

    async def get_feed(self, feed_id: uuid.UUID) -> FeedRow | None:
        """Return one feed, or None."""
        return await self._session.get(FeedRow, feed_id)

    async def feed_for_path(
        self, connection_id: uuid.UUID, remote_path: str
    ) -> FeedRow | None:
        """Return the feed on this connection watching ``remote_path``, or None."""
        result = await self._session.execute(
            select(FeedRow).where(
                FeedRow.connection_id == connection_id,
                FeedRow.remote_path == remote_path,
            )
        )
        return result.scalars().first()

    async def add_feed(self, row: FeedRow) -> FeedRow:
        """Persist a feed and refresh it."""
        self._session.add(row)
        await flush(self._session)
        await refresh(self._session, row)
        return row

    async def delete_feed(self, row: FeedRow) -> None:
        """Delete one feed."""
        await self._session.delete(row)
        await flush(self._session)

    # --- pending authorizations ----------------------------------------------

    async def replace_authorization(
        self, row: OAuthAuthorizationRow
    ) -> OAuthAuthorizationRow:
        """Store a new pending flow, dropping any earlier one for the provider.

        A second "connect" supersedes the first: two live verifiers would mean
        arc guessing which one the pasted code belongs to, and guessing wrong
        looks to the athlete exactly like a code that does not work.
        """
        for existing in await self.authorizations(row.provider):
            await self._session.delete(existing)
        self._session.add(row)
        await flush(self._session)
        await refresh(self._session, row)
        return row

    async def authorizations(
        self, provider: ConnectionProvider
    ) -> Sequence[OAuthAuthorizationRow]:
        """Every pending flow for a provider, newest first."""
        result = await self._session.execute(
            select(OAuthAuthorizationRow)
            .where(OAuthAuthorizationRow.provider == provider)
            .order_by(OAuthAuthorizationRow.created_at.desc())
        )
        return list(result.scalars())

    async def delete_authorization(self, row: OAuthAuthorizationRow) -> None:
        """Spend (or discard) one pending flow."""
        await self._session.delete(row)
        await flush(self._session)

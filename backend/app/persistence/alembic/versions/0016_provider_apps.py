"""The Dropbox app the athlete registers, and two columns for the redirect.

`provider_apps` holds the OAuth app key the athlete pastes into the settings
panel, one row per provider, unique on `provider`. It is a table rather than a
column on `connections` because the key has to exist *before* any connection
does — producing the authorize link is the first thing it is needed for — and
a nullable column on a row that does not yet exist cannot hold it. The key is
stored in the clear: it is a public OAuth client id (PKCE, no app secret), and
sealing it under `SECRETS__ENCRYPTION_KEY` would mean losing that key also
destroys the ability to *re-connect*, which is the only remedy for losing it.

The two columns added to `oauth_authorizations` — `state` and `redirect_uri` —
are shipped **inert**: nothing writes them yet, and the paste flow that is the
only flow today stores neither. They are here so the redirect PR that follows
is behaviour and no migration, the same trick `0015` used for the four polling
columns on `feeds`. Nullable is what makes it safe in both directions: an
upgrade leaves a flow already in progress redeemable, and a downgrade takes
both columns away with the table.

Nothing is backfilled. An instance with no Dropbox app has one more empty
table and behaves exactly as it did before.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.persistence.types

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Non-native VARCHAR holding the member VALUE, as `enum_column` renders it —
#: the same object `0015` created the other three tables with.
CONNECTION_PROVIDER = sa.Enum("dropbox", name="connectionprovider", native_enum=False)


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "provider_apps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", CONNECTION_PROVIDER, nullable=False),
        sa.Column("app_key", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            app.persistence.types.UtcDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            app.persistence.types.UtcDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_apps")),
        sa.UniqueConstraint("provider", name="uq_provider_apps_provider"),
    )

    op.add_column(
        "oauth_authorizations", sa.Column("state", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "oauth_authorizations",
        sa.Column("redirect_uri", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    """Revert the migration.

    The stored app key goes with it, and the athlete re-pastes it (or sets
    `DROPBOX__APP_KEY`, which this table only overrides). A connection made
    with the stored key survives and keeps working as long as the environment
    names the same app — arc reports which source is in force either way.
    """
    op.drop_column("oauth_authorizations", "redirect_uri")
    op.drop_column("oauth_authorizations", "state")
    op.drop_table("provider_apps")

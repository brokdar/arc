"""The Dropbox app the athlete registers, stored so no restart is needed.

`provider_apps` holds the OAuth app key the athlete pastes into the
add-integration flow, one row per provider, unique on `provider`. It is a
table rather than a column on `connections` because the key has to exist
*before* any connection does — producing the authorize link is the first
thing it is needed for — and a nullable column on a row that does not yet
exist cannot hold it. The key is stored in the clear: it is a public OAuth
client id (PKCE, no app secret), and sealing it under
`SECRETS__ENCRYPTION_KEY` would mean losing that key also destroys the
ability to *re-connect*, which is the only remedy for losing it.

The two inert `oauth_authorizations` columns this table's first draft shipped
beside (`state`, `redirect_uri`) are not here: `0017` carried them while this
revision waited its turn.

Nothing is backfilled. An instance with no Dropbox app has one more empty
table and behaves exactly as it did before.

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.persistence.types

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Non-native VARCHAR holding the member VALUE, as `enum_column` renders it —
#: the same object `0015` created the other three connection tables with.
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


def downgrade() -> None:
    """Revert the migration.

    The stored app key goes with it, and the athlete re-pastes it (or sets
    `DROPBOX__APP_KEY`, which this table only overrides). A connection made
    with the stored key survives and keeps working as long as the environment
    names the same app — arc reports which source is in force either way.
    """
    op.drop_table("provider_apps")

"""Cloud connections, watched folders, and the PKCE flows in between.

Three tables. `connections` holds one credential per provider — sealed, as
`bytea`, never readable from the row alone — with a unique constraint on
`provider` that is what makes "one Dropbox account, disconnect to change it"
true for a race as well as for a well-behaved client. `feeds` holds one row per
watched folder, unique on `(connection_id, remote_path)` over the *normalised*
path, and carries its whole polling state from birth: `cursor`,
`cursor_attempts`, `last_delivery_at` and `last_error` are written by the
delivery PR that follows this one, and are here now so that PR ships no
migration of its own. `oauth_authorizations` holds a started-but-unfinished
connect flow, because the athlete's paste crosses a browser tab and possibly a
container restart.

Nothing is backfilled and nothing is read at boot: an instance with no Dropbox
account has three empty tables and behaves exactly as it did before.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.persistence.types

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Non-native VARCHARs holding the member VALUE, no CHECK constraint — the
#: convention `app.persistence.types.enum_column` sets.
CONNECTION_PROVIDER = sa.Enum("dropbox", name="connectionprovider", native_enum=False)
CONNECTION_STATUS = sa.Enum(
    "connected", "needs_reauth", "error", name="connectionstatus", native_enum=False
)


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", CONNECTION_PROVIDER, nullable=False),
        sa.Column("status", CONNECTION_STATUS, nullable=False),
        sa.Column("account_label", sa.String(length=300), nullable=True),
        sa.Column("scopes", app.persistence.types.JSONColumn, nullable=False),
        # Fernet ciphertext under SECRETS__ENCRYPTION_KEY. `bytea` rather than
        # text: the sealed blob is bytes, and storing it base64'd a second time
        # would only make it look like something a human could read.
        sa.Column("credentials", sa.LargeBinary(), nullable=False),
        sa.Column(
            "access_token_expires_at",
            app.persistence.types.UtcDateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connections")),
        sa.UniqueConstraint("provider", name="uq_connections_provider"),
    )
    op.create_index(
        op.f("ix_connections_status"), "connections", ["status"], unique=False
    )

    op.create_table(
        "feeds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("remote_path", sa.String(length=1000), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("cursor_attempts", sa.Integer(), nullable=False),
        sa.Column(
            "last_delivery_at",
            app.persistence.types.UtcDateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            app.persistence.types.UtcDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Deleting a connection takes its feeds with it: a feed without a
        # credential is a folder nothing can ever read.
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["connections.id"],
            name=op.f("fk_feeds_connection_id_connections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feeds")),
        sa.UniqueConstraint(
            "connection_id", "remote_path", name="uq_feeds_connection_id_remote_path"
        ),
    )
    op.create_index(
        op.f("ix_feeds_connection_id"), "feeds", ["connection_id"], unique=False
    )

    op.create_table(
        "oauth_authorizations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", CONNECTION_PROVIDER, nullable=False),
        sa.Column("code_verifier", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            app.persistence.types.UtcDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            app.persistence.types.UtcDateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_authorizations")),
    )
    op.create_index(
        op.f("ix_oauth_authorizations_provider"),
        "oauth_authorizations",
        ["provider"],
        unique=False,
    )


def downgrade() -> None:
    """Revert the migration.

    Every stored credential goes with it. That is the honest outcome — the
    tables are the only place arc keeps them — and the athlete reconnects,
    which is a two-minute ritual and not a data loss: no session, no recording
    and no ingest event lives here.
    """
    op.drop_index(
        op.f("ix_oauth_authorizations_provider"), table_name="oauth_authorizations"
    )
    op.drop_table("oauth_authorizations")
    op.drop_index(op.f("ix_feeds_connection_id"), table_name="feeds")
    op.drop_table("feeds")
    op.drop_index(op.f("ix_connections_status"), table_name="connections")
    op.drop_table("connections")

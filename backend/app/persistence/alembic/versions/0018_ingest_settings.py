"""The sweep interval, out of `.env` and into a table the athlete can write.

One table, `ingest_settings`, unique on `scope`. It is empty on every existing
installation and an empty table changes nothing: with no row, the local drop
sweeps on `INGEST__SCAN_INTERVAL_SECONDS` exactly as it has since WP-4.3. The
environment stays the seed; a row overrides it.

**Nothing is backfilled, deliberately.** Writing today's environment value into
a row at upgrade time would look harmless and would freeze it: from then on the
operator could change `INGEST__SCAN_INTERVAL_SECONDS`, restart, and watch arc
ignore them, because a stored value outranks the environment by design. Absent
has to keep meaning "the athlete has not taken this out of the file's hands".

**Why here and not on `integrations`.** The value belongs to the local drop,
and the local drop has no row — `0017` writes none on purpose, so that a sweep
running since WP-4.3 cannot be deleted from Settings. There is no row to add a
column to.

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.persistence.types

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "ingest_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=40), nullable=False),
        sa.Column("scan_interval_seconds", sa.Integer(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingest_settings")),
        sa.UniqueConstraint("scope", name="uq_ingest_settings_scope"),
    )


def downgrade() -> None:
    """Revert the migration.

    The athlete's interval goes with the table and the sweep falls back to
    `INGEST__SCAN_INTERVAL_SECONDS` — which is what it was running on before
    this migration, so a downgraded instance keeps collecting either way.
    """
    op.drop_table("ingest_settings")

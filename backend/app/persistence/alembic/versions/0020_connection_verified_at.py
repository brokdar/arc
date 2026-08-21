"""When arc last saw a stored credential actually work.

`connections.last_verified_at` is what turns `status = 'connected'` from a
claim into an observation. Before it, `connected` meant "nothing has told arc
otherwise" — a sentence a row could go on saying for weeks after a permission
change in the Dropbox console killed the grant, because nothing ever asked.

Nullable, and null is a **state** rather than a gap: it means nobody has
checked this connection yet, which is exactly true of every row that exists
when this migration runs and of a connection stored under the transient-probe
rule. So there is no backfill — writing `created_at` or `now()` into it would
manufacture a verification that never happened, which is the defect this
column exists to end. The first successful `list_folder` fills it in.

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.persistence.types

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column(
        "connections",
        # No `batch_alter_table`: adding a nullable column is the one ALTER
        # SQLite has always supported, so the plain form is portable here.
        sa.Column(
            "last_verified_at",
            app.persistence.types.UtcDateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Revert the migration.

    The stamps go with the column and the panel returns to reporting health as
    the absence of an error. Nothing else is touched: no status was derived
    from this value, so a downgraded instance keeps every connection it had.
    """
    op.drop_column("connections", "last_verified_at")

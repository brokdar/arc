"""Session context: ambient temperature on the session row (#23).

One nullable column. A session stores what the device measured and what the
athlete concluded; the *conditions* the measurement was taken under — heat
above all, which decouples heart rate from power — lived only in prose, where
nothing can filter on them. `temperature_c` is the cheapest queryable slice of
that: one athlete-reported number per session, bounds enforced by the domain
(`app.domain.activity.check_temperature`), ahead of the MMP's fuller
observed-conditions model (D210). `rpe` needed no migration: the column has
existed since 0005 and only its write paths grew.

Nothing is backfilled: no session recorded before this revision carries a
temperature, and null is the honest value for "nobody said".

Batch (move-and-copy) style like every alter here, so the same code runs on
SQLite and Postgres.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    with op.batch_alter_table("sessions") as batch:
        batch.add_column(sa.Column("temperature_c", sa.Float(), nullable=True))


def downgrade() -> None:
    """Revert the migration."""
    with op.batch_alter_table("sessions") as batch:
        batch.drop_column("temperature_c")

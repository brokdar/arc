"""Logged sets learn per-side and timed holds (#25).

Three changes to `logged_sets`, all of them the storage half of one shape
decision made in `app.domain.strength.StrengthSet`:

* `per_side` — whether the row is one side of a bilateral movement, and so
  **two** working sets. The logged row carries it as well as the prescription
  because completion divides logged sets by prescribed sets, and the two have
  to be the same unit or the ratio is nonsense. `server_default false` so the
  rows already stored get the meaning they always had.
* `duration_s` — seconds held, for a timed set.
* `reps` becomes nullable — a 45-second plank has no rep count, and storing
  `1` for it was a made-up number entering volume arithmetic as if it were
  work. Exactly one of the two is set; the rule is stated in the domain and
  enforced by the service rather than by a check constraint, so the message
  the athlete sees is the same one on every surface.

Nothing is backfilled: every row stored before this revision is a rep-based,
bilateral set, which is exactly what the defaults say.

Batch (move-and-copy) style like every alter here, so the same code runs on
SQLite — which cannot ALTER a column's nullability at all — and Postgres.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    with op.batch_alter_table("logged_sets") as batch:
        batch.add_column(sa.Column("duration_s", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "per_side",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.alter_column("reps", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    """Revert the migration.

    Timed sets have no rep count, so they cannot survive a column that
    requires one: they are dropped rather than given an invented `reps` value,
    which is the same refusal the upgrade exists to make possible.
    """
    op.execute(sa.text("DELETE FROM logged_sets WHERE reps IS NULL"))
    with op.batch_alter_table("logged_sets") as batch:
        batch.alter_column("reps", existing_type=sa.Integer(), nullable=False)
        batch.drop_column("per_side")
        batch.drop_column("duration_s")

"""WP-3 calendar: whether the plan is being enforced (`athlete.plan_state`).

One column. It is added with a ``server_default`` — unlike every other column
on `athlete`, which has none — because the table already holds its one row and
"no answer yet" is not a state a plan can be in: an existing profile is on an
active plan. The default stays in place rather than being dropped afterwards,
so the column keeps its meaning for an INSERT that predates the application
knowing about it (and so this revision needs no `ALTER COLUMN`, which SQLite
cannot do outside a batch rebuild).

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Enums are non-native VARCHARs holding the member VALUE
#: (`app.persistence.types.enum_column`) with no CHECK constraint, so this
#: compiles to `VARCHAR(6)` — the length of `paused`/`active` — on either
#: dialect. Adding a member needs a migration only if its value is longer
#: than every existing one, and then a batch one, because it widens the
#: column (D81).
PLAN_STATE = sa.Enum("active", "paused", name="planstate", native_enum=False)


def upgrade() -> None:
    """Apply the migration."""
    op.add_column(
        "athlete",
        sa.Column(
            "plan_state",
            PLAN_STATE,
            nullable=False,
            server_default="active",
        ),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("athlete", "plan_state")

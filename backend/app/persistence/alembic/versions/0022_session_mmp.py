"""Each session's power curve: `session_mmp`.

One row per session holding its mean-maximal power at every grid duration
(`app.domain.power_curve`), replaced whenever the session's metrics are
computed. See `app.persistence.power_curve.SessionMmpRow` for why it is its own
table and not a versioned block on the metric artefact.

No backfill, deliberately: a migration cannot read parquet. A session recorded
before this revision has no row — and the API says the curve "has not been
computed yet" rather than inventing one — until `just rebuild-streams` (or a
recompute) runs the analyser over it.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import app.persistence.types

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: JSON on SQLite, JSONB on Postgres — the `JSONColumn` spelling from
#: `app.persistence.types`, written out because a migration must keep saying
#: what it meant on the day it ran.
JSON_COLUMN = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
)


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "session_mmp",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("grid_version", sa.Integer(), nullable=False),
        sa.Column("durations_s", JSON_COLUMN, nullable=False),
        sa.Column("watts_dw", JSON_COLUMN, nullable=False),
        sa.Column("start_indices", JSON_COLUMN, nullable=False),
        sa.Column("clean", JSON_COLUMN, nullable=False),
        sa.Column(
            "computed_at",
            app.persistence.types.UtcDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_session_mmp_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session_mmp")),
        sa.UniqueConstraint("session_id", name=op.f("uq_session_mmp_session_id")),
    )


def downgrade() -> None:
    """Revert the migration. The curves are derived; a rebuild recreates them."""
    op.drop_table("session_mmp")

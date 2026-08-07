"""WP-5 metrics: the versioned metric artefact, and resting HR as an anchor.

Two changes, and only one of them is a table.

**`session_metrics`** is this repository's second versioned artefact, modelled
on the first (`planned_session_intents`) down to the constraint name shape:
``(session_id, version)`` unique, ``as_of`` defaulted by the database,
``superseded_by`` deliberately **not** a foreign key — the old version and its
successor are written in one flush, and a self-referential FK would order them
for no benefit. Recomputation appends; nothing is ever updated in place
(invariant 1).

The four ``*_anchor_version_id`` columns are real foreign keys because anchor
history is append-only and nothing deletes a version, so the reference cannot
dangle. They are columns rather than JSON because "recompute everything that
used this FTP version" is the query the versioning doctrine exists to make
possible, and it cannot be a JSON scan. The metrics themselves are one JSON
payload: the set grows every work package and nothing queries an individual
metric.

**`anchor_versions.anchor_type` widens** from VARCHAR(7) to VARCHAR(10).
`AnchorType.RESTING_HR` (D114) is the first member longer than ``w_prime``,
and the enum columns this codebase uses are non-native VARCHARs sized to the
longest member value. Inside ``batch_alter_table`` because SQLite cannot alter
a column in place; on PostgreSQL the same call emits a plain ``ALTER COLUMN
... TYPE``. No data changes: every existing value is shorter than the new
bound.

Nothing is backfilled. Before this revision no metric artefact existed, so
there is no row for a new column to have the wrong value for; sessions already
ingested get their artefact from
``POST /api/v1/sessions/{id}/metrics/recompute``, which is the same path a
recomputation takes.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import app.persistence.types

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: JSON on SQLite, JSONB on Postgres — the `JSONColumn` spelling from
#: `app.persistence.types`, written out because a migration must keep saying
#: what it meant on the day it ran, even after the alias changes.
JSON_COLUMN = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
)

#: The zone model each channel's distribution was banded by (A5.5).
ZONE_MODEL = sa.Enum("coggan_7", "lthr_5", name="zonemodel", native_enum=False)

#: Widths of `anchor_versions.anchor_type` before and after `resting_hr`.
ANCHOR_TYPE_BEFORE = sa.String(length=7)
ANCHOR_TYPE_AFTER = sa.String(length=10)


def upgrade() -> None:
    """Apply the migration."""
    # `resting_hr` is ten characters; every previous member fitted in seven.
    with op.batch_alter_table("anchor_versions") as batch:
        batch.alter_column(
            "anchor_type",
            existing_type=ANCHOR_TYPE_BEFORE,
            type_=ANCHOR_TYPE_AFTER,
            existing_nullable=False,
        )

    op.create_table(
        "session_metrics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "as_of",
            app.persistence.types.UtcDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Not a foreign key; see the module docstring.
        sa.Column("superseded_by", sa.Uuid(), nullable=True),
        sa.Column("recompute_reason", sa.String(length=200), nullable=True),
        # The pins (D115, A5.5). All nullable: a session with no power pins no
        # FTP, and an athlete with no resting-HR anchor pins none.
        sa.Column("ftp_anchor_version_id", sa.Uuid(), nullable=True),
        sa.Column("lthr_anchor_version_id", sa.Uuid(), nullable=True),
        sa.Column("max_hr_anchor_version_id", sa.Uuid(), nullable=True),
        sa.Column("resting_hr_anchor_version_id", sa.Uuid(), nullable=True),
        sa.Column("power_zone_model", ZONE_MODEL, nullable=True),
        sa.Column("hr_zone_model", ZONE_MODEL, nullable=True),
        sa.Column("payload", JSON_COLUMN, nullable=False),
        sa.ForeignKeyConstraint(
            ["ftp_anchor_version_id"],
            ["anchor_versions.id"],
            name=op.f("fk_session_metrics_ftp_anchor_version_id_anchor_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["lthr_anchor_version_id"],
            ["anchor_versions.id"],
            name=op.f("fk_session_metrics_lthr_anchor_version_id_anchor_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["max_hr_anchor_version_id"],
            ["anchor_versions.id"],
            name=op.f("fk_session_metrics_max_hr_anchor_version_id_anchor_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["resting_hr_anchor_version_id"],
            ["anchor_versions.id"],
            name=op.f(
                "fk_session_metrics_resting_hr_anchor_version_id_anchor_versions"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_session_metrics_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session_metrics")),
        # One row per (session, version): the chain is how the no-overwrite
        # rule is enforced, so a duplicate version number corrupts it.
        sa.UniqueConstraint(
            "session_id", "version", name=op.f("uq_session_metrics_session_id")
        ),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_table("session_metrics")
    with op.batch_alter_table("anchor_versions") as batch:
        batch.alter_column(
            "anchor_type",
            existing_type=ANCHOR_TYPE_AFTER,
            type_=ANCHOR_TYPE_BEFORE,
            existing_nullable=False,
        )

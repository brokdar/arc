"""The daily wellness series (Increment 1): one row per athlete-local date.

One table. `wellness_days` holds the objective markers, the subjective
ratings, the confounder tags and the free note the athlete reports each
morning — one consolidated touchpoint, held to one row a day by a **unique
index on `local_date`** rather than by a code path that could forget it.

**Why `local_date` and `created_at` are both here and there is no `backfilled`
flag.** `local_date` is the day the reading describes and `created_at` is when
it was entered, so the lag is a subtraction — which is what makes backfilling a
file of historical readings expressible through the ordinary write path, and
what `app.domain.wellness.is_late_entry` reads. A stored boolean would be a
denormalization of that subtraction, and one that goes wrong the first time a
row is corrected.

**Why `hrv_ms` and not `hrv_rmssd_ms`.** RMSSD and SDNN are different
statistics over the same beat intervals and Apple HealthKit exposes only SDNN,
so Increment 2's ingest path could never fill a column whose name promises
RMSSD. The statistic is a stored discriminator (`hrv_metric`) beside the
measurement context (`hrv_context`), and a baseline is computed within one
(metric, context) pair — see `app.domain.wellness.HrvMetric`.

**Why no `wellness_prompts` table yet.** The evening-style daily prompt is the
next PR of this increment, and it arrives with the sweep that raises and
expires it. Creating its table here would ship a table nothing reads, which is
the "a PR that adds a column and stops" shape this increment's plan refuses.

Nothing is backfilled: no day before this ships has a wellness row, and null is
the honest value for "nobody said". Every value column is nullable and null
means *not provided*, never zero.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import app.persistence.types

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: JSON on SQLite, JSONB on Postgres — the `JSONColumn` spelling from
#: `app.persistence.types`, written out because a migration must keep saying
#: what it meant on the day it ran.
JSON_COLUMN = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
)

#: Non-native VARCHARs holding the member VALUE, no CHECK constraint — the
#: convention `app.persistence.types.enum_column` sets.
HRV_METRIC = sa.Enum("rmssd", "sdnn", name="hrvmetric", native_enum=False)
HRV_CONTEXT = sa.Enum(
    "sleeping", "waking_spot", "manual", name="hrvcontext", native_enum=False
)
WELLNESS_PROVENANCE = sa.Enum(
    "athlete_reported",
    "device_measured",
    name="wellnessprovenance",
    native_enum=False,
)
WELLNESS_SOURCE = sa.Enum(
    "athlete", "agent", name="wellnesssource", native_enum=False
)


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "wellness_days",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("sleep_duration_s", sa.Integer(), nullable=True),
        sa.Column("sleep_start_local", sa.Time(), nullable=True),
        sa.Column("sleep_end_local", sa.Time(), nullable=True),
        sa.Column("resting_hr_bpm", sa.Integer(), nullable=True),
        sa.Column("hrv_ms", sa.Float(), nullable=True),
        sa.Column("hrv_metric", HRV_METRIC, nullable=True),
        sa.Column("hrv_context", HRV_CONTEXT, nullable=True),
        sa.Column("respiratory_rate_brpm", sa.Float(), nullable=True),
        sa.Column("spo2", sa.Float(), nullable=True),
        sa.Column("wrist_temperature_delta_c", sa.Float(), nullable=True),
        sa.Column("weight_kg", sa.Float(), nullable=True),
        sa.Column("sleep_quality", sa.Integer(), nullable=True),
        sa.Column("fatigue", sa.Integer(), nullable=True),
        sa.Column("soreness", sa.Integer(), nullable=True),
        sa.Column("stress", sa.Integer(), nullable=True),
        sa.Column("motivation", sa.Integer(), nullable=True),
        sa.Column("soreness_by_region", JSON_COLUMN, nullable=False),
        sa.Column("confounders", JSON_COLUMN, nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("provenance", WELLNESS_PROVENANCE, nullable=False),
        sa.Column("source", WELLNESS_SOURCE, nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wellness_days")),
    )
    # Unique *and* indexed in one object: the constraint is the one-row-per-day
    # promise and the index is what every range read scans.
    op.create_index(
        op.f("ix_wellness_days_local_date"),
        "wellness_days",
        ["local_date"],
        unique=True,
    )


def downgrade() -> None:
    """Revert the migration.

    The whole series goes. There is nowhere else the athlete's mornings are
    stored, so a downgrade is a decision to discard them — stated here rather
    than softened, because the only honest alternative would be refusing to
    downgrade at all.
    """
    op.drop_index(op.f("ix_wellness_days_local_date"), table_name="wellness_days")
    op.drop_table("wellness_days")

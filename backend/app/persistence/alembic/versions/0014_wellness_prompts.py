"""The daily wellness prompt (Increment 1): one question a day, and it expires.

One table, `wellness_prompts`, mirroring `evening_prompts`: a dated row, a
stored deadline, a nullable `resolved_at` and a three-member status. It arrives
here rather than in `0013` because `0013` refused to ship a table nothing read
— the sweep that raises and closes these rows is this revision's PR.

**The unique index on `local_date` is the feature, not a detail.** "One prompt
a day, ever" is held by the database, because the sweep that raises them runs
hourly and is *expected* to fire over the same date many times. A pre-check in
the service would be an optimisation on top of this; on its own it would be a
promise that a race, a redeploy or a second process could break silently, and
the athlete would see a column of unanswered questions.

Nothing is backfilled: no day before this ships was ever asked about, and no
row is the honest record of that. Expiry closes a day into "not provided" and
raises no follow-up — see `app.services.wellness.WellnessService.expire_prompts`.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.persistence.types

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: A non-native VARCHAR holding the member VALUE, no CHECK constraint — the
#: convention `app.persistence.types.enum_column` sets.
PROMPT_STATUS = sa.Enum(
    "pending", "answered", "expired", name="wellnesspromptstatus", native_enum=False
)


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "wellness_prompts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("status", PROMPT_STATUS, nullable=False),
        sa.Column(
            "expires_at",
            app.persistence.types.UtcDateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "resolved_at",
            app.persistence.types.UtcDateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            app.persistence.types.UtcDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wellness_prompts")),
        # One prompt per date. Postgres backs a unique constraint with a unique
        # index, so this is also what "is a question standing today" scans on
        # every read of the Today view and every coaching context.
        sa.UniqueConstraint("local_date", name=op.f("uq_wellness_prompts_local_date")),
    )
    # The sweep's own query: pending prompts whose deadline has passed.
    op.create_index(
        op.f("ix_wellness_prompts_status"),
        "wellness_prompts",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    """Revert the migration.

    Every prompt goes, which discards the record of which days were asked about
    and went unanswered. The days themselves (`wellness_days`) are untouched:
    what is lost is the difference between "the athlete reported nothing" and
    "nobody asked", which is the distinction this table exists to keep.
    """
    op.drop_index(op.f("ix_wellness_prompts_status"), table_name="wellness_prompts")
    op.drop_table("wellness_prompts")

"""Plans with phase and follow-exactly; key sessions with a time of day.

**`plans`** is new: the container planned sessions belong to — name, phase,
follow_exactly, active, a half-open `[starts_on, ends_before)` span, and
`is_default` (TRUE on one row, NULL on the rest, unique; see
`app.persistence.plans`). **`plan_week_tags`** holds tags (`recovery`) per plan
and week.

**`planned_sessions`** gains `plan_id` (required), `key` (false) and
`time_of_day` (nullable).

**Every existing session moves into one default plan**, created here with
phase `base` and follow_exactly false, undated and active — whether or not any
session exists, so a fresh database has a plan for its first session to join.
That displaces leaving existing sessions plan-less: a nullable `plan_id` would
make every reader that asks "which plan, which phase" handle a session with no
answer, forever, for rows that only predate the question. `base` and
not-follow-exactly are the values under which nothing that later reads them
(progression, swaps) behaves differently from today, so the move changes no
behaviour. Pinned by `tests/integration/test_plans_migration.py` (AC-38).

`plan_id` is added nullable, filled, then made NOT NULL — the one order that
works on a table with rows in it.

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-11
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.persistence.types

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Non-native VARCHARs holding the member value — `enum_column`.
PLAN_PHASE = sa.Enum(
    "base",
    "build",
    "peak",
    "taper",
    "maintenance",
    "transition",
    name="planphase",
    native_enum=False,
)
PLAN_WEEK_TAG = sa.Enum("recovery", name="planweektag", native_enum=False)

UTC = app.persistence.types.UtcDateTime(timezone=True)

#: The default plan's name, written out rather than imported: a migration must
#: keep saying what it did on the day it ran.
DEFAULT_PLAN_NAME = "My plan"


def upgrade() -> None:
    """Apply the migration."""
    plans = op.create_table(
        "plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("phase", PLAN_PHASE, nullable=False),
        sa.Column("follow_exactly", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=True),
        sa.Column("starts_on", sa.Date(), nullable=True),
        sa.Column("ends_before", sa.Date(), nullable=True),
        sa.Column("created_at", UTC, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", UTC, server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plans")),
        sa.UniqueConstraint("is_default", name=op.f("uq_plans_is_default")),
    )
    op.create_table(
        "plan_week_tags",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("tag", PLAN_WEEK_TAG, nullable=False),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["plans.id"],
            name=op.f("fk_plan_week_tags_plan_id_plans"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plan_week_tags")),
        sa.UniqueConstraint(
            "plan_id", "week_start", "tag", name=op.f("uq_plan_week_tags_plan_id")
        ),
    )

    default_id = uuid.uuid7()
    op.bulk_insert(
        plans,
        [
            {
                "id": default_id,
                "name": DEFAULT_PLAN_NAME,
                "phase": "base",
                "follow_exactly": False,
                "active": True,
                "is_default": True,
                "starts_on": None,
                "ends_before": None,
            }
        ],
    )

    with op.batch_alter_table("planned_sessions") as batch:
        batch.add_column(sa.Column("plan_id", sa.Uuid(), nullable=True))
        batch.add_column(
            sa.Column("key", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("time_of_day", sa.Time(), nullable=True))

    op.execute(
        sa.text("UPDATE planned_sessions SET plan_id = :plan_id").bindparams(
            sa.bindparam("plan_id", value=default_id, type_=sa.Uuid())
        )
    )

    with op.batch_alter_table("planned_sessions") as batch:
        batch.alter_column("plan_id", existing_type=sa.Uuid(), nullable=False)
        batch.create_foreign_key(
            op.f("fk_planned_sessions_plan_id_plans"),
            "plans",
            ["plan_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index(op.f("ix_planned_sessions_plan_id"), ["plan_id"])


def downgrade() -> None:
    """Revert the migration. Plans, week tags, key flags and times are lost."""
    with op.batch_alter_table("planned_sessions") as batch:
        batch.drop_index(op.f("ix_planned_sessions_plan_id"))
        batch.drop_constraint(
            op.f("fk_planned_sessions_plan_id_plans"), type_="foreignkey"
        )
        batch.drop_column("time_of_day")
        batch.drop_column("key")
        batch.drop_column("plan_id")
    op.drop_table("plan_week_tags")
    op.drop_table("plans")

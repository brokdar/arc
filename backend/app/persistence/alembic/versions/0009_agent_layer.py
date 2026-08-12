"""WP-8 agent layer: the athlete's red flag, and plan-change proposals.

Three columns and one table.

**`athlete.red_flag_*`** is the illness/injury state (WP-8.4). `red_flag_active`
carries a ``server_default`` for the reason `plan_state` did in 0004: the table
already holds its one row, and "not answered yet" is not a state this can be
in — an existing profile is not flagged. The default stays rather than being
dropped afterwards, so the column keeps its meaning for an INSERT that predates
the application knowing about it, and this revision needs no ``ALTER COLUMN``
(which SQLite cannot do outside a batch rebuild). The other two are nullable
and need no default: they are non-null exactly while the flag is up, which the
domain enforces, not the database — a CHECK spanning three columns would have
to be dropped and rebuilt on SQLite for every later change to the table.

**`plan_proposals`** is the proposal record (WP-8.2). It is named in full
because WP-6 already owns "proposal" for a *match* proposal (`session_matches`
with status `pending`), and two things called the same in one schema is a bug
waiting for whoever reads it next.

No foreign keys at all on this table, and both omissions are deliberate:

* `supersedes_id` / `superseded_by_id` point at other rows of this same table
  and are written in a single flush, so a self-referential FK would order them
  for no benefit — the same reasoning 0006 and 0008 record for
  ``superseded_by``;
* the planned sessions a proposal is *about* live inside the ``changes`` and
  ``diff`` documents rather than in columns. A proposal must survive the plan
  entry it discussed — an accepted proposal that deleted a session would
  otherwise cascade itself away, taking the record of who suggested it — and
  the diff is a snapshot of what the plan looked like when the suggestion was
  made, which stops being true the moment a foreign key keeps it current.

Nothing is backfilled: there were no proposals before this revision, and no
profile was flagged.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import app.persistence.types

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: JSON on SQLite, JSONB on Postgres — the `JSONColumn` spelling from
#: `app.persistence.types`, written out because a migration must keep saying
#: what it meant on the day it ran.
JSON_COLUMN = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
)

#: Non-native VARCHARs holding the member VALUE, no CHECK constraint — the
#: convention `app.persistence.types.enum_column` sets. Each compiles to
#: `VARCHAR(n)` where n is the longest member value, on either dialect.
RED_FLAG_SEVERITY = sa.Enum(
    "mild", "moderate", "severe", name="redflagseverity", native_enum=False
)
PROPOSAL_STATUS = sa.Enum(
    "pending",
    "accepted",
    "rejected",
    "lapsed",
    "superseded",
    "resolved_by_reality",
    name="proposalstatus",
    native_enum=False,
)


def upgrade() -> None:
    """Apply the migration."""
    op.add_column(
        "athlete",
        sa.Column(
            "red_flag_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column("athlete", sa.Column("red_flag_note", sa.String(length=1000)))
    op.add_column("athlete", sa.Column("red_flag_severity", RED_FLAG_SEVERITY))

    op.create_table(
        "plan_proposals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("status", PROPOSAL_STATUS, nullable=False),
        sa.Column("rationale", sa.String(length=4000), nullable=False),
        sa.Column("changes", JSON_COLUMN, nullable=False),
        sa.Column("diff", JSON_COLUMN, nullable=False),
        sa.Column(
            "expires_at",
            app.persistence.types.UtcDateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=120), nullable=False),
        sa.Column(
            "created_at",
            app.persistence.types.UtcDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "resolved_at",
            app.persistence.types.UtcDateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("resolution_note", sa.String(length=1000), nullable=True),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("superseded_by_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plan_proposals")),
    )
    op.create_index(
        op.f("ix_plan_proposals_created_at"),
        "plan_proposals",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_plan_proposals_created_by"),
        "plan_proposals",
        ["created_by"],
        unique=False,
    )
    op.create_index(
        op.f("ix_plan_proposals_expires_at"),
        "plan_proposals",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_plan_proposals_status"), "plan_proposals", ["status"], unique=False
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(op.f("ix_plan_proposals_status"), table_name="plan_proposals")
    op.drop_index(op.f("ix_plan_proposals_expires_at"), table_name="plan_proposals")
    op.drop_index(op.f("ix_plan_proposals_created_by"), table_name="plan_proposals")
    op.drop_index(op.f("ix_plan_proposals_created_at"), table_name="plan_proposals")
    op.drop_table("plan_proposals")
    op.drop_column("athlete", "red_flag_severity")
    op.drop_column("athlete", "red_flag_note")
    op.drop_column("athlete", "red_flag_active")

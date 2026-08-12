"""WP-8 agent notes: the interpretive record, stored apart from the computed one.

One table. Invariant 7 says agent-written text is attributed, distinguishable
and stored separately from computed findings, and "separately" is this file:
`sessions` and `session_scores` hold what was measured and derived, and
`agent_notes` holds what a model said about them.

**The target is exactly one of two**, and a CHECK says so rather than a
convention. A nullable pair with no constraint is a shape every reader has to
interpret, and a row with both set would belong to a session *and* a week with
no rule for which read wins. `ck_agent_notes_one_target` is a one-column-pair
predicate that later revisions of this table will not need to rebuild, unlike
the three-column red-flag rule 0009 deliberately left to the domain.

**`session_id` is a foreign key and `plan_week` is not.** A note about a
session is commentary on a row, and cascades with it. A plan week is a date
range the calendar defines (`app.domain.plan` — Monday to Sunday); there is no
row to point at, which is also why the service refuses a date that is not a
Monday rather than storing two keys for one week.

**`cites` is JSON, not a join table.** The artefacts a note rests on live in
several tables — a session, a planned session, an anchor version, a score —
so a foreign key could only reach one of them, and a note that outlives a
cited row is still a true record of what was said. The ids are validated as
uuids on the way in and resolved by nobody.

Nothing is backfilled: no note existed before this revision.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import app.persistence.types

revision: str = "0010"
down_revision: str | None = "0009"
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
NOTE_KIND = sa.Enum("evaluation", "annotation", name="notekind", native_enum=False)
DISPUTE_RATING = sa.Enum("up", "down", name="disputerating", native_enum=False)


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "agent_notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("plan_week", sa.Date(), nullable=True),
        sa.Column("kind", NOTE_KIND, nullable=False),
        sa.Column("text", sa.String(length=8000), nullable=False),
        sa.Column("model_id", sa.String(length=120), nullable=False),
        sa.Column("created_by", sa.String(length=120), nullable=False),
        sa.Column(
            "created_at",
            app.persistence.types.UtcDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("cites", JSON_COLUMN, nullable=False),
        sa.Column("dispute", DISPUTE_RATING, nullable=True),
        sa.Column(
            "disputed_at",
            app.persistence.types.UtcDateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "(session_id IS NULL) <> (plan_week IS NULL)",
            name=op.f("ck_agent_notes_one_target"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_agent_notes_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_notes")),
    )
    op.create_index(
        op.f("ix_agent_notes_created_at"), "agent_notes", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_agent_notes_created_by"), "agent_notes", ["created_by"], unique=False
    )
    op.create_index(op.f("ix_agent_notes_kind"), "agent_notes", ["kind"], unique=False)
    op.create_index(
        op.f("ix_agent_notes_model_id"), "agent_notes", ["model_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_notes_plan_week"), "agent_notes", ["plan_week"], unique=False
    )
    op.create_index(
        op.f("ix_agent_notes_session_id"), "agent_notes", ["session_id"], unique=False
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(op.f("ix_agent_notes_session_id"), table_name="agent_notes")
    op.drop_index(op.f("ix_agent_notes_plan_week"), table_name="agent_notes")
    op.drop_index(op.f("ix_agent_notes_model_id"), table_name="agent_notes")
    op.drop_index(op.f("ix_agent_notes_kind"), table_name="agent_notes")
    op.drop_index(op.f("ix_agent_notes_created_by"), table_name="agent_notes")
    op.drop_index(op.f("ix_agent_notes_created_at"), table_name="agent_notes")
    op.drop_table("agent_notes")

"""WP-6 matching: the link table between plan and reality, and the missed prompt.

Two tables and no column changes.

**`session_matches`** is the link table build-plan WP-6.5 asks for rather than
a foreign key on `sessions`. The MVP's one-to-one restriction lives in two
unique constraints — one per side — which a later increment drops when
set-to-set matching arrives, instead of a column a later increment would have
to migrate. `previous_session_status` and `previous_planned_status` are what
make unlinking exact (WP-6.8): they hold the two statuses the link displaced,
so removing it restores those rather than a guess at the defaults.

**`evening_prompts`** is WP-6.7's record, written when a planned session goes
past its grace with nothing linked to it. Unique on `planned_session_id`,
because the sweep is idempotent and runs hourly over the same backlog.

**Nothing widens an existing enum column.** `sessions.status` gains three
members here (`matched`, `unplanned`, `displaced`) and `planned_sessions.status`
gains none — its `displaced` member has been in the vocabulary since 0002. Both
columns are VARCHAR(9) and every new value is nine characters or fewer, which
is exactly the property the non-native enum reserved them for, so this revision has no
``ALTER COLUMN`` in it.

Nothing is backfilled either. Every session ingested before this revision is
`unmatched`, which is still true of it: matching runs on ingest, and an older
session gets its link from `POST /api/v1/sessions/{id}/rematch`.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import app.persistence.types

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: JSON on SQLite, JSONB on Postgres — the `JSONColumn` spelling from
#: `app.persistence.types`, written out because a migration must keep saying
#: what it meant on the day it ran.
JSON_COLUMN = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
)

#: The enum vocabularies this revision introduces, non-native as everywhere
#: else in this schema (see `app.persistence.types.enum_column`): a VARCHAR
#: sized to the longest member, with no CHECK constraint.
MATCH_LINK_STATUS = sa.Enum(
    "auto_high",
    "pending",
    "confirmed",
    "displaced",
    name="matchlinkstatus",
    native_enum=False,
)
SESSION_MATCH_STATUS = sa.Enum(
    "unmatched",
    "matched",
    "unplanned",
    "displaced",
    name="sessionmatchstatus",
    native_enum=False,
)
SESSION_STATUS = sa.Enum(
    "planned",
    "completed",
    "missed",
    "displaced",
    name="sessionstatus",
    native_enum=False,
)
PROMPT_KIND = sa.Enum(
    "missed_session", name="eveningpromptkind", native_enum=False
)
PROMPT_STATUS = sa.Enum(
    "pending", "answered", "expired", name="eveningpromptstatus", native_enum=False
)


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "session_matches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("planned_session_id", sa.Uuid(), nullable=False),
        sa.Column("status", MATCH_LINK_STATUS, nullable=False),
        # Nullable, and null is not zero: it means no component of the
        # comparison could be assessed at all.
        sa.Column("similarity", sa.Float(), nullable=True),
        sa.Column("breakdown", JSON_COLUMN, nullable=False),
        sa.Column("created_by", sa.String(length=120), nullable=False),
        # What unlinking restores (WP-6.8).
        sa.Column("previous_session_status", SESSION_MATCH_STATUS, nullable=False),
        sa.Column("previous_planned_status", SESSION_STATUS, nullable=False),
        sa.Column(
            "confirmed_at",
            app.persistence.types.UtcDateTime(timezone=True),
            nullable=True,
        ),
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
        sa.ForeignKeyConstraint(
            ["planned_session_id"],
            ["planned_sessions.id"],
            name=op.f("fk_session_matches_planned_session_id_planned_sessions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_session_matches_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session_matches")),
        # The MVP's one-to-one restriction, in the database rather than only in
        # the service: the "is either side already linked" check is a read that
        # can always lose a race, and a session with two links is a state
        # nothing downstream knows how to render.
        sa.UniqueConstraint(
            "planned_session_id", name=op.f("uq_session_matches_planned_session_id")
        ),
        sa.UniqueConstraint("session_id", name=op.f("uq_session_matches_session_id")),
    )
    op.create_index(
        op.f("ix_session_matches_status"), "session_matches", ["status"], unique=False
    )

    op.create_table(
        "evening_prompts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("planned_session_id", sa.Uuid(), nullable=False),
        sa.Column("kind", PROMPT_KIND, nullable=False),
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
        sa.ForeignKeyConstraint(
            ["planned_session_id"],
            ["planned_sessions.id"],
            name=op.f("fk_evening_prompts_planned_session_id_planned_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evening_prompts")),
        # One open prompt per planned session: the sweep is idempotent, and
        # without this a session left unmatched for a week would collect one a
        # day.
        sa.UniqueConstraint(
            "planned_session_id", name=op.f("uq_evening_prompts_planned_session_id")
        ),
    )
    op.create_index(
        op.f("ix_evening_prompts_status"), "evening_prompts", ["status"], unique=False
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(op.f("ix_evening_prompts_status"), table_name="evening_prompts")
    op.drop_table("evening_prompts")
    op.drop_index(op.f("ix_session_matches_status"), table_name="session_matches")
    op.drop_table("session_matches")

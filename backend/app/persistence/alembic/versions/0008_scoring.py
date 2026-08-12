"""WP-7 scoring: the score and alignment chains, and the athlete's testimony.

Four tables and no column changes.

**`session_alignments` and `session_scores`** are versioned derived artefacts
(invariant 1), shaped exactly like `session_metrics`: ``version`` / ``as_of`` /
``superseded_by`` / ``recompute_reason`` and one ``UniqueConstraint(session_id,
version)`` per chain. ``superseded_by`` is deliberately **not** a foreign key —
the old version and its successor are written in one flush, and a
self-referential FK would order them for no benefit, the same reasoning 0006
records.

**`verdict_declarations` and `session_reasons`** are the athlete's words, and
they are separate tables from the score for one reason: a rescore appends a new
score version, and testimony that lived on that chain would be rewritten every
time the machine changed its mind. `contested` lives with the declaration
because it is a fact *about* the declaration (WP-7.4).

`session_reasons` carries a **check constraint** rather than two tables: reasons
about a completed session hang off its declaration, reasons about a missed one
hang off the planned session (there is no declaration to hang from), and
``(declaration_id IS NULL) <> (planned_session_id IS NULL)`` is the whole
difference. Portable as written — SQLite and Postgres both take it — so no
batch mode is needed here; this revision only creates tables.

Its version chain is closed by **two** unique constraints rather than one,
because it has two possible subjects and the version is numbered within
whichever one a row names. The check constraint leaves exactly one of the two
columns non-null and NULLs are distinct in a unique index on both dialects, so
each constraint binds only its own half of the table — and two concurrent
revisions can no longer both land version *n+1*.

**Every reference to a planned session is `ON DELETE SET NULL`**, not CASCADE,
except the reasons' own subject. Deleting a plan entry must not destroy the
record of what was measured against it; the artefact stays readable and simply
stops naming a plan. The reasons row is the exception because it *is* about
that plan entry and means nothing without it.

Nothing is backfilled. Every session matched before this revision is unscored,
which is true of it: scoring runs when a link settles, and an older session
gets its first score from `POST /api/v1/sessions/{id}/score/recompute`.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import app.persistence.types

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "session_alignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("planned_session_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "as_of",
            app.persistence.types.UtcDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("superseded_by", sa.Uuid(), nullable=True),
        sa.Column("recompute_reason", sa.String(length=200), nullable=True),
        sa.Column("offset_s", sa.Integer(), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=120), nullable=False),
        sa.ForeignKeyConstraint(
            ["planned_session_id"],
            ["planned_sessions.id"],
            name=op.f("fk_session_alignments_planned_session_id_planned_sessions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_session_alignments_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session_alignments")),
        sa.UniqueConstraint(
            "session_id", "version", name=op.f("uq_session_alignments_session_id")
        ),
    )
    op.create_index(
        op.f("ix_session_alignments_planned_session_id"),
        "session_alignments",
        ["planned_session_id"],
        unique=False,
    )
    op.create_table(
        "session_scores",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("planned_session_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "as_of",
            app.persistence.types.UtcDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("superseded_by", sa.Uuid(), nullable=True),
        sa.Column("recompute_reason", sa.String(length=200), nullable=True),
        sa.Column("intent_version", sa.Integer(), nullable=True),
        sa.Column(
            "pinned_anchor_versions",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column("metrics_version_id", sa.Uuid(), nullable=True),
        sa.Column("alignment_version_id", sa.Uuid(), nullable=True),
        sa.Column(
            "suggested_verdict",
            sa.Enum(
                "as_intended",
                "under",
                "over",
                "abandoned",
                "different_session",
                name="verdict",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "payload",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=120), nullable=False),
        sa.ForeignKeyConstraint(
            ["alignment_version_id"],
            ["session_alignments.id"],
            name=op.f("fk_session_scores_alignment_version_id_session_alignments"),
        ),
        sa.ForeignKeyConstraint(
            ["metrics_version_id"],
            ["session_metrics.id"],
            name=op.f("fk_session_scores_metrics_version_id_session_metrics"),
        ),
        sa.ForeignKeyConstraint(
            ["planned_session_id"],
            ["planned_sessions.id"],
            name=op.f("fk_session_scores_planned_session_id_planned_sessions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_session_scores_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session_scores")),
        sa.UniqueConstraint(
            "session_id", "version", name=op.f("uq_session_scores_session_id")
        ),
    )
    op.create_index(
        op.f("ix_session_scores_planned_session_id"),
        "session_scores",
        ["planned_session_id"],
        unique=False,
    )
    op.create_table(
        "verdict_declarations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("planned_session_id", sa.Uuid(), nullable=True),
        sa.Column(
            "declared_verdict",
            sa.Enum(
                "as_intended",
                "under",
                "over",
                "abandoned",
                "different_session",
                name="verdict",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "declared_at",
            app.persistence.types.UtcDateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "suggested_at_declaration",
            sa.Enum(
                "as_intended",
                "under",
                "over",
                "abandoned",
                "different_session",
                name="verdict",
                native_enum=False,
            ),
            nullable=True,
        ),
        sa.Column("score_version_id", sa.Uuid(), nullable=True),
        sa.Column("contested", sa.Boolean(), nullable=False),
        sa.Column(
            "contested_at",
            app.persistence.types.UtcDateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "contested_verdict",
            sa.Enum(
                "as_intended",
                "under",
                "over",
                "abandoned",
                "different_session",
                name="verdict",
                native_enum=False,
            ),
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
            name=op.f("fk_verdict_declarations_planned_session_id_planned_sessions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["score_version_id"],
            ["session_scores.id"],
            name=op.f("fk_verdict_declarations_score_version_id_session_scores"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_verdict_declarations_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verdict_declarations")),
        sa.UniqueConstraint(
            "session_id", name=op.f("uq_verdict_declarations_session_id")
        ),
    )
    op.create_index(
        op.f("ix_verdict_declarations_contested"),
        "verdict_declarations",
        ["contested"],
        unique=False,
    )
    op.create_index(
        op.f("ix_verdict_declarations_planned_session_id"),
        "verdict_declarations",
        ["planned_session_id"],
        unique=False,
    )
    op.create_table(
        "session_reasons",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("declaration_id", sa.Uuid(), nullable=True),
        sa.Column("planned_session_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "as_of",
            app.persistence.types.UtcDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("superseded_by", sa.Uuid(), nullable=True),
        sa.Column("recompute_reason", sa.String(length=200), nullable=True),
        sa.Column(
            "reasons",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("recorded_by", sa.String(length=120), nullable=False),
        sa.CheckConstraint(
            "(declaration_id IS NULL) <> (planned_session_id IS NULL)",
            name=op.f("ck_session_reasons_one_subject"),
        ),
        sa.ForeignKeyConstraint(
            ["declaration_id"],
            ["verdict_declarations.id"],
            name=op.f("fk_session_reasons_declaration_id_verdict_declarations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["planned_session_id"],
            ["planned_sessions.id"],
            name=op.f("fk_session_reasons_planned_session_id_planned_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session_reasons")),
        sa.UniqueConstraint(
            "declaration_id", "version", name=op.f("uq_session_reasons_declaration_id")
        ),
        sa.UniqueConstraint(
            "planned_session_id",
            "version",
            name=op.f("uq_session_reasons_planned_session_id"),
        ),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_table("session_reasons")
    op.drop_index(
        op.f("ix_verdict_declarations_planned_session_id"),
        table_name="verdict_declarations",
    )
    op.drop_index(
        op.f("ix_verdict_declarations_contested"), table_name="verdict_declarations"
    )
    op.drop_table("verdict_declarations")
    op.drop_index(
        op.f("ix_session_scores_planned_session_id"), table_name="session_scores"
    )
    op.drop_table("session_scores")
    op.drop_index(
        op.f("ix_session_alignments_planned_session_id"),
        table_name="session_alignments",
    )
    op.drop_table("session_alignments")

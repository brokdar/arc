"""WP-2 workout model: exercise catalogue, workout library, planned sessions.

Five tables, in dependency order. Nothing is seeded here — the exercise
catalogue is bundled data that `app.services.exercises` seeds lazily and
idempotently on first access, so a truncating test fixture or a restore from
dump cannot leave the application with prescriptions that reference nothing
(the same reasoning that keeps the athlete row out of `0002`).

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import app.persistence.types

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: JSON on SQLite, JSONB on Postgres — the `JSONColumn` spelling from
#: `app.persistence.types`, written out because a migration must keep saying
#: what it meant on the day it ran, even after the alias changes.
JSON_COLUMN = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
)

#: Enums are non-native VARCHARs holding the member VALUE
#: (`app.persistence.types.enum_column`), so adding a member later is an
#: ordinary check-constraint migration on either dialect.
DISCIPLINE = sa.Enum("cycling", "strength", name="discipline", native_enum=False)


def upgrade() -> None:
    """Apply the migration."""
    # The exercise catalogue. Primary key is the slug, not a uuid: a
    # prescription references an exercise from inside a JSON structure, where
    # a foreign key cannot reach, so the identifier has to be stable and
    # readable across every deployment.
    op.create_table(
        "exercises",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "squat",
                "hinge",
                "lunge",
                "press",
                "pull",
                "core",
                "carry",
                "mobility",
                "conditioning",
                name="exercisecategory",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("unilateral", sa.Boolean(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exercises")),
    )
    op.create_index(
        op.f("ix_exercises_category"), "exercises", ["category"], unique=False
    )
    op.create_index(op.f("ix_exercises_name"), "exercises", ["name"], unique=False)

    # The workout library. The prescription is one JSON document: a step tree
    # is recursive, and shredding it into rows would be a join per nesting
    # level to read back something only ever read whole.
    op.create_table(
        "workouts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("discipline", DISCIPLINE, nullable=False),
        sa.Column("structure", JSON_COLUMN, nullable=False),
        sa.Column("folder", sa.String(length=200), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workouts")),
    )
    op.create_index(
        op.f("ix_workouts_discipline"), "workouts", ["discipline"], unique=False
    )
    op.create_index(op.f("ix_workouts_folder"), "workouts", ["folder"], unique=False)
    op.create_index(op.f("ix_workouts_name"), "workouts", ["name"], unique=False)

    # Tags get a table rather than a JSON array: "which workouts are tagged X"
    # is a query, and array containment is spelled differently on SQLite and
    # Postgres.
    op.create_table(
        "workout_tags",
        sa.Column("workout_id", sa.Uuid(), nullable=False),
        sa.Column("tag", sa.String(length=60), nullable=False),
        sa.ForeignKeyConstraint(
            ["workout_id"],
            ["workouts.id"],
            name=op.f("fk_workout_tags_workout_id_workouts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workout_id", "tag", name=op.f("pk_workout_tags")),
    )
    op.create_index(op.f("ix_workout_tags_tag"), "workout_tags", ["tag"], unique=False)

    # The calendar entry: an identity and a status, mutable in the ordinary
    # way. What the session is *for* lives in the intent versions below.
    op.create_table(
        "planned_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("discipline", DISCIPLINE, nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "planned",
                "completed",
                "missed",
                "displaced",
                name="sessionstatus",
                native_enum=False,
            ),
            nullable=False,
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_planned_sessions")),
    )
    op.create_index(
        op.f("ix_planned_sessions_date"), "planned_sessions", ["date"], unique=False
    )
    op.create_index(
        op.f("ix_planned_sessions_discipline"),
        "planned_sessions",
        ["discipline"],
        unique=False,
    )
    op.create_index(
        op.f("ix_planned_sessions_status"), "planned_sessions", ["status"], unique=False
    )

    # Append-only intent versions (build-plan invariant 4). Carries WP-1's
    # versioning vocabulary verbatim; `superseded_by` is deliberately not a
    # foreign key, since the two rows of a revision are written in one flush.
    op.create_table(
        "planned_session_intents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("planned_session_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "as_of",
            app.persistence.types.UtcDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("superseded_by", sa.Uuid(), nullable=True),
        sa.Column("recompute_reason", sa.String(length=200), nullable=True),
        sa.Column("edited_post_hoc", sa.Boolean(), nullable=False),
        sa.Column(
            "purpose",
            sa.Enum(
                "recovery",
                "endurance",
                "tempo",
                "sweet_spot",
                "threshold",
                "vo2max",
                "anaerobic",
                "neuromuscular",
                "unstructured",
                "technique",
                "test",
                "max_strength",
                "strength_endurance",
                "hypertrophy",
                "power",
                "core",
                "mobility",
                "conditioning",
                name="purpose",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("intent_text", sa.String(length=4000), nullable=True),
        sa.Column("coach_notes", sa.String(length=4000), nullable=True),
        sa.Column("success_criteria", JSON_COLUMN, nullable=False),
        sa.Column("pinned_anchor_versions", JSON_COLUMN, nullable=False),
        sa.Column("workout_id", sa.Uuid(), nullable=True),
        sa.Column("structure", JSON_COLUMN, nullable=False),
        sa.ForeignKeyConstraint(
            ["planned_session_id"],
            ["planned_sessions.id"],
            name=op.f("fk_planned_session_intents_planned_session_id_planned_sessions"),
            ondelete="CASCADE",
        ),
        # SET NULL, not CASCADE: deleting a library workout must not delete
        # the history of what was prescribed from it. The frozen snapshot in
        # `structure` stands on its own; only the provenance link goes.
        sa.ForeignKeyConstraint(
            ["workout_id"],
            ["workouts.id"],
            name=op.f("fk_planned_session_intents_workout_id_workouts"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_planned_session_intents")),
        sa.UniqueConstraint(
            "planned_session_id",
            "version",
            name=op.f("uq_planned_session_intents_planned_session_id"),
        ),
    )
    op.create_index(
        op.f("ix_planned_session_intents_planned_session_id"),
        "planned_session_intents",
        ["planned_session_id"],
        unique=False,
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(
        op.f("ix_planned_session_intents_planned_session_id"),
        table_name="planned_session_intents",
    )
    op.drop_table("planned_session_intents")
    op.drop_index(
        op.f("ix_planned_sessions_status"), table_name="planned_sessions"
    )
    op.drop_index(
        op.f("ix_planned_sessions_discipline"), table_name="planned_sessions"
    )
    op.drop_index(op.f("ix_planned_sessions_date"), table_name="planned_sessions")
    op.drop_table("planned_sessions")
    op.drop_index(op.f("ix_workout_tags_tag"), table_name="workout_tags")
    op.drop_table("workout_tags")
    op.drop_index(op.f("ix_workouts_name"), table_name="workouts")
    op.drop_index(op.f("ix_workouts_folder"), table_name="workouts")
    op.drop_index(op.f("ix_workouts_discipline"), table_name="workouts")
    op.drop_table("workouts")
    op.drop_index(op.f("ix_exercises_name"), table_name="exercises")
    op.drop_index(op.f("ix_exercises_category"), table_name="exercises")
    op.drop_table("exercises")

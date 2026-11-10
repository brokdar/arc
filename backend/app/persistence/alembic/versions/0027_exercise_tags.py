"""The exercise library knows muscles, equipment and restrictions; notes.

**`exercises`** gains the library tags: `muscles` (`{region: weight}`),
`mechanic`, `pattern`, `equipment`, `load_type` and `restrictions`. Existing
rows get empty JSON from the server defaults and NULL scalar tags; there is no
backfill here, because the tags live in the bundled catalogue file and the
next `ensure_seeded` — run on the first access after the upgrade — diffs every
row against it and fills them in. A migration copying the file's values would
freeze them on the day it ran.

**`exercise_notes`** is new: the athlete's one note per exercise, keyed by the
exercise slug.

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import app.persistence.types

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: JSON on SQLite, JSONB on Postgres — the `JSONColumn` spelling, written out
#: because a migration must keep saying what it meant on the day it ran.
JSON_COLUMN = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
)

#: Non-native VARCHARs holding the member value, no CHECK — `enum_column`.
MECHANIC = sa.Enum("multi_joint", "single_joint", name="mechanic", native_enum=False)
PATTERN = sa.Enum(
    "squat",
    "hinge",
    "lunge",
    "horizontal_push",
    "vertical_push",
    "horizontal_pull",
    "vertical_pull",
    "chest_fly",
    "shoulder_raise",
    "rear_delt",
    "shrug",
    "elbow_flexion",
    "elbow_extension",
    "knee_flexion",
    "knee_extension",
    "calf_raise",
    "anti_extension",
    "anti_rotation",
    "anti_lateral_flexion",
    "trunk_flexion",
    "rotation",
    "carry",
    "jump",
    "mobility",
    "conditioning",
    name="movementpattern",
    native_enum=False,
)
LOAD_TYPE = sa.Enum(
    "barbell",
    "dumbbell",
    "stack",
    "kettlebell",
    "plate_loaded",
    "band",
    "bodyweight",
    "unloaded",
    name="loadtype",
    native_enum=False,
)

UTC = app.persistence.types.UtcDateTime(timezone=True)


def upgrade() -> None:
    """Apply the migration."""
    with op.batch_alter_table("exercises") as batch:
        batch.add_column(
            sa.Column("muscles", JSON_COLUMN, nullable=False, server_default="{}")
        )
        batch.add_column(sa.Column("mechanic", MECHANIC, nullable=True))
        batch.add_column(sa.Column("pattern", PATTERN, nullable=True))
        batch.add_column(
            sa.Column("equipment", JSON_COLUMN, nullable=False, server_default="[]")
        )
        batch.add_column(sa.Column("load_type", LOAD_TYPE, nullable=True))
        batch.add_column(
            sa.Column("restrictions", JSON_COLUMN, nullable=False, server_default="[]")
        )

    op.create_table(
        "exercise_notes",
        sa.Column("exercise_id", sa.String(length=80), nullable=False),
        sa.Column("text", sa.String(length=500), nullable=False),
        sa.Column("created_at", UTC, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", UTC, server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["exercise_id"],
            ["exercises.id"],
            name=op.f("fk_exercise_notes_exercise_id_exercises"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("exercise_id", name=op.f("pk_exercise_notes")),
    )


def downgrade() -> None:
    """Revert the migration. Every note and every tag is lost."""
    op.drop_table("exercise_notes")
    with op.batch_alter_table("exercises") as batch:
        batch.drop_column("restrictions")
        batch.drop_column("load_type")
        batch.drop_column("equipment")
        batch.drop_column("pattern")
        batch.drop_column("mechanic")
        batch.drop_column("muscles")

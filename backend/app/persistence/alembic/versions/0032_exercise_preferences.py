"""The athlete's preferences per exercise: one new table.

**`exercise_preferences`**, keyed by the exercise slug like `exercise_notes`:
``avoid`` (never offered as a swap or suggested), ``less_often`` (offered
after everything else), ``remembered_swap_id`` (the pick that ranks first
next time; forgotten, not cascaded, if that catalogue row ever goes), and
``ease_back_step`` (the athlete's place on *Ease back in*, null when not
easing back). The flags default to false in the database too, so a row
written outside the ORM still reads as "nothing stated".

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.persistence.types

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UTC = app.persistence.types.UtcDateTime(timezone=True)


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "exercise_preferences",
        sa.Column("exercise_id", sa.String(length=80), nullable=False),
        sa.Column("avoid", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "less_often", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("remembered_swap_id", sa.String(length=80), nullable=True),
        sa.Column("ease_back_step", sa.SmallInteger(), nullable=True),
        sa.Column("created_at", UTC, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", UTC, server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["exercise_id"],
            ["exercises.id"],
            name=op.f("fk_exercise_preferences_exercise_id_exercises"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["remembered_swap_id"],
            ["exercises.id"],
            name=op.f("fk_exercise_preferences_remembered_swap_id_exercises"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("exercise_id", name=op.f("pk_exercise_preferences")),
    )


def downgrade() -> None:
    """Revert the migration; every stated preference is lost."""
    op.drop_table("exercise_preferences")

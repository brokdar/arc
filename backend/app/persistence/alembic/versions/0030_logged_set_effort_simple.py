"""Simple mode's effort answer on a logged set: `logged_sets.effort_simple`.

The gym screen asks Simple-mode athletes *How did that feel?* — Easy, Good or
Hard — and the answer is stored as said, not converted to reps left (see
`app.domain.activity.EffortSimple`). One nullable column; existing rows were
never asked the question, so they stay null.

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Non-native VARCHAR holding the member value, no CHECK — `enum_column`.
EFFORT_SIMPLE = sa.Enum("easy", "good", "hard", name="effortsimple", native_enum=False)


def upgrade() -> None:
    """Apply the migration."""
    with op.batch_alter_table("logged_sets") as batch:
        batch.add_column(sa.Column("effort_simple", EFFORT_SIMPLE, nullable=True))


def downgrade() -> None:
    """Revert the migration; every stored Easy / Good / Hard answer is lost."""
    with op.batch_alter_table("logged_sets") as batch:
        batch.drop_column("effort_simple")

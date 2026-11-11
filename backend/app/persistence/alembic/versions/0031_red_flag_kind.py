"""Which red-flag text applies, and the under-18 flag: two columns.

* ``athlete.red_flag_kind`` — `emergency` or `advice`
  (`app.domain.athlete.RedFlagKind`), null while the flag is down. A flag
  already up when this runs was raised from the profile form or by the coach,
  which say "I am unwell", never "call an ambulance", so it becomes
  ``advice``. A profile with no flag is left exactly as it was.
* ``athlete_settings.no_max_efforts`` — set from the before-you-start screen's
  "I'm under 18"; ``false`` for the row that already exists.

The plan for this work numbered the revision 0030 over 0029; 0030 merged
first (`0030_logged_set_effort_simple.py`), so this is 0031 over 0030 and the
chain keeps one head.

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Non-native VARCHAR holding the member value, no CHECK — `enum_column`.
RED_FLAG_KIND = sa.Enum("emergency", "advice", name="redflagkind", native_enum=False)


def upgrade() -> None:
    """Apply the migration."""
    with op.batch_alter_table("athlete") as batch:
        batch.add_column(sa.Column("red_flag_kind", RED_FLAG_KIND, nullable=True))
    op.execute(
        "UPDATE athlete SET red_flag_kind = 'advice' WHERE red_flag_active = true"
    )
    with op.batch_alter_table("athlete_settings") as batch:
        batch.add_column(
            sa.Column(
                "no_max_efforts",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    """Revert the migration; which text a flag had, and the under-18 flag, are lost."""
    with op.batch_alter_table("athlete_settings") as batch:
        batch.drop_column("no_max_efforts")
    with op.batch_alter_table("athlete") as batch:
        batch.drop_column("red_flag_kind")

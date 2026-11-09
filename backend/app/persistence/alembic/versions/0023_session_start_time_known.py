"""Whether a session's start time was reported or is only an anchor.

A manual session the athlete dated but never timed ("Kraft 1, Montagabend")
used to force the coach to invent a clock reading and confess it in the note.
`sessions.start_time` stays not null, because every window query and the
overlap check order by it, so the honest representation is a flag: false marks
a `start_time` that is the local midnight of `local_date` and means nothing
more.

Existing rows were all timed, hence the server default true and no backfill.

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    with op.batch_alter_table("sessions") as batch:
        batch.add_column(
            sa.Column(
                "start_time_known",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )


def downgrade() -> None:
    """Revert the migration.

    Day-only sessions keep their midnight anchor as an ordinary start time:
    the flag is the only thing lost.
    """
    with op.batch_alter_table("sessions") as batch:
        batch.drop_column("start_time_known")

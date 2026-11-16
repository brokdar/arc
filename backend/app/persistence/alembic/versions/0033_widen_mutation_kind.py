"""Widen ``sync_mutations.kind`` for ``session_discard``.

``enum_column`` sizes the non-native VARCHAR from the longest member value at
migration time (``app.persistence.types.enum_column``). ``session_discard``
is 15 characters; the column was created at 14 (``session_finish``). SQLite
does not enforce VARCHAR length, so unit tests passed while Postgres refused
the receipt row (a 500 on ``POST /sync``). Batch-altered for SQLite.

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Every member as of this revision (non-native, no CHECK).
MEMBERS = (
    "session_start",
    "session_finish",
    "set_upsert",
    "set_delete",
    "session_rating",
    "dont_learn",
    "note_upsert",
    "session_claim",
    "session_reopen",
    "red_flag",
    "swap",
    "session_discard",
)
OLD = sa.String(length=14)
NEW = sa.Enum(*MEMBERS, name="mutationkind", native_enum=False)


def upgrade() -> None:
    """Apply the migration."""
    with op.batch_alter_table("sync_mutations") as batch:
        batch.alter_column(
            "kind", existing_type=OLD, type_=NEW, existing_nullable=False
        )


def downgrade() -> None:
    """Revert the migration."""
    op.execute(sa.text("DELETE FROM sync_mutations WHERE kind = 'session_discard'"))
    with op.batch_alter_table("sync_mutations") as batch:
        batch.alter_column(
            "kind", existing_type=NEW, type_=OLD, existing_nullable=False
        )

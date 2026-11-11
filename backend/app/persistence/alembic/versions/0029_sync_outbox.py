"""The phone's outbox: receipts and merged state for `POST /sync`.

**`sync_mutations`** is one receipt per mutation id ever applied — what turns
a replay into `already_applied` — and its autoincrement `seq` is the server's
receive order, the conflict rule's tie-breaker (`app.domain.sync`).

**`sync_entities`** holds, per synced session, set and exercise note, the
merged field values and each field's winning stamp; a deleted set's tombstone
lives only here (`app.persistence.sync`).

Both tables are new and empty; nothing existing is touched.

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import app.persistence.types

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: JSON on SQLite, JSONB on Postgres — the `JSONColumn` spelling, written out
#: because a migration must keep saying what it meant on the day it ran.
JSON_COLUMN = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
)
UTC = app.persistence.types.UtcDateTime(timezone=True)
#: Non-native VARCHAR holding the member value, no CHECK — `enum_column`.
MUTATION_KIND = sa.Enum(
    "session_start",
    "session_finish",
    "set_upsert",
    "set_delete",
    "session_rating",
    "dont_learn",
    "note_upsert",
    name="mutationkind",
    native_enum=False,
)


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "sync_mutations",
        sa.Column("seq", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("mutation_id", sa.Uuid(), nullable=False),
        sa.Column("kind", MUTATION_KIND, nullable=False),
        sa.Column("actor", sa.String(length=120), nullable=False),
        sa.Column("received_at", UTC, server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("seq", name=op.f("pk_sync_mutations")),
        sa.UniqueConstraint("mutation_id", name=op.f("uq_sync_mutations_mutation_id")),
    )
    op.create_table(
        "sync_entities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entity", sa.String(length=16), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("values", JSON_COLUMN, nullable=False),
        sa.Column("clocks", JSON_COLUMN, nullable=False),
        sa.Column("updated_at", UTC, server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_sync_entities_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sync_entities")),
        sa.UniqueConstraint("entity", "key", name=op.f("uq_sync_entities_entity")),
    )
    op.create_index(
        op.f("ix_sync_entities_session_id"), "sync_entities", ["session_id"]
    )


def downgrade() -> None:
    """Revert the migration. The server forgets which uploads it has seen."""
    op.drop_index(op.f("ix_sync_entities_session_id"), table_name="sync_entities")
    op.drop_table("sync_entities")
    op.drop_table("sync_mutations")

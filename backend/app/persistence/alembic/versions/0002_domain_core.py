"""WP-1 domain core: athlete, anchor versions, audit log; drop the items example.

`items` was WP-0's worked example, created by 0001. It is dropped here rather
than by editing 0001, which is already on `main`: rewriting a shipped revision
would leave anyone who had run it with a table no migration accounts for. The
downgrade recreates it exactly as 0001 built it, so `head -> base -> head`
round-trips (an integration test enforces that).

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import app.persistence.types

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: JSON on SQLite, JSONB on Postgres — the `JSONColumn` spelling from
#: `app.persistence.types`, written out because a migration must keep saying
#: what it meant on the day it ran, even after the alias changes.
JSON_COLUMN = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
)


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "anchor_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        # Enums are non-native VARCHARs holding the member VALUE (see
        # `app.persistence.types.enum_column`); `cp` and `w_prime` are
        # reserved by the build plan and unused in the MVP.
        sa.Column(
            "anchor_type",
            sa.Enum(
                "ftp",
                "lthr",
                "max_hr",
                "cp",
                "w_prime",
                name="anchortype",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column(
            "unit",
            sa.Enum("W", "bpm", "J", name="anchorunit", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "provenance",
            sa.Enum(
                "assumed",
                "estimated",
                "athlete_reported",
                "tested",
                name="provenance",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("protocol", sa.String(length=200), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("ci_low", sa.Float(), nullable=True),
        sa.Column("ci_high", sa.Float(), nullable=True),
        sa.Column(
            "source",
            sa.Enum("athlete", "agent", name="anchorsource", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "staleness_state",
            sa.Enum(
                "fresh", "aging", "stale", name="stalenessstate", native_enum=False
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            app.persistence.types.UtcDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_anchor_versions")),
    )
    op.create_index(
        op.f("ix_anchor_versions_anchor_type"),
        "anchor_versions",
        ["anchor_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_anchor_versions_effective_date"),
        "anchor_versions",
        ["effective_date"],
        unique=False,
    )

    # One row, fixed primary key (`app.persistence.athlete`). Not seeded here:
    # the service bootstraps it on first access, so a restored dump or a
    # truncating test fixture cannot leave the application without a profile.
    op.create_table(
        "athlete",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column(
            "sex",
            sa.Enum("female", "male", "unspecified", name="sex", native_enum=False),
            nullable=False,
        ),
        sa.Column("height_cm", sa.Float(), nullable=True),
        sa.Column("capabilities", JSON_COLUMN, nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_athlete")),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor", sa.String(length=120), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("payload_json", JSON_COLUMN, nullable=False),
        sa.Column(
            "at",
            app.persistence.types.UtcDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(op.f("ix_audit_log_action"), "audit_log", ["action"], unique=False)
    op.create_index(op.f("ix_audit_log_actor"), "audit_log", ["actor"], unique=False)
    op.create_index(op.f("ix_audit_log_at"), "audit_log", ["at"], unique=False)

    op.drop_index(op.f("ix_items_name"), table_name="items")
    op.drop_table("items")


def downgrade() -> None:
    """Revert the migration."""
    # Rebuilt with 0001's own spellings, so a down-then-up round trip lands
    # on exactly the table 0001 created and can drop it again.
    op.create_table(
        "items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_items")),
    )
    op.create_index(op.f("ix_items_name"), "items", ["name"], unique=True)

    op.drop_index(op.f("ix_audit_log_at"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_actor"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_action"), table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("athlete")
    op.drop_index(
        op.f("ix_anchor_versions_effective_date"), table_name="anchor_versions"
    )
    op.drop_index(op.f("ix_anchor_versions_anchor_type"), table_name="anchor_versions")
    op.drop_table("anchor_versions")

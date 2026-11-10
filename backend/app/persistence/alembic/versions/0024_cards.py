"""Suggestion cards, and whether a session is still going.

**`cards`** is the store behind the one card slot on Today
(`app.domain.cards`). No foreign keys: a card's subject lives in its
``payload`` document, read only by its kind's handler, and a card must survive
whatever it was about.

**`sessions.lifecycle`** says whether a session is between Start and Finish
(`active`) or done (`finished`). Cards never show while one is active. Every
existing row was recorded after the fact, hence the server default `finished`
and no backfill.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import app.persistence.types

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: JSON on SQLite, JSONB on Postgres — the `JSONColumn` spelling, written out
#: because a migration must keep saying what it meant on the day it ran.
JSON_COLUMN = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
)

#: Non-native VARCHARs holding the member value, no CHECK — `enum_column`.
CARD_STATE = sa.Enum(
    "queued",
    "shown",
    "snoozed",
    "accepted",
    "dismissed",
    "expired",
    name="cardstate",
    native_enum=False,
)
SESSION_LIFECYCLE = sa.Enum(
    "active", "finished", name="sessionlifecycle", native_enum=False
)

UTC = app.persistence.types.UtcDateTime(timezone=True)


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "cards",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=60), nullable=False),
        sa.Column("priority", sa.SmallInteger(), nullable=False),
        sa.Column("state", CARD_STATE, nullable=False),
        sa.Column("headline", sa.String(length=120), nullable=False),
        sa.Column("because", sa.String(length=280), nullable=False),
        sa.Column("payload", JSON_COLUMN, nullable=False),
        sa.Column("first_shown", UTC, nullable=True),
        sa.Column("expires_at", UTC, nullable=True),
        sa.Column("snoozed_until", UTC, nullable=True),
        sa.Column("resolved_at", UTC, nullable=True),
        sa.Column("created_by", sa.String(length=120), nullable=False),
        sa.Column(
            "created_at", UTC, server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cards")),
    )
    op.create_index(op.f("ix_cards_kind"), "cards", ["kind"], unique=False)
    op.create_index(op.f("ix_cards_state"), "cards", ["state"], unique=False)
    op.create_index(
        op.f("ix_cards_expires_at"), "cards", ["expires_at"], unique=False
    )

    with op.batch_alter_table("sessions") as batch:
        batch.add_column(
            sa.Column(
                "lifecycle",
                SESSION_LIFECYCLE,
                nullable=False,
                server_default="finished",
            )
        )
        batch.create_index(
            op.f("ix_sessions_lifecycle"), ["lifecycle"], unique=False
        )


def downgrade() -> None:
    """Revert the migration. Cards are dropped with their history."""
    with op.batch_alter_table("sessions") as batch:
        batch.drop_index(op.f("ix_sessions_lifecycle"))
        batch.drop_column("lifecycle")
    op.drop_index(op.f("ix_cards_expires_at"), table_name="cards")
    op.drop_index(op.f("ix_cards_state"), table_name="cards")
    op.drop_index(op.f("ix_cards_kind"), table_name="cards")
    op.drop_table("cards")

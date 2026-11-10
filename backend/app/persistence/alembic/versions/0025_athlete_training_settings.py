"""The athlete's training settings, as a seeded singleton.

**`athlete_settings`** holds one row under scope ``training``
(`app.persistence.athlete_settings`): mode, plain logbook, how often a set
asks for effort, units, locale, high contrast, and the two once-only screens.
The unique ``scope`` is what keeps it one row.

**Seeded here, idempotently.** Every database that reaches 0025 gets the
default row — Simple, kilograms, English, nothing seen or answered — so the
table says what the API already answers. The insert is ``ON CONFLICT (scope)
DO NOTHING``: replaying it (`tests/integration/test_migrations.py`) leaves
the one row, and the athlete's choices in it, alone. The defaults are spelled
out as literals rather than read from `app.domain.training_mode`, because a
migration must keep saying what it meant on the day it ran.

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-10
"""

import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

import app.persistence.types

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Non-native VARCHARs holding the member value, no CHECK — `enum_column`.
TRAINING_MODE = sa.Enum(
    "simple", "standard", "full", name="trainingmode", native_enum=False
)
ASK_FREQUENCY = sa.Enum(
    "always", "last_set", "never", name="askfrequency", native_enum=False
)
UNITS = sa.Enum("kg", "lb", name="units", native_enum=False)
LOCALE = sa.Enum("en", "de", name="locale", native_enum=False)
LIFTED_BEFORE = sa.Enum(
    "never", "a_little", "regularly", name="liftedbefore", native_enum=False
)

UTC = app.persistence.types.UtcDateTime(timezone=True)

#: The default row. Re-runnable: a second run finds the scope taken.
SEED_SQL = (
    "INSERT INTO athlete_settings (id, scope, mode, plain_logbook, "
    "ask_frequency, units, locale, high_contrast, before_you_start_seen, "
    "first_run_seen, lifted_before, created_at, updated_at) VALUES "
    "(:id, 'training', 'simple', false, 'always', 'kg', 'en', false, false, "
    "false, NULL, now(), now()) ON CONFLICT (scope) DO NOTHING"
)


def seed_params() -> dict[str, Any]:
    """Bind parameters for :data:`SEED_SQL`: a fresh uuid7, the ids' convention."""
    return {"id": uuid.uuid7()}


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "athlete_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=40), nullable=False),
        sa.Column("mode", TRAINING_MODE, nullable=False),
        sa.Column("plain_logbook", sa.Boolean(), nullable=False),
        sa.Column("ask_frequency", ASK_FREQUENCY, nullable=False),
        sa.Column("units", UNITS, nullable=False),
        sa.Column("locale", LOCALE, nullable=False),
        sa.Column("high_contrast", sa.Boolean(), nullable=False),
        sa.Column("before_you_start_seen", sa.Boolean(), nullable=False),
        sa.Column("first_run_seen", sa.Boolean(), nullable=False),
        sa.Column("lifted_before", LIFTED_BEFORE, nullable=True),
        sa.Column(
            "created_at", UTC, server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", UTC, server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_athlete_settings")),
        sa.UniqueConstraint("scope", name="uq_athlete_settings_scope"),
    )
    op.execute(sa.text(SEED_SQL).bindparams(**seed_params()))


def downgrade() -> None:
    """Revert the migration. The stored settings are dropped with the table."""
    op.drop_table("athlete_settings")

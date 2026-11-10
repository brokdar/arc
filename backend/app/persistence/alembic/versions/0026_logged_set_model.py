"""Logged sets that know their kind, side, effort and phone id.

**`logged_sets`** gains what a set logged in the gym knows about itself: a
phone-minted `client_id`, `set_kind`, `side`, the planned line it answered,
the movement it replaced, how it reached arc (`source`) and how far it can be
trusted (`confidence`), reported effort (`rir_capped`, `rpe`, `hold_quality`),
timing (`rest_s`, `completed_at`, `utc_offset_min`) and pain (`pain_kind`,
`pain_band`).

The unique constraint moves from `(session_id, set_index)` to
`(session_id, client_id)`: `client_id` is the identity a re-upload is
recognised by, and `set_index` stays the order but has to be rewritable in one
transaction on reorder. Both constraints are named `uq_logged_sets_session_id`
by the metadata convention (it names a unique constraint by its first column),
so the swap drops and recreates one name.

Backfill: every existing row gets its own uuid7 `client_id`, written row by row
in Python because neither dialect generates a uuid7. Every existing row is
`side = both` — the `per_side` ones included, since none ever said which side
it was — `source = recap` (all were typed in after the fact), `set_kind =
working` and `confidence = high`; those come from the server defaults.

**`sessions`** gains `dont_learn` (server default false), its private
`dont_learn_reason`, and the Finish screen's `session_rating`.

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-10
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.persistence.types

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Non-native VARCHARs holding the member value, no CHECK — `enum_column`,
#: written out because a migration must keep saying what it meant on the day
#: it ran.
SET_KIND = sa.Enum(
    "warmup",
    "working",
    "top",
    "backoff",
    "amrap",
    "drop",
    "myo",
    "failure",
    "lengthened_partial",
    name="loggedsetkind",
    native_enum=False,
)
SET_SIDE = sa.Enum("both", "left", "right", name="setside", native_enum=False)
SET_SOURCE = sa.Enum(
    "app", "import", "coach", "recap", name="setsource", native_enum=False
)
SET_CONFIDENCE = sa.Enum("high", "low", name="setconfidence", native_enum=False)
HOLD_QUALITY = sa.Enum(
    "clean", "shaky", "fell", name="holdquality", native_enum=False
)
PAIN_KIND = sa.Enum("ache", "sharp", name="painkind", native_enum=False)
PAIN_BAND = sa.Enum("0-3", "4-5", "6+", name="painband", native_enum=False)

UTC = app.persistence.types.UtcDateTime(timezone=True)

#: The name both the old and the new unique constraint carry.
UNIQUE = "uq_logged_sets_session_id"


def upgrade() -> None:
    """Apply the migration."""
    with op.batch_alter_table("sessions") as batch:
        batch.add_column(
            sa.Column(
                "dont_learn", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch.add_column(
            sa.Column("dont_learn_reason", sa.String(length=500), nullable=True)
        )
        batch.add_column(sa.Column("session_rating", sa.Integer(), nullable=True))

    with op.batch_alter_table("logged_sets") as batch:
        batch.add_column(sa.Column("client_id", sa.Uuid(), nullable=True))
        batch.add_column(
            sa.Column("set_kind", SET_KIND, nullable=False, server_default="working")
        )
        batch.add_column(
            sa.Column("side", SET_SIDE, nullable=False, server_default="both")
        )
        batch.add_column(
            sa.Column("planned_line_ref", sa.String(length=64), nullable=True)
        )
        batch.add_column(
            sa.Column("replaced_exercise_id", sa.String(length=80), nullable=True)
        )
        batch.add_column(
            sa.Column("source", SET_SOURCE, nullable=False, server_default="recap")
        )
        batch.add_column(
            sa.Column(
                "confidence", SET_CONFIDENCE, nullable=False, server_default="high"
            )
        )
        batch.add_column(
            sa.Column(
                "rir_capped", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch.add_column(sa.Column("rpe", sa.Float(), nullable=True))
        batch.add_column(sa.Column("hold_quality", HOLD_QUALITY, nullable=True))
        batch.add_column(sa.Column("rest_s", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("completed_at", UTC, nullable=True))
        batch.add_column(sa.Column("utc_offset_min", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("pain_kind", PAIN_KIND, nullable=True))
        batch.add_column(sa.Column("pain_band", PAIN_BAND, nullable=True))
        batch.create_foreign_key(
            op.f("fk_logged_sets_replaced_exercise_id_exercises"),
            "exercises",
            ["replaced_exercise_id"],
            ["id"],
            ondelete="SET NULL",
        )

    _backfill()

    with op.batch_alter_table("logged_sets") as batch:
        batch.alter_column("client_id", existing_type=sa.Uuid(), nullable=False)
        batch.drop_constraint(UNIQUE, type_="unique")
        batch.create_unique_constraint(UNIQUE, ["session_id", "client_id"])


def _backfill() -> None:
    """A distinct uuid7 per existing set, and `side = both` for per-side rows.

    The side is already `both` from the server default; it is written again
    for the `per_side` rows so the intent survives anyone changing that
    default later.
    """
    connection = op.get_bind()
    logged_sets = sa.table(
        "logged_sets",
        sa.column("id", sa.Uuid()),
        sa.column("client_id", sa.Uuid()),
        sa.column("per_side", sa.Boolean()),
        sa.column("side", sa.String()),
    )
    ids = connection.execute(sa.select(logged_sets.c.id)).scalars().all()
    for row_id in ids:
        connection.execute(
            logged_sets.update()
            .where(logged_sets.c.id == row_id)
            .values(client_id=uuid.uuid7())
        )
    connection.execute(
        logged_sets.update()
        .where(logged_sets.c.per_side == sa.true())
        .values(side="both")
    )


def downgrade() -> None:
    """Revert the migration.

    `set_index` is renumbered 0..n-1 per session first (by its current order,
    then by row id), because the restored unique `(session_id, set_index)`
    would refuse any duplicate positions a reorder left behind. Everything
    else this revision added is lost.
    """
    connection = op.get_bind()
    logged_sets = sa.table(
        "logged_sets",
        sa.column("id", sa.Uuid()),
        sa.column("session_id", sa.Uuid()),
        sa.column("set_index", sa.Integer()),
    )
    ordered = connection.execute(
        sa.select(logged_sets.c.id, logged_sets.c.session_id).order_by(
            logged_sets.c.session_id, logged_sets.c.set_index, logged_sets.c.id
        )
    ).all()
    position: dict[uuid.UUID, int] = {}
    for row_id, session_id in ordered:
        index = position.get(session_id, 0)
        position[session_id] = index + 1
        connection.execute(
            logged_sets.update()
            .where(logged_sets.c.id == row_id)
            .values(set_index=index)
        )

    with op.batch_alter_table("logged_sets") as batch:
        batch.drop_constraint(UNIQUE, type_="unique")
        batch.create_unique_constraint(UNIQUE, ["session_id", "set_index"])
        batch.drop_constraint(
            op.f("fk_logged_sets_replaced_exercise_id_exercises"), type_="foreignkey"
        )
        for column in (
            "pain_band",
            "pain_kind",
            "utc_offset_min",
            "completed_at",
            "rest_s",
            "hold_quality",
            "rpe",
            "rir_capped",
            "confidence",
            "source",
            "replaced_exercise_id",
            "planned_line_ref",
            "side",
            "set_kind",
            "client_id",
        ):
            batch.drop_column(column)

    with op.batch_alter_table("sessions") as batch:
        batch.drop_column("session_rating")
        batch.drop_column("dont_learn_reason")
        batch.drop_column("dont_learn")

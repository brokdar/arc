"""The athlete's own spelling of a watched folder.

`feeds.remote_path` is `path_lower` — Dropbox's normalisation, the identity
`uq_feeds_connection_id_remote_path` is written against, and a lie about the
folder's name. Two surfaces rendered it anyway: the integration card's folder
line, and the "already collecting this folder" refusal the picker prints
directly under a breadcrumb spelled the athlete's way. `/apps/wahoofitness`
under `/Apps/WahooFitness` reads as a case bug in arc, and one real run spent
an hour on the one that was not there.

Nothing recovers the display spelling from the stored one, so it has to be
kept at the moment the folder is chosen — which is what this column is for.

Nullable, and null is a **state**: watched before display paths were stored.
No backfill, deliberately — title-casing a path would be arc asserting a
capitalisation Dropbox never gave it, and getting it wrong on a folder the
athlete named themselves is worse than showing the stored path they have seen
all along, which is what every reader falls back to.

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Kept in step with `app.persistence.connections.MAX_REMOTE_PATH_LENGTH`, and
#: written out rather than imported: a migration states the schema as it was on
#: the day it ran, and importing the model would make the history follow a
#: constant somebody changes later.
MAX_REMOTE_PATH_LENGTH = 1_000


def upgrade() -> None:
    """Apply the migration."""
    op.add_column(
        "feeds",
        # No `batch_alter_table`: adding a nullable column is the one ALTER
        # SQLite has always supported, so the plain form is portable here.
        sa.Column(
            "remote_path_display",
            sa.String(length=MAX_REMOTE_PATH_LENGTH),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Revert the migration.

    The spellings go with the column and both surfaces return to rendering the
    stored path. Nothing else is touched: no feed was ever *identified* by this
    value, so a downgraded instance keeps every folder it was watching.
    """
    op.drop_column("feeds", "remote_path_display")

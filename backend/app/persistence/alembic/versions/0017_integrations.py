"""Integrations: what arc collects, attached to the folders already watching.

Three changes and one backfill.

`integrations` holds one row per source the athlete asked arc to collect from,
unique on `kind` — "add Wahoo again with a second folder" is one integration
with two folders, and the constraint is what makes that true for the backfill
below as well as for the service. **No `local_drop` row is ever written**: the
local drop is synthesized by `IntegrationService.list`, because a row for a
sweep that runs whether or not anyone configured it would be one the athlete
could delete and never get back.

`feeds.integration_id` is **nullable**, and the null means something: "this
folder was configured before integrations existed and has not been classified".
The backfill classifies exactly the feeds whose `remote_path` *is* a catalogue
default path and guesses at nothing else — a folder called `/photos` might well
hold `.fit` files, and filing it under a source the athlete never chose would
be a wrong answer nothing later would question. Nothing is deleted, no
`remote_path` is rewritten, and every unclassified feed keeps polling.

`oauth_authorizations` gains `state` and `redirect_uri`, both nullable and read
by no code in this release. They ship here so the OAuth-redirect PR is a
behaviour change rather than a schema change against a database already holding
live credentials: the migration is the risky half of that work, and it belongs
with the one that is already touching these tables.

`0016` is deliberately absent: it was reserved for the in-app Dropbox app key
while that work sat on a parallel branch, and the reservation was never spent —
the rebuilt change ships its table as `0019` instead. The gap is permanent:
renumbering a shipped chain would strand every database stamped by it.

Revision ID: 0017
Revises: 0015
Create Date: 2026-08-19
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.persistence.types

revision: str = "0017"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Non-native VARCHAR holding the member VALUE, matching `enum_column`.
INTEGRATION_KIND = sa.Enum(
    "local_drop", "wahoo", name="integrationkind", native_enum=False
)

#: A **snapshot** of `app.domain.integrations.CATALOGUE`'s default paths, in
#: the spelling `normalise_remote_path` produces.
#:
#: Copied rather than imported on purpose: a migration is a statement about the
#: database at one moment in history, and importing the live catalogue would
#: silently change what this revision did the day a member is added — replaying
#: the chain on an old dump would then produce a different result from the one
#: that shipped. `local_drop` has no path here because it has no cloud folder.
CATALOGUE_DEFAULT_PATHS: dict[str, str] = {"/apps/wahoofitness": "wahoo"}


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "integrations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", INTEGRATION_KIND, nullable=False),
        sa.Column(
            "created_at",
            app.persistence.types.UtcDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integrations")),
        sa.UniqueConstraint("kind", name="uq_integrations_kind"),
    )

    with op.batch_alter_table("feeds") as batch:
        batch.add_column(sa.Column("integration_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_feeds_integration_id_integrations"),
            "integrations",
            ["integration_id"],
            ["id"],
            ondelete="CASCADE",
        )
    op.create_index(
        op.f("ix_feeds_integration_id"), "feeds", ["integration_id"], unique=False
    )

    with op.batch_alter_table("oauth_authorizations") as batch:
        batch.add_column(sa.Column("state", sa.String(length=128), nullable=True))
        batch.add_column(
            sa.Column("redirect_uri", sa.String(length=500), nullable=True)
        )

    _classify_existing_feeds()


def _classify_existing_feeds() -> None:
    """Attach every feed sitting on a catalogue default path, and only those.

    Idempotent by construction: the integration is looked up before it is
    inserted, so replaying the chain over a database whose feeds are already
    classified produces the same one row rather than a unique-constraint
    failure.
    """
    feeds = sa.table(
        "feeds",
        sa.column("id", sa.Uuid),
        sa.column("remote_path", sa.String),
        sa.column("integration_id", sa.Uuid),
    )
    integrations = sa.table(
        "integrations", sa.column("id", sa.Uuid), sa.column("kind", sa.String)
    )
    bind = op.get_bind()

    for path, kind in CATALOGUE_DEFAULT_PATHS.items():
        matched = list(
            bind.execute(
                sa.select(feeds.c.id).where(feeds.c.remote_path == path)
            ).scalars()
        )
        if not matched:
            # No feed at this path means no integration: an empty row would be
            # an entry in Settings with nothing behind it.
            continue
        integration_id = bind.execute(
            sa.select(integrations.c.id).where(integrations.c.kind == kind)
        ).scalar()
        if integration_id is None:
            integration_id = uuid.uuid7()
            bind.execute(
                integrations.insert().values(id=integration_id, kind=kind)
            )
        bind.execute(
            feeds.update()
            .where(feeds.c.id.in_(matched))
            .values(integration_id=integration_id)
        )


def downgrade() -> None:
    """Revert the migration.

    Every feed row survives with its `remote_path` untouched — the classification
    is the only thing lost, and re-running the upgrade recreates it from the
    paths. The two inert `oauth_authorizations` columns go too; nothing reads
    them at this revision, so no flow is interrupted.
    """
    with op.batch_alter_table("oauth_authorizations") as batch:
        batch.drop_column("redirect_uri")
        batch.drop_column("state")

    op.drop_index(op.f("ix_feeds_integration_id"), table_name="feeds")
    with op.batch_alter_table("feeds") as batch:
        batch.drop_constraint(
            op.f("fk_feeds_integration_id_integrations"), type_="foreignkey"
        )
        batch.drop_column("integration_id")

    op.drop_table("integrations")

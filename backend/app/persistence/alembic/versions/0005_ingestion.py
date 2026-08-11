"""WP-4 ingestion: completed sessions, recordings, repairs and the ingest log.

Six tables, in dependency order. Nothing is seeded and nothing is backfilled —
before this revision there were no completed sessions at all, so there is no
existing row for a new column to have the wrong value for.

Two of them carry columns with no behavior yet, deliberately (addenda §7):
`sessions.weight_kg` / `weight_provenance` (R3) and `sessions.session_context`
(R5) on one side, `recordings.external_id` / `source` (R4) on the other. They
are here because adding a column to a table holding real training data is a
migration plus a re-derivation of everything downstream, and adding it to an
empty one is free.

The load-bearing constraint is `uq_recordings_file_hash` over
``(file_hash, file_sport_index)``: the dedup key. The hash alone would be
wrong for a multisport file, where several activities share one hash and
ingesting the second must not look like re-ingesting the first (A4.5).

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import app.persistence.types

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: JSON on SQLite, JSONB on Postgres — the `JSONColumn` spelling from
#: `app.persistence.types`, written out because a migration must keep saying
#: what it meant on the day it ran, even after the alias changes.
JSON_COLUMN = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
)

#: Enums are non-native VARCHARs holding the member VALUE
#: (`app.persistence.types.enum_column`) with no CHECK constraint (D81), so a
#: member no longer than the longest one here needs no migration at all —
#: which is what made `sessionmatchstatus` a one-member enum here and WP-6's
#: `matched` / `unplanned` / `displaced` free to add later, and what let
#: `streamchannel` gain `distance` (D197) with no migration either. So the
#: vocabularies below are what this revision *created*, not what the
#: application uses today; `app.domain.streams.StreamChannel` and its
#: neighbours are the live lists, and `alembic check` stays green regardless.
SESSION_DISCIPLINE = sa.Enum(
    "cycling", "strength", "other", name="sessiondiscipline", native_enum=False
)
CLASSIFICATION_SOURCE = sa.Enum(
    "sport_field", "heuristic", name="classificationsource", native_enum=False
)
SESSION_MATCH_STATUS = sa.Enum(
    "unmatched", name="sessionmatchstatus", native_enum=False
)
RECORDING_KIND = sa.Enum("device", "manual", name="recordingkind", native_enum=False)
PROVENANCE = sa.Enum(
    "assumed",
    "estimated",
    "athlete_reported",
    "tested",
    name="provenance",
    native_enum=False,
)
SESSION_CONTEXT = sa.Enum(
    "training",
    "commute",
    "group_ride",
    "race",
    "event",
    name="sessioncontext",
    native_enum=False,
)
STREAM_CHANNEL = sa.Enum(
    "power",
    "hr",
    "cadence",
    "speed",
    "elevation",
    "temp",
    "lat",
    "lon",
    name="streamchannel",
    native_enum=False,
)
ANOMALY_KIND = sa.Enum(
    "gap_interpolated",
    "spike_clipped",
    "dropout_held",
    "dropped",
    "resampled_only",
    name="anomalykind",
    native_enum=False,
)
QUARANTINE_REASON = sa.Enum(
    "no_samples",
    "non_monotonic_timestamps",
    "too_short",
    "implausible_channel",
    "unreadable_file",
    "suspected_duplicate",
    name="quarantinereason",
    native_enum=False,
)
QUARANTINE_STATUS = sa.Enum(
    "pending",
    "confirmed_discarded",
    "rejected_ingested",
    name="quarantinestatus",
    native_enum=False,
)
INGEST_OUTCOME = sa.Enum(
    "ingested",
    "duplicate_file",
    "quarantined",
    "error",
    name="ingestoutcome",
    native_enum=False,
)


def upgrade() -> None:
    """Apply the migration."""
    # The completed session: the real-world event. Not `planned_sessions` —
    # that is what was asked for, this is what happened, and until WP-6 there
    # is no link between the two.
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "start_time",
            app.persistence.types.UtcDateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "end_time",
            app.persistence.types.UtcDateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("discipline", SESSION_DISCIPLINE, nullable=False),
        sa.Column("classification_source", CLASSIFICATION_SOURCE, nullable=False),
        sa.Column("discipline_overridden", sa.Boolean(), nullable=False),
        sa.Column("status", SESSION_MATCH_STATUS, nullable=False),
        sa.Column("recording_kind", RECORDING_KIND, nullable=False),
        sa.Column("rpe", sa.Float(), nullable=True),
        sa.Column("notes", sa.String(length=4000), nullable=True),
        # R3, reserved: bodyweight at the time and where it came from.
        sa.Column("weight_kg", sa.Float(), nullable=True),
        sa.Column("weight_provenance", PROVENANCE, nullable=True),
        # R5, reserved: only `training` is ever written in the MVP.
        sa.Column("session_context", SESSION_CONTEXT, nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
    )
    op.create_index(
        op.f("ix_sessions_discipline"), "sessions", ["discipline"], unique=False
    )
    op.create_index(
        op.f("ix_sessions_local_date"), "sessions", ["local_date"], unique=False
    )
    op.create_index(
        op.f("ix_sessions_recording_kind"),
        "sessions",
        ["recording_kind"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sessions_start_time"), "sessions", ["start_time"], unique=False
    )
    op.create_index(op.f("ix_sessions_status"), "sessions", ["status"], unique=False)

    # One device file's account of one session. The A4.4 duration numbers and
    # the A4.3 source provenance live here rather than on the session: they
    # describe the file, and a second file for the same ride would have its
    # own.
    op.create_table(
        "recordings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("file_sport_index", sa.Integer(), nullable=False),
        sa.Column("original_path", sa.String(length=1024), nullable=False),
        sa.Column("original_ext", sa.String(length=16), nullable=False),
        sa.Column("sport", sa.String(length=80), nullable=True),
        sa.Column("elapsed_time_s", sa.Float(), nullable=False),
        sa.Column("recording_time_s", sa.Float(), nullable=False),
        sa.Column("recording_stops", JSON_COLUMN, nullable=False),
        sa.Column("median_time_delta_s", sa.Float(), nullable=False),
        sa.Column("moving_time_s", sa.Float(), nullable=False),
        sa.Column("power_source_candidates", JSON_COLUMN, nullable=False),
        sa.Column("power_source", sa.String(length=200), nullable=True),
        sa.Column("power_source_rule", sa.String(length=200), nullable=True),
        sa.Column("hr_source_candidates", JSON_COLUMN, nullable=False),
        sa.Column("hr_source", sa.String(length=200), nullable=True),
        sa.Column("hr_source_rule", sa.String(length=200), nullable=True),
        sa.Column("channels", JSON_COLUMN, nullable=False),
        # R4, reserved: the vendor's id and which integration it came from.
        sa.Column("external_id", sa.String(length=200), nullable=True),
        sa.Column("source", sa.String(length=60), nullable=True),
        sa.Column(
            "created_at",
            app.persistence.types.UtcDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_recordings_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recordings")),
        # The dedup key; see the module docstring.
        sa.UniqueConstraint(
            "file_hash", "file_sport_index", name=op.f("uq_recordings_file_hash")
        ),
    )
    op.create_index(
        op.f("ix_recordings_session_id"), "recordings", ["session_id"], unique=False
    )

    # A4.2: every region of every channel that was substituted, addressed by
    # row on the 1 Hz grid. An unrecorded repair is indistinguishable from a
    # measurement.
    op.create_table(
        "stream_anomalies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("recording_id", sa.Uuid(), nullable=False),
        sa.Column("channel", STREAM_CHANNEL, nullable=False),
        sa.Column("start_index", sa.Integer(), nullable=False),
        sa.Column("end_index", sa.Integer(), nullable=False),
        sa.Column("kind", ANOMALY_KIND, nullable=False),
        sa.Column("substituted_value", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            app.persistence.types.UtcDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["recording_id"],
            ["recordings.id"],
            name=op.f("fk_stream_anomalies_recording_id_recordings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stream_anomalies")),
    )
    op.create_index(
        op.f("ix_stream_anomalies_recording_id"),
        "stream_anomalies",
        ["recording_id"],
        unique=False,
    )

    # Manual strength entry (B-6). `exercise_id` is SET NULL rather than
    # CASCADE and `exercise_name` is always written, so a set stays readable
    # if the catalogue entry it named is ever removed.
    op.create_table(
        "logged_sets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("exercise_id", sa.String(length=80), nullable=True),
        sa.Column("exercise_name", sa.String(length=160), nullable=False),
        sa.Column("set_index", sa.Integer(), nullable=False),
        sa.Column("reps", sa.Integer(), nullable=False),
        sa.Column("load_kg", sa.Float(), nullable=True),
        sa.Column("rir", sa.Integer(), nullable=True),
        sa.Column("notes", sa.String(length=4000), nullable=True),
        sa.Column(
            "created_at",
            app.persistence.types.UtcDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["exercise_id"],
            ["exercises.id"],
            name=op.f("fk_logged_sets_exercise_id_exercises"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_logged_sets_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_logged_sets")),
        sa.UniqueConstraint(
            "session_id", "set_index", name=op.f("uq_logged_sets_session_id")
        ),
    )
    op.create_index(
        op.f("ix_logged_sets_exercise_id"), "logged_sets", ["exercise_id"], unique=False
    )

    # The athlete's queue of refused files.
    op.create_table(
        "quarantine_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("file_sport_index", sa.Integer(), nullable=True),
        sa.Column("reason", QUARANTINE_REASON, nullable=False),
        sa.Column("detail", sa.String(length=1000), nullable=True),
        sa.Column("quarantined_path", sa.String(length=1024), nullable=False),
        sa.Column("status", QUARANTINE_STATUS, nullable=False),
        sa.Column("suspected_session_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            app.persistence.types.UtcDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "resolved_at",
            app.persistence.types.UtcDateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["suspected_session_id"],
            ["sessions.id"],
            name=op.f("fk_quarantine_records_suspected_session_id_sessions"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quarantine_records")),
    )
    op.create_index(
        op.f("ix_quarantine_records_created_at"),
        "quarantine_records",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_quarantine_records_file_hash"),
        "quarantine_records",
        ["file_hash"],
        unique=False,
    )
    op.create_index(
        op.f("ix_quarantine_records_status"),
        "quarantine_records",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_quarantine_records_suspected_session_id"),
        "quarantine_records",
        ["suspected_session_id"],
        unique=False,
    )

    # The append-only log: one row per file the pipeline looked at.
    op.create_table(
        "ingest_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=True),
        sa.Column("outcome", INGEST_OUTCOME, nullable=False),
        sa.Column("detail", sa.String(length=1000), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column(
            "at",
            app.persistence.types.UtcDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_ingest_events_session_id_sessions"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingest_events")),
    )
    op.create_index(op.f("ix_ingest_events_at"), "ingest_events", ["at"], unique=False)
    op.create_index(
        op.f("ix_ingest_events_file_hash"),
        "ingest_events",
        ["file_hash"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ingest_events_outcome"), "ingest_events", ["outcome"], unique=False
    )
    op.create_index(
        op.f("ix_ingest_events_session_id"),
        "ingest_events",
        ["session_id"],
        unique=False,
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(op.f("ix_ingest_events_session_id"), table_name="ingest_events")
    op.drop_index(op.f("ix_ingest_events_outcome"), table_name="ingest_events")
    op.drop_index(op.f("ix_ingest_events_file_hash"), table_name="ingest_events")
    op.drop_index(op.f("ix_ingest_events_at"), table_name="ingest_events")
    op.drop_table("ingest_events")
    op.drop_index(
        op.f("ix_quarantine_records_suspected_session_id"),
        table_name="quarantine_records",
    )
    op.drop_index(op.f("ix_quarantine_records_status"), table_name="quarantine_records")
    op.drop_index(
        op.f("ix_quarantine_records_file_hash"), table_name="quarantine_records"
    )
    op.drop_index(
        op.f("ix_quarantine_records_created_at"), table_name="quarantine_records"
    )
    op.drop_table("quarantine_records")
    op.drop_index(op.f("ix_logged_sets_exercise_id"), table_name="logged_sets")
    op.drop_table("logged_sets")
    op.drop_index(
        op.f("ix_stream_anomalies_recording_id"), table_name="stream_anomalies"
    )
    op.drop_table("stream_anomalies")
    op.drop_index(op.f("ix_recordings_session_id"), table_name="recordings")
    op.drop_table("recordings")
    op.drop_index(op.f("ix_sessions_status"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_start_time"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_recording_kind"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_local_date"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_discipline"), table_name="sessions")
    op.drop_table("sessions")

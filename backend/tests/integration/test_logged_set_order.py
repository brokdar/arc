"""`set_index` is rewritable order; `client_id` is identity — on both dialects.

AC-23. The unit suite runs on SQLite, which checks a unique constraint per
statement, but the claim is about Postgres as much: under the old unique
`(session_id, set_index)` a reorder that swaps two positions collides half-way
through on either. So the reorder runs here against the real Postgres schema
`alembic upgrade head` built, and against a SQLite schema built from the same
models.

The migration half seeds `0025`-shaped rows and runs `0026` over them, because
a backfill is only correct against data it did not create.
"""

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.domain.activity import (
    ClassificationSource,
    RecordingKind,
    SessionDiscipline,
)
from app.persistence.activity import LoggedSetRow, SessionRow
from app.persistence.db import Base
from tests.integration.test_migrations import at_revision, rows, run_sql

#: The revision under test, and the one it is applied on top of.
LOGGED_SET_MODEL = "0026"
BEFORE = "0025"

START = dt.datetime(2026, 5, 11, 15, 30, tzinfo=dt.UTC)


async def _sqlite() -> AsyncEngine:
    """An in-memory SQLite with the models' schema and foreign keys on."""
    engine = create_async_engine("sqlite+aiosqlite://")

    @event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys(connection: object, _record: object) -> None:
        cursor = connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine


@pytest.fixture(params=["postgres", "sqlite"])
async def engine(request: pytest.FixtureRequest) -> AsyncIterator[AsyncEngine]:
    """The migrated integration Postgres, or a SQLite built from the models."""
    if request.param == "postgres":
        built = create_async_engine(get_settings().postgres.async_url)
    else:
        built = await _sqlite()
    yield built
    await built.dispose()


def a_strength_session() -> SessionRow:
    return SessionRow(
        start_time=START,
        end_time=START + dt.timedelta(hours=1),
        timezone="Europe/Zurich",
        local_date=START.date(),
        discipline=SessionDiscipline.STRENGTH,
        classification_source=ClassificationSource.MANUAL,
        recording_kind=RecordingKind.MANUAL,
    )


async def test_reordering_and_inserting_sets_never_violates_a_constraint(
    engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        parent = a_strength_session()
        session.add(parent)
        await session.flush()
        sets = [
            LoggedSetRow(
                session_id=parent.id, exercise_name=name, set_index=index, reps=5
            )
            for index, name in enumerate("ABCD")
        ]
        session.add_all(sets)
    session_id = parent.id

    # One transaction: 0..3 becomes 3..0, then a new set goes in at index 1
    # and everything at or after it moves down one.
    async with factory() as session, session.begin():
        stored = (
            await session.scalars(
                select(LoggedSetRow).where(LoggedSetRow.session_id == session_id)
            )
        ).all()
        for row in stored:
            row.set_index = 3 - row.set_index
        await session.flush()
        for row in stored:
            if row.set_index >= 1:
                row.set_index += 1
        session.add(
            LoggedSetRow(session_id=session_id, exercise_name="E", set_index=1, reps=5)
        )

    async with factory() as session:
        order = (
            await session.execute(
                select(LoggedSetRow.exercise_name, LoggedSetRow.set_index)
                .where(LoggedSetRow.session_id == session_id)
                .order_by(LoggedSetRow.set_index)
            )
        ).all()
    assert [tuple(row) for row in order] == [
        ("D", 0),
        ("E", 1),
        ("C", 2),
        ("B", 3),
        ("A", 4),
    ]


async def test_a_client_id_is_unique_within_a_session_only(
    engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    shared = uuid.uuid7()
    async with factory() as session, session.begin():
        first, second = a_strength_session(), a_strength_session()
        session.add_all([first, second])
        await session.flush()
        session.add_all(
            [
                LoggedSetRow(
                    session_id=parent.id,
                    client_id=shared,
                    exercise_name="Squat",
                    set_index=0,
                    reps=5,
                )
                for parent in (first, second)
            ]
        )

    async with factory() as session:
        session.add(
            LoggedSetRow(
                session_id=first.id,
                client_id=shared,
                exercise_name="Squat",
                set_index=1,
                reps=5,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()


# --- the migration --------------------------------------------------------------


def seed_legacy_session(session_id: uuid.UUID) -> None:
    """One manual strength session as `0025` shaped it."""
    run_sql(
        [
            (
                (
                    "INSERT INTO sessions (id, start_time, end_time, timezone, "
                    "local_date, discipline, classification_source, "
                    "discipline_overridden, status, recording_kind, session_context) "
                    "VALUES (:id, :start, :end, 'Europe/Zurich', :day, 'strength', "
                    "'manual', false, 'unmatched', 'manual', 'training')"
                ),
                {
                    "id": session_id,
                    "start": START,
                    "end": START + dt.timedelta(hours=1),
                    "day": START.date(),
                },
            )
        ]
    )


def seed_legacy_set(session_id: uuid.UUID, set_index: int, *, per_side: bool) -> None:
    """One `logged_sets` row as `0025` shaped it — no client id, no side."""
    run_sql(
        [
            (
                (
                    "INSERT INTO logged_sets (id, session_id, exercise_name, "
                    "set_index, reps, per_side) VALUES (:id, :session_id, "
                    "'Split squat', :set_index, 8, :per_side)"
                ),
                {
                    "id": uuid.uuid4(),
                    "session_id": session_id,
                    "set_index": set_index,
                    "per_side": per_side,
                },
            )
        ]
    )


def test_the_migration_gives_every_legacy_set_its_own_id_and_both_sides(
    alembic_config: Config,
) -> None:
    at_revision(alembic_config, BEFORE)
    first, second = uuid.uuid4(), uuid.uuid4()
    seed_legacy_session(first)
    seed_legacy_session(second)
    seed_legacy_set(first, 0, per_side=True)
    seed_legacy_set(first, 1, per_side=True)
    seed_legacy_set(first, 2, per_side=False)
    seed_legacy_set(second, 0, per_side=True)

    command.upgrade(alembic_config, LOGGED_SET_MODEL)

    stored = rows(
        "SELECT client_id, per_side, side, source, set_kind, confidence, "
        "rir_capped FROM logged_sets"
    )
    assert len(stored) == 4
    client_ids = [row[0] for row in stored]
    assert all(value is not None for value in client_ids)
    assert len(set(client_ids)) == 4
    assert all(uuid.UUID(str(value)).version == 7 for value in client_ids)
    assert {row[2] for row in stored if row[1]} == {"both"}
    assert {row[2] for row in stored} == {"both"}
    assert {row[3] for row in stored} == {"recap"}
    assert {row[4] for row in stored} == {"working"}
    assert {row[5] for row in stored} == {"high"}
    assert {row[6] for row in stored} == {False}
    assert rows(
        "SELECT dont_learn, dont_learn_reason, session_rating FROM sessions"
    ) == [
        (False, None, None),
        (False, None, None),
    ]

    command.upgrade(alembic_config, "head")


def test_downgrading_renumbers_positions_a_reorder_left_duplicated(
    alembic_config: Config,
) -> None:
    """Back at `0025`, `(session_id, set_index)` is unique again and must hold."""
    command.upgrade(alembic_config, "head")
    session_id = uuid.uuid4()
    seed_legacy_session(session_id)
    for _ in range(3):
        run_sql(
            [
                (
                    (
                        "INSERT INTO logged_sets (id, session_id, client_id, "
                        "exercise_name, set_index, reps) VALUES (:id, :session_id, "
                        ":client_id, 'Squat', 1, 5)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "session_id": session_id,
                        "client_id": uuid.uuid7(),
                    },
                )
            ]
        )

    at_revision(alembic_config, BEFORE)

    assert sorted(row[0] for row in rows("SELECT set_index FROM logged_sets")) == [
        0,
        1,
        2,
    ]
    command.upgrade(alembic_config, "head")


def test_alembic_check_reports_no_drift_after_0026(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    # 0026 is on the chain to head — not necessarily head itself, or every
    # later migration would have to edit this test.
    script = ScriptDirectory.from_config(alembic_config)
    assert LOGGED_SET_MODEL in {
        revision.revision for revision in script.walk_revisions("base", "heads")
    }
    command.check(alembic_config)

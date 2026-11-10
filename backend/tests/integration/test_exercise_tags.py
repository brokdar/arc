"""Exercise tags and notes against a real Postgres, and migration `0027` (AC-36).

What SQLite cannot prove: that `muscles` comes back from JSONB exactly as the
seeder wrote it (float weights, every region key), that the stored columns
really are `jsonb`, that `0027` applies on top of `0026` with catalogue rows
already present — which then re-seed with their tags on first access — and
that `alembic check` is clean afterwards.

Synchronous on purpose, like `test_migrations.py`: `command.upgrade` runs its
own event loop, so the async parts run under `asyncio.run`.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import NullPool, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.domain.actor import Actor
from app.persistence.exercises import ExerciseNoteRow, ExerciseRow
from app.services.exercises import ExerciseService
from app.services.templates import load_exercise_catalogue


def run[T](body: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Run ``body`` on its own engine and session, leaving nothing open."""

    async def _main() -> T:
        engine = create_async_engine(
            get_settings().postgres.async_url, poolclass=NullPool
        )
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                return await body(session)
        finally:
            await engine.dispose()

    return asyncio.run(_main())


def rows(sql: str, **params: Any) -> list[tuple[Any, ...]]:
    """Read the database back as plain tuples."""

    async def _body(session: AsyncSession) -> list[tuple[Any, ...]]:
        return [tuple(row) for row in await session.execute(text(sql), params)]

    return run(_body)


def execute(sql: str, **params: Any) -> None:
    """Run one statement and commit."""

    async def _body(session: AsyncSession) -> None:
        await session.execute(text(sql), params)
        await session.commit()

    run(_body)


def seed() -> int:
    """Seed the catalogue through the service, as a first access would."""

    async def _body(session: AsyncSession) -> int:
        return await ExerciseService.from_session(session).ensure_seeded()

    return run(_body)


def test_muscles_round_trip_through_jsonb(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    seed()
    by_id = {entry.id: entry for entry in load_exercise_catalogue()}

    async def _read(session: AsyncSession) -> list[ExerciseRow]:
        return list((await session.execute(select(ExerciseRow))).scalars())

    stored = run(_read)
    assert len(stored) == len(by_id)
    for row in stored:
        entry = by_id[row.id]
        assert row.muscles == {
            region.value: weight for region, weight in entry.muscles.items()
        }, row.id
        assert all(isinstance(weight, float) for weight in row.muscles.values())
        assert row.equipment == sorted(item.value for item in entry.equipment)
        assert row.restrictions == sorted(tag.value for tag in entry.restrictions)
        assert row.mechanic is entry.mechanic
        assert row.pattern is entry.pattern
        assert row.load_type is entry.load_type

    assert rows(
        "SELECT jsonb_typeof(muscles), muscles -> 'pec_sternal', "
        "jsonb_typeof(equipment), jsonb_typeof(restrictions) "
        "FROM exercises WHERE id = 'bench_press'"
    ) == [("object", 1.0, "array", "array")]
    assert sorted(
        rows(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'exercises' "
            "AND column_name IN ('muscles', 'equipment', 'restrictions')"
        )
    ) == [("equipment", "jsonb"), ("muscles", "jsonb"), ("restrictions", "jsonb")]
    # A second seed finds every JSONB value equal to the file's: nothing to do.
    assert seed() == 0


def test_a_note_round_trips_and_stays_one_per_exercise(
    alembic_config: Config,
) -> None:
    command.upgrade(alembic_config, "head")
    note = "Pins at hole 4 — Kniebeuge 膝 🏋️"

    async def _write(session: AsyncSession) -> None:
        service = ExerciseService.from_session(session)
        await service.set_note("bench_press", "first", actor=Actor.system())
        await service.set_note("bench_press", note, actor=Actor.system())

    run(_write)

    async def _read(session: AsyncSession) -> ExerciseNoteRow | None:
        return await session.get(ExerciseNoteRow, "bench_press")

    row = run(_read)
    assert row is not None
    assert row.text == note
    assert row.updated_at.tzinfo is not None
    assert rows("SELECT count(*) FROM exercise_notes") == [(1,)]


def test_0027_upgrades_from_0026_with_the_catalogue_seeded(
    alembic_config: Config,
) -> None:
    command.downgrade(alembic_config, "0026")
    assert rows("SELECT to_regclass('exercise_notes')") == [(None,)]
    execute(
        "INSERT INTO exercises (id, name, category, unilateral, created_at, "
        "updated_at) VALUES ('bench_press', 'Barbell Bench Press', 'press', "
        "false, now(), now())"
    )

    command.upgrade(alembic_config, "0027")

    # Existing rows get empty tags from the server defaults ...
    assert rows(
        "SELECT muscles, equipment, restrictions, mechanic, pattern, load_type "
        "FROM exercises WHERE id = 'bench_press'"
    ) == [({}, [], [], None, None, None)]
    assert rows("SELECT count(*) FROM exercise_notes") == [(0,)]
    command.upgrade(alembic_config, "head")
    command.check(alembic_config)

    # ... and the first access after the upgrade fills them from the file.
    assert seed() == len(load_exercise_catalogue())
    assert rows(
        "SELECT muscles -> 'pec_sternal', mechanic FROM exercises "
        "WHERE id = 'bench_press'"
    ) == [(1.0, "multi_joint")]


def test_0027_downgrades_and_round_trips(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")

    command.downgrade(alembic_config, "0026")

    assert rows("SELECT to_regclass('exercise_notes')") == [(None,)]
    assert (
        rows(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'exercises' AND column_name IN "
            "('muscles', 'mechanic', 'pattern', 'equipment', 'load_type', "
            "'restrictions')"
        )
        == []
    )
    command.upgrade(alembic_config, "head")
    command.check(alembic_config)

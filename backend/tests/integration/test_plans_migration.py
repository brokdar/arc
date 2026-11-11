"""Migration `0028` against a real Postgres (AC-38).

`0028` **backfills**: every planned session that exists when it runs moves
into one default plan. A backfill is only proved against rows it did not
create, so each test seeds at `0027` — the revision before plans — with raw
SQL in that revision's shape, upgrades, and reads back. The golden comparison
is the whole stored identity of each session and its intent chain, taken
before the upgrade and compared after it: the only thing allowed to change is
the three new columns.

Synchronous on purpose, like `test_migrations.py`: `command.upgrade` runs its
own event loop, so the async parts run under `asyncio.run`.
"""

import asyncio
import datetime as dt
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import NullPool, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.services.planned_sessions import PlannedSessionService

BEFORE_PLANS = "0027"

STRENGTH: dict[str, Any] = {
    "discipline": "strength",
    "groups": [
        {
            "items": [
                {
                    "exercise_id": "back_squat",
                    "sets": 5,
                    "reps": 3,
                    "load": {"kind": "kg", "value": 100.0},
                }
            ]
        }
    ],
}

RIDE: dict[str, Any] = {
    "discipline": "cycling",
    "steps": [
        {
            "kind": "steady",
            "duration_s": 3600,
            "targets": {
                "power": {"kind": "absolute", "low": 180, "high": 200, "unit": "W"}
            },
        }
    ],
}

GOLDEN_SQL = (
    "SELECT s.id, s.date, s.discipline, s.status, i.version, i.purpose, "
    "i.intent_text, i.structure, i.success_criteria, i.pinned_anchor_versions "
    "FROM planned_sessions s JOIN planned_session_intents i "
    "ON i.planned_session_id = s.id ORDER BY s.id, i.version"
)


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
            await asyncio.sleep(0)

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


def seed_session(
    *,
    day: dt.date,
    discipline: str,
    purpose: str,
    structure: dict[str, Any],
    intent_text: str | None,
    versions: int = 1,
) -> uuid.UUID:
    """One planned session and its intent chain, as `0027` shaped them."""
    session_id = uuid.uuid7()
    execute(
        "INSERT INTO planned_sessions (id, date, discipline, status, created_at, "
        "updated_at) VALUES (:id, :date, :discipline, 'planned', now(), now())",
        id=session_id,
        date=day,
        discipline=discipline,
    )
    intent_ids = [uuid.uuid7() for _ in range(versions)]
    for index, intent_id in enumerate(intent_ids):
        superseded = intent_ids[index + 1] if index + 1 < versions else None
        execute(
            "INSERT INTO planned_session_intents (id, planned_session_id, version, "
            "as_of, superseded_by, edited_post_hoc, purpose, intent_text, "
            "success_criteria, pinned_anchor_versions, structure) VALUES "
            "(:id, :session, :version, now(), :superseded, false, :purpose, "
            ":text, CAST(:criteria AS jsonb), '{}'::jsonb, "
            "CAST(:structure AS jsonb))",
            id=intent_id,
            session=session_id,
            version=index + 1,
            superseded=superseded,
            purpose=purpose,
            text=intent_text,
            criteria=json.dumps([]),
            structure=json.dumps(structure),
        )
    return session_id


def test_every_existing_session_moves_into_the_default_plan_unchanged(
    alembic_config: Config,
) -> None:
    command.downgrade(alembic_config, BEFORE_PLANS)
    lift = seed_session(
        day=dt.date(2026, 3, 10),
        discipline="strength",
        purpose="max_strength",
        structure=STRENGTH,
        intent_text="Heavy triples — Kniebeuge 🏋️",
        versions=2,
    )
    ride = seed_session(
        day=dt.date(2026, 3, 12),
        discipline="cycling",
        purpose="endurance",
        structure=RIDE,
        intent_text=None,
    )
    golden = rows(GOLDEN_SQL)
    assert len(golden) == 3

    command.upgrade(alembic_config, "head")

    assert rows(GOLDEN_SQL) == golden
    [(default_id, name, phase, follow, active, is_default, starts, ends)] = rows(
        "SELECT id, name, phase, follow_exactly, active, is_default, starts_on, "
        "ends_before FROM plans"
    )
    assert (phase, follow, active, is_default) == ("base", False, True, True)
    assert name
    assert (starts, ends) == (None, None)
    assert sorted(
        rows("SELECT id, plan_id, key, time_of_day FROM planned_sessions")
    ) == sorted([(lift, default_id, False, None), (ride, default_id, False, None)])

    # And the application reads them back through the ORM: same date, purpose
    # and structure as before the migration ran.
    async def _read(session: AsyncSession) -> list[tuple[Any, ...]]:
        service = PlannedSessionService.from_session(session)
        found = []
        for session_id in (lift, ride):
            row = await service.get(session_id)
            intent = row.current_intent
            found.append(
                (row.date, intent.purpose.value, intent.structure, row.plan_id)
            )
        return found

    assert run(_read) == [
        (dt.date(2026, 3, 10), "max_strength", STRENGTH, default_id),
        (dt.date(2026, 3, 12), "endurance", RIDE, default_id),
    ]


def test_plan_id_is_required_after_the_backfill(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")

    assert rows(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = 'planned_sessions' AND column_name = 'plan_id'"
    ) == [("NO",)]


def test_a_database_with_no_planned_sessions_still_gets_the_default_plan(
    alembic_config: Config,
) -> None:
    command.downgrade(alembic_config, BEFORE_PLANS)
    assert rows("SELECT count(*) FROM planned_sessions") == [(0,)]

    command.upgrade(alembic_config, "head")

    assert rows("SELECT phase, follow_exactly, active, is_default FROM plans") == [
        ("base", False, True, True)
    ]


def test_at_most_one_plan_is_the_default(alembic_config: Config) -> None:
    """`is_default` is TRUE or NULL, and unique: a second TRUE is refused by
    Postgres itself, not by a service that might be bypassed."""
    command.downgrade(alembic_config, BEFORE_PLANS)
    command.upgrade(alembic_config, "head")

    with pytest.raises(IntegrityError, match="uq_plans_is_default"):
        execute(
            "INSERT INTO plans (id, name, phase, follow_exactly, active, "
            "is_default, created_at, updated_at) VALUES "
            "(:id, 'Second default', 'base', false, true, true, now(), now())",
            id=uuid.uuid7(),
        )


def test_alembic_check_is_clean_after_0028(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")

    command.check(alembic_config)


def test_0028_downgrades_and_reapplies(alembic_config: Config) -> None:
    command.downgrade(alembic_config, BEFORE_PLANS)
    seed_session(
        day=dt.date(2026, 3, 10),
        discipline="strength",
        purpose="max_strength",
        structure=STRENGTH,
        intent_text=None,
    )

    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, BEFORE_PLANS)

    assert rows("SELECT count(*) FROM planned_sessions") == [(1,)]
    command.upgrade(alembic_config, "head")
    assert rows("SELECT count(*) FROM plans") == [(1,)]

"""`cards` against a real Postgres, and migration `0024` (AC-8).

What SQLite cannot prove: that the payload comes back from JSONB exactly as
written — nested lists, objects inside lists, non-ASCII text — that the stored
column really is `jsonb`, that the aware timestamps survive, and that `0024`
applies on top of `0023` with sessions already in the table (which become
`finished`) and leaves `alembic check` clean.

Synchronous on purpose, like `test_migrations.py`: `command.upgrade` runs its
own event loop, so the async parts run under `asyncio.run`.
"""

import asyncio
import datetime as dt
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.domain.actor import Actor
from app.domain.cards import CardPriority, CardState
from app.persistence.cards import CardRow
from app.services.cards import NOTE, CardService

PAYLOAD: dict[str, Any] = {
    "exercises": [["back_squat", "front_squat"], ["bench_press"]],
    "sets": [{"reps": 5, "load_kg": 102.5, "tags": ["top", "single"]}, []],
    "note": "Kniebeuge fühlte sich schwer an — 膝 🏋️",
    "flags": [True, False, None],
}


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


def test_a_card_round_trips_every_column(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")

    async def _create(session: AsyncSession) -> uuid.UUID:
        row = await CardService.from_session(session).create(
            actor=Actor.system(),
            kind=NOTE,
            priority=CardPriority.SUNDAY,
            headline="Übersicht für die Woche",
            because="Drei Einheiten — eine davon verschoben.",
            payload=PAYLOAD,
        )
        return row.id

    card_id = run(_create)
    first = dt.datetime(2026, 11, 9, 7, 30, 15, 123456, tzinfo=dt.UTC)

    async def _show(session: AsyncSession) -> None:
        row = await session.get(CardRow, card_id)
        assert row is not None
        row.state = CardState.SHOWN
        row.first_shown = first
        row.expires_at = first + dt.timedelta(days=7)
        await session.commit()

    run(_show)

    async def _read(session: AsyncSession) -> CardRow:
        row = await session.get(CardRow, card_id)
        assert row is not None
        return row

    row = run(_read)
    assert row.kind == NOTE
    assert row.priority == CardPriority.SUNDAY
    assert row.state is CardState.SHOWN
    assert row.payload == PAYLOAD
    assert row.headline == "Übersicht für die Woche"
    assert row.first_shown == first
    assert row.first_shown is not None
    assert row.first_shown.tzinfo is not None
    assert row.expires_at == dt.datetime(2026, 11, 16, 7, 30, 15, 123456, tzinfo=dt.UTC)

    # Stored as JSONB, nested structure intact, text not escaped away.
    assert rows(
        "SELECT jsonb_typeof(payload), jsonb_typeof(payload -> 'exercises' -> 0), "
        "payload ->> 'note', state, priority FROM cards WHERE id = :id",
        id=card_id,
    ) == [("object", "array", PAYLOAD["note"], "shown", 3)]
    assert rows(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'cards' AND column_name = 'payload'"
    ) == [("jsonb",)]


def test_0024_upgrades_from_0023_with_sessions_present(alembic_config: Config) -> None:
    command.downgrade(alembic_config, "0023")
    assert rows("SELECT to_regclass('cards')") == [(None,)]
    session_id = uuid.uuid7()
    execute(
        "INSERT INTO sessions (id, start_time, end_time, start_time_known, timezone, "
        "local_date, discipline, classification_source, discipline_overridden, "
        "status, recording_kind, session_context, created_at, updated_at) VALUES "
        "(:id, now(), now(), true, 'UTC', current_date, 'strength', 'manual', "
        "false, 'unmatched', 'manual', 'training', now(), now())",
        id=session_id,
    )

    command.upgrade(alembic_config, "0024")

    assert rows("SELECT lifecycle FROM sessions WHERE id = :id", id=session_id) == [
        ("finished",)
    ]
    assert rows("SELECT count(*) FROM cards") == [(0,)]
    command.upgrade(alembic_config, "head")
    command.check(alembic_config)


def test_0024_downgrades_and_round_trips(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")

    command.downgrade(alembic_config, "0023")

    assert rows("SELECT to_regclass('cards')") == [(None,)]
    assert (
        rows(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'sessions' AND column_name = 'lifecycle'"
        )
        == []
    )
    command.upgrade(alembic_config, "head")
    command.check(alembic_config)

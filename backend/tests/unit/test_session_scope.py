"""`session_scope` is how non-HTTP code gets a session, and how tests bind it.

MCP tools, APScheduler jobs and ingest have no FastAPI dependency to override,
so `set_session_factory` is the only seam that keeps them off a real Postgres
in the unit suite.
"""

import datetime as dt

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.actor import Actor
from app.domain.anchors import (
    AnchorSource,
    AnchorType,
    AnchorUnit,
    Provenance,
)
from app.persistence import db
from app.persistence.anchors import AnchorVersionRow
from app.persistence.db import get_session_factory, session_scope, set_session_factory
from app.services.anchors import AnchorService


async def test_session_scope_uses_the_installed_factory(
    db_session: AsyncSession,
) -> None:
    # What an MCP tool or scheduler job will do: open a scope, call the same
    # service the API calls, let the service commit.
    async with session_scope() as session:
        await AnchorService.from_session(session).append(
            actor=Actor.agent("coach"),
            anchor_type=AnchorType.FTP,
            value=250,
            provenance=Provenance.ESTIMATED,
            source=AnchorSource.AGENT,
        )

    values = (await db_session.execute(select(AnchorVersionRow.value))).scalars().all()
    assert list(values) == [250]


async def _job_that_writes_then_fails() -> None:
    """A non-HTTP caller that dies with uncommitted work pending."""
    async with session_scope() as session:
        session.add(
            AnchorVersionRow(
                anchor_type=AnchorType.FTP,
                value=999,
                unit=AnchorUnit.WATT,
                provenance=Provenance.ASSUMED,
                effective_date=dt.date(2026, 3, 1),
                source=AnchorSource.AGENT,
            )
        )
        await session.flush()
        raise RuntimeError("job failed")


async def test_session_scope_rolls_back_on_error(db_session: AsyncSession) -> None:
    with pytest.raises(RuntimeError, match="job failed"):
        await _job_that_writes_then_fails()

    assert (await db_session.execute(select(AnchorVersionRow))).scalars().all() == []


def test_the_factory_override_is_resettable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    assert get_session_factory() is session_factory

    set_session_factory(None)

    # The cached engine goes with it. It is built once from `get_settings()`,
    # which every test re-reads after `get_settings.cache_clear()`; leaving it
    # behind would pin the whole run to the first test's settings.
    assert db._session_factory is None
    assert db._engine is None

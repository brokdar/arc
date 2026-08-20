"""Guard: a write a caller loses is an `AppError`, never a 500.

Every repository flushes through `app.persistence.db`, which is the one place
that can classify a driver exception — so this is where the classification is
pinned. Each case here is produced by really losing the race (two sessions, one
row) rather than by raising the exception at the test, because what matters is
that SQLAlchemy raises what this layer claims to catch.

Found by Schemathesis: the fuzzer runs `--workers auto`, which is the only
thing in this repository that writes concurrently, and both cases below reached
the athlete as 500s.
"""

import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import ConflictError
from app.domain.connections import ConnectionProvider
from app.persistence.connections import ProviderAppRow
from app.persistence.db import commit, flush


async def test_an_update_onto_a_deleted_row_is_a_conflict_not_a_crash(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as writer, session_factory() as deleter:
        row = ProviderAppRow(provider=ConnectionProvider.DROPBOX, app_key="first")
        writer.add(row)
        await commit(writer)

        # The read half of a read-modify-write: the row is in `writer`'s
        # identity map and about to be updated.
        held = await writer.get(ProviderAppRow, row.id)
        assert held is not None

        deleted = await deleter.get(ProviderAppRow, row.id)
        assert deleted is not None
        await deleter.delete(deleted)
        await commit(deleter)

        held.app_key = "second"
        with pytest.raises(ConflictError):
            await flush(writer)


async def test_the_session_is_usable_after_a_lost_write(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The rollback is the point: a poisoned session fails the *next* request."""
    async with session_factory() as writer, session_factory() as deleter:
        row = ProviderAppRow(provider=ConnectionProvider.DROPBOX, app_key="first")
        writer.add(row)
        await commit(writer)
        # Read before the rollback: afterwards the instance is expired, and
        # touching it would emit IO the failed transaction cannot serve.
        row_id: uuid.UUID = row.id

        held = await writer.get(ProviderAppRow, row_id)
        assert held is not None
        deleted = await deleter.get(ProviderAppRow, row_id)
        assert deleted is not None
        await deleter.delete(deleted)
        await commit(deleter)

        held.app_key = "second"
        with pytest.raises(ConflictError):
            await flush(writer)

        writer.add(ProviderAppRow(provider=ConnectionProvider.DROPBOX, app_key="third"))
        await commit(writer)

        assert await writer.get(ProviderAppRow, row_id) is None


def test_no_repository_refreshes_outside_the_guarded_helper() -> None:
    """`session.refresh` on a row another write deleted is a 500.

    A guard rather than a convention, because the direct call is the obvious
    thing to write and the failure only appears under concurrent load — which
    in this repository means the nightly fuzzer, on somebody else's branch.
    """
    root = Path(__file__).parents[2] / "app"
    offenders = [
        f"{path.relative_to(root)}:{number}"
        for path in sorted(root.rglob("*.py"))
        if path.name != "db.py"
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if re.search(r"session\.refresh\(", line)
    ]

    assert offenders == [], (
        "call `app.persistence.db.refresh` instead: it turns a row deleted "
        f"mid-write into a 409 rather than a 500 — {offenders}"
    )


async def test_a_uniqueness_race_is_still_a_conflict(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The case this layer already handled, kept honest by the rename."""
    async with session_factory() as first, session_factory() as second:
        first.add(ProviderAppRow(provider=ConnectionProvider.DROPBOX, app_key="a"))
        await commit(first)

        second.add(ProviderAppRow(provider=ConnectionProvider.DROPBOX, app_key="b"))
        with pytest.raises(ConflictError):
            await commit(second)

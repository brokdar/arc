"""Guard: a write a caller loses is an `AppError`, never a 500.

Every repository flushes through `app.persistence.db`, which is the one place
that can classify a driver exception — so this is where the classification is
pinned. Each case here is produced by really losing the race (two sessions, one
row) rather than by raising the exception at the test, because what matters is
that SQLAlchemy raises what this layer claims to catch.

Found by Schemathesis: the fuzzer runs `--workers auto`, which is the only
thing in this repository that writes concurrently, and the first two cases
below reached the athlete as 500s.

**Every table with both a write path and a delete path is raced here**, one
test each, because "SQLAlchemy raises `StaleDataError` for this" is a fact
about the *mapping* — a relationship that loads its collection on delete, a
`passive_deletes` flag, a bulk statement that would quietly match zero rows and
raise nothing at all — and not a fact this layer can assume. The provider-app
pair proves the classification; the rest prove each mapping actually reaches
it.

The arrangement is always the same: two sessions on one engine, one of them
deletes the row the other is holding, and the holder then really flushes. The
*timing* is forced, which is the point — a race left to chance is a test that
passes for the wrong reason — but nothing here raises the exception on the
application's behalf.

**Read "two sessions" narrowly.** The unit engine is in-memory SQLite on a
`StaticPool` (`tests/unit/conftest.py`), so both sessions check out the *same*
DBAPI connection and therefore the same transaction — one connection wearing
two hats. What that really exercises is the ORM's response to a write that
matches zero rows: `StaleDataError` on the UPDATE, a `SAWarning` on the DELETE,
and `app.persistence.db` turning either into a `ConflictError`. That is the
whole claim, and it is the claim this layer needs. What it does *not* exercise
is isolation: there is no second transaction, and the deleter's `commit` would
commit anything the holder had already flushed. Every test here happens to
inject its delete before the holder's first write, so nothing is smuggled
across — but a new one copying the shape is one reordering away from a test
that passes for a reason it did not mean. Behaviour that genuinely depends on
two transactions (locking, isolation level, real concurrent commits) belongs in
`tests/integration/`, against Postgres.
"""

import datetime as dt
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import SAWarning
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import athlete_today
from app.core.exceptions import ConflictError
from app.domain.connections import ConnectionProvider
from app.domain.integrations import IntegrationKind
from app.domain.matching import MatchLinkStatus
from app.domain.wellness import WellnessSource
from app.persistence.connections import (
    ConnectionRepository,
    ConnectionRow,
    FeedRow,
    OAuthAuthorizationRow,
    ProviderAppRow,
)
from app.persistence.db import commit, flush
from app.persistence.integrations import IntegrationRow
from app.persistence.matching import SessionMatchRepository, SessionMatchRow
from app.persistence.wellness import WellnessDayRow, WellnessRepository


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


async def test_a_day_retracted_under_an_edit_is_a_conflict_not_a_crash(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`wellness_days`: PATCH the day while the same day is being cleared.

    Both halves are the one endpoint — `PATCH /wellness/days/{date}` writes a
    value, and the same PATCH clearing the last value on a day *deletes* the
    row (`WellnessRepository.delete`'s docstring: absence is how this surface
    spells "nothing was reported"). So the athlete's phone and the coach agent
    editing the same morning is exactly this race, and neither of them has to
    be doing anything unusual.
    """
    today = athlete_today()
    async with session_factory() as writer, session_factory() as retractor:
        writer.add(
            WellnessDayRow(
                local_date=today, resting_hr_bpm=46, source=WellnessSource.ATHLETE
            )
        )
        await commit(writer)

        held = (
            (
                await writer.execute(
                    select(WellnessDayRow).where(WellnessDayRow.local_date == today)
                )
            )
            .scalars()
            .one()
        )
        retracted = (
            (
                await retractor.execute(
                    select(WellnessDayRow).where(WellnessDayRow.local_date == today)
                )
            )
            .scalars()
            .one()
        )
        await WellnessRepository(retractor).delete(retracted)
        await commit(retractor)

        held.resting_hr_bpm = 48
        with pytest.raises(ConflictError):
            await WellnessRepository(writer).add(held)


async def test_refreshing_a_token_on_a_disconnected_connection_is_a_conflict(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`connections`: a token refresh landing after the athlete disconnected.

    The write side is `app.connectors.dropbox.DropboxClient`, which reseals the
    row when Dropbox hands back a new access token — a read-modify-write on a
    row the athlete can remove from the settings panel at any moment, and one
    that runs from the poll rather than from a request, so it is running
    exactly when nobody is watching. Raced on the update rather than on the
    delete deliberately: see the idempotent-delete test below for why a lost
    DELETE is not this.
    """
    async with session_factory() as refresher, session_factory() as athlete:
        row = ConnectionRow(
            provider=ConnectionProvider.DROPBOX, credentials=b"sealed-blob"
        )
        refresher.add(row)
        await commit(refresher)
        connection_id: uuid.UUID = row.id

        held = await refresher.get(ConnectionRow, connection_id)
        assert held is not None
        disconnected = await athlete.get(ConnectionRow, connection_id)
        assert disconnected is not None
        await ConnectionRepository(athlete).delete(disconnected)
        await commit(athlete)

        held.credentials = b"resealed-blob"
        held.access_token_expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(hours=4)
        with pytest.raises(ConflictError):
            await flush(refresher)


async def test_spending_an_authorization_a_second_connect_dropped_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`oauth_authorizations`: the one table here whose lost race is *not* a 409.

    Every write to this table is an INSERT or a DELETE — `replace_authorization`
    drops the earlier pending flows and adds a new row, `complete_dropbox`
    spends the one it read — so there is no read-modify-write to go stale, and
    pressing Connect again while a paste is in flight leaves the paste deleting
    a row that is already gone.

    SQLAlchemy does not raise for that. It counts the rows the DELETE matched
    and *warns* (`confirm_deleted_rows`), because the caller's intent — this
    row should not exist — is already satisfied. So the losing side commits
    normally and the athlete sees the flow complete, which is the right answer
    and worth pinning: a future reader looking for the missing conflict test
    for this table should find this instead of assuming it was forgotten.

    Asserted through `pytest.warns` because `filterwarnings = ["error"]` turns
    that warning into a failure in this suite and into nothing at all in
    production — so the warning is proof the race really happened, and the
    surrounding `commit` is proof it was survivable.
    """
    async with session_factory() as pasting, session_factory() as reconnecting:
        pending = OAuthAuthorizationRow(
            provider=ConnectionProvider.DROPBOX,
            code_verifier="a-verifier",
            expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=15),
        )
        pasting.add(pending)
        await commit(pasting)

        [held] = await ConnectionRepository(pasting).authorizations(
            ConnectionProvider.DROPBOX
        )
        [superseded] = await ConnectionRepository(reconnecting).authorizations(
            ConnectionProvider.DROPBOX
        )
        await ConnectionRepository(reconnecting).delete_authorization(superseded)
        await commit(reconnecting)

        with pytest.warns(SAWarning, match="expected to delete 1 row"):
            await ConnectionRepository(pasting).delete_authorization(held)
        await commit(pasting)

        assert (
            await ConnectionRepository(pasting).authorizations(
                ConnectionProvider.DROPBOX
            )
            == []
        )


async def test_adding_a_folder_to_a_removed_integration_is_a_conflict_not_a_crash(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`integrations`: add a second folder while the integration is removed.

    `IntegrationService.add` appends the feed to an integration it has already
    read, so the losing write is an INSERT carrying a foreign key to a row that
    is no longer there. That is the other shape a lost race takes — an
    integrity violation rather than a stale UPDATE — and `app.persistence.db`
    answers both with `ConflictError`, which is what makes "the athlete gets a
    409" true without the service knowing which one happened.
    """
    async with session_factory() as adder, session_factory() as remover:
        connection = ConnectionRow(
            provider=ConnectionProvider.DROPBOX, credentials=b"sealed-blob"
        )
        integration = IntegrationRow(kind=IntegrationKind.WAHOO)
        adder.add(connection)
        adder.add(integration)
        await commit(adder)
        integration_id: uuid.UUID = integration.id

        removed = await remover.get(IntegrationRow, integration_id)
        assert removed is not None
        await remover.delete(removed)
        await commit(remover)

        adder.add(
            FeedRow(
                connection_id=connection.id,
                integration_id=integration_id,
                remote_path="/Apps/WahooFitness",
            )
        )
        with pytest.raises(ConflictError):
            await flush(adder)


async def test_confirming_a_link_the_athlete_unlinked_is_a_conflict_not_a_crash(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`session_matches`: confirm a link that was unlinked in the same moment.

    The endpoint pair is real and adjacent on the same screen —
    `POST /matches/{id}/confirm` reads the link and writes its status, while
    `DELETE /matches/{id}` removes it — so this is the plainest read-modify-
    write race in the application.

    The two rows the link points at are built through HTTP rather than typed
    in: `session_matches` has a foreign key to each, and a link between rows
    the application would never have produced proves nothing about the mapping
    the application actually uses.
    """
    link_id = await _a_link(client)

    async with session_factory() as confirmer, session_factory() as unlinker:
        held = await SessionMatchRepository(confirmer).get(link_id)
        assert held is not None
        gone = await unlinker.get(SessionMatchRow, link_id)
        assert gone is not None
        await unlinker.delete(gone)
        await commit(unlinker)

        held.status = MatchLinkStatus.CONFIRMED
        held.confirmed_at = dt.datetime.now(dt.UTC)
        with pytest.raises(ConflictError):
            await SessionMatchRepository(confirmer).add(held)


async def _a_link(client: AsyncClient) -> uuid.UUID:
    """One planned session, one session that happened, linked by hand."""
    day = athlete_today() - dt.timedelta(days=1)
    structure: dict[str, Any] = {
        "discipline": "cycling",
        "steps": [{"kind": "steady", "duration_s": 3600, "role": "work"}],
    }
    anchor = await client.post(
        "/api/v1/anchors",
        json={"anchor_type": "ftp", "value": 250, "provenance": "estimated"},
    )
    assert anchor.status_code == 201, anchor.text
    planned = await client.post(
        "/api/v1/planned-sessions",
        json={"date": day.isoformat(), "purpose": "endurance", "structure": structure},
    )
    assert planned.status_code == 201, planned.text
    done = await client.post(
        "/api/v1/manual-sessions",
        json={
            "start_time": f"{day.isoformat()}T09:00:00+00:00",
            "timezone": "UTC",
            "duration_s": 3600,
            "discipline": "cycling",
            "sets": [],
        },
    )
    assert done.status_code == 201, done.text
    # Matching runs on ingest, so the link already exists — posting one by hand
    # here would be answered with the 409 that says the session is spoken for.
    link = done.json()["match"]
    assert link is not None, done.text
    return uuid.UUID(link["id"])


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

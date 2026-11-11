"""The §13.2 conflict table, row by row, on Postgres (AC-42).

The phone's uploads go through `POST /sync` against the real schema
`alembic upgrade head` built — JSONB entity state, the receipts' sequence,
the unique keys. The coach has no `/sync` door of its own (the API is the
athlete's), so the coach's edit is the same service called with an agent
actor, which is what any coach surface will call. Every outcome is read back
from the stored session through `GET /sessions/{id}`.

The rule under test is `app.domain.sync`: per field, the latest client edit
time wins, the server's receive order breaks a tie, deletes are tombstones,
and a coach edit's time is the server time it arrived.
"""

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.domain.actor import Actor
from app.domain.sync import MAX_SCHEMA_VERSION, MutationKind
from app.services.sync import MutationInput, SyncService

SYNC = "/api/v1/sync"
SESSIONS = "/api/v1/sessions"

#: Edits are stamped around the real now, so the coach's server-time stamp
#: falls between them where a case needs it to.
NOW = dt.datetime.now(dt.UTC).replace(microsecond=0)
START = NOW - dt.timedelta(hours=1)


def ago(minutes: float) -> str:
    return (NOW - dt.timedelta(minutes=minutes)).isoformat()


class Phone:
    """One device's outbox for one session: mints ids, stamps edits."""

    def __init__(self, session_client_id: uuid.UUID | None = None) -> None:
        self.session = session_client_id or uuid.uuid7()

    def start(self, at: str | None = None) -> dict[str, Any]:
        return {
            "kind": "session_start",
            "mutation_id": str(uuid.uuid7()),
            "client_edit_time": at or START.isoformat(),
            "session_client_id": str(self.session),
            "start_time": START.isoformat(),
            "timezone": "Europe/Zurich",
        }

    def upsert(self, set_id: uuid.UUID, at: str, **fields: Any) -> dict[str, Any]:
        return {
            "kind": "set_upsert",
            "mutation_id": str(uuid.uuid7()),
            "client_edit_time": at,
            "session_client_id": str(self.session),
            "set_client_id": str(set_id),
            "set": fields,
        }

    def delete(self, set_id: uuid.UUID, at: str) -> dict[str, Any]:
        return {
            "kind": "set_delete",
            "mutation_id": str(uuid.uuid7()),
            "client_edit_time": at,
            "session_client_id": str(self.session),
            "set_client_id": str(set_id),
        }

    def finish(self, at: str) -> dict[str, Any]:
        return {
            "kind": "session_finish",
            "mutation_id": str(uuid.uuid7()),
            "client_edit_time": at,
            "session_client_id": str(self.session),
            "end_time": at,
        }

    def dont_learn(self, value: bool, at: str) -> dict[str, Any]:
        return {
            "kind": "dont_learn",
            "mutation_id": str(uuid.uuid7()),
            "client_edit_time": at,
            "session_client_id": str(self.session),
            "dont_learn": value,
        }


def squat(index: int = 0, **extra: Any) -> dict[str, Any]:
    return {
        "exercise_id": "back_squat",
        "reps": 5,
        "load_kg": 100.0,
        "set_index": index,
        **extra,
    }


async def upload(client: AsyncClient, *mutations: dict[str, Any]) -> None:
    response = await client.post(
        SYNC, json={"schema_version": MAX_SCHEMA_VERSION, "mutations": list(mutations)}
    )
    assert response.status_code == 200, response.text


async def the_session(client: AsyncClient) -> dict[str, Any]:
    """The one stored session, as `GET /sessions/{id}` renders it."""
    listing = (await client.get(SESSIONS)).json()
    assert listing["total"] == 1, listing
    response = await client.get(f"{SESSIONS}/{listing['items'][0]['id']}")
    assert response.status_code == 200, response.text
    return response.json()


def stored_set(session: dict[str, Any], set_id: uuid.UUID) -> dict[str, Any] | None:
    return next(
        (s for s in session["logged_sets"] if s["client_id"] == str(set_id)), None
    )


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().postgres.async_url, poolclass=NullPool)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()


async def coach_edit(
    db: AsyncSession, phone: Phone, set_id: uuid.UUID, **fields: Any
) -> None:
    """The coach changes a set. It claims an old edit time; server time wins."""
    await SyncService.from_session(db).apply(
        [
            MutationInput(
                mutation_id=uuid.uuid7(),
                kind=MutationKind.SET_UPSERT,
                client_edit_time=START,
                session_client_id=phone.session,
                set_client_id=set_id,
                fields={"set": fields},
            )
        ],
        schema_version=MAX_SCHEMA_VERSION,
        actor=Actor.agent("coach"),
    )


# --- (a) phone edit at 10:00, coach edit at 10:05 ------------------------------


async def test_a_coach_edit_after_the_phone_edit_wins_per_field(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The phone's older edit loses the field the coach changed, keeps the other."""
    phone, set_id = Phone(), uuid.uuid7()
    await upload(client, phone.start(), phone.upsert(set_id, ago(20), **squat()))

    # The coach corrects the load now (server time).
    await coach_edit(db, phone, set_id, load_kg=102.5)
    # The phone's offline edit was made five minutes ago and uploads now.
    await upload(client, phone.upsert(set_id, ago(5), reps=6, load_kg=97.5))

    stored = stored_set(await the_session(client), set_id)
    assert stored is not None
    assert stored["load_kg"] == 102.5
    assert stored["reps"] == 6
    # A coach correction does not make the athlete's set the coach's.
    assert stored["source"] == "app"


# --- (b) two devices log the same session offline -----------------------------


async def test_two_devices_creating_sets_offline_keep_both(client: AsyncClient) -> None:
    """Different client ids are different sets, even at one index."""
    phone = Phone()
    tablet = Phone(phone.session)
    first, second = uuid.uuid7(), uuid.uuid7()

    await upload(client, phone.start(), phone.upsert(first, ago(30), **squat(0)))
    await upload(
        client,
        tablet.start(ago(31)),
        tablet.upsert(second, ago(29), **squat(0, load_kg=80.0)),
    )

    session = await the_session(client)
    assert sorted((s["client_id"], s["load_kg"]) for s in session["logged_sets"]) == (
        sorted([(str(first), 100.0), (str(second), 80.0)])
    )


# --- (c) an offline edit made after the original uploaded ---------------------


async def test_an_offline_edit_after_the_upload_applies_when_newer(
    client: AsyncClient,
) -> None:
    phone, set_id = Phone(), uuid.uuid7()
    await upload(client, phone.start(), phone.upsert(set_id, ago(30), **squat(rir=2)))

    await upload(client, phone.upsert(set_id, ago(10), rir=1))
    assert stored_set(await the_session(client), set_id)["rir"] == 1  # type: ignore[index]

    # An edit made before the one stored is not applied, however late it lands.
    await upload(client, phone.upsert(set_id, ago(40), rir=4))
    assert stored_set(await the_session(client), set_id)["rir"] == 1  # type: ignore[index]


# --- (d) delete on one side, edit on the other ---------------------------------


async def test_a_delete_newer_than_the_edit_leaves_the_set_deleted(
    client: AsyncClient,
) -> None:
    phone, set_id = Phone(), uuid.uuid7()
    await upload(client, phone.start(), phone.upsert(set_id, ago(30), **squat()))

    await upload(client, phone.delete(set_id, ago(10)))
    await upload(client, phone.upsert(set_id, ago(20), reps=8))

    assert stored_set(await the_session(client), set_id) is None


async def test_an_edit_newer_than_the_delete_revives_the_set(
    client: AsyncClient,
) -> None:
    phone, set_id = Phone(), uuid.uuid7()
    await upload(client, phone.start(), phone.upsert(set_id, ago(30), **squat()))
    await upload(client, phone.delete(set_id, ago(20)))
    assert stored_set(await the_session(client), set_id) is None

    await upload(client, phone.upsert(set_id, ago(10), reps=8))

    revived = stored_set(await the_session(client), set_id)
    assert revived is not None
    # Revived with every field it had, and the edit that revived it.
    assert (revived["exercise_id"], revived["load_kg"], revived["reps"]) == (
        "back_squat",
        100.0,
        8,
    )


# --- (e) "don't learn" toggled offline ----------------------------------------


async def metric_versions(db: AsyncSession, session_id: str) -> int:
    return int(
        await db.scalar(
            text("SELECT count(*) FROM session_metrics WHERE session_id = :id"),
            {"id": uuid.UUID(session_id)},
        )
        or 0
    )


async def test_the_latest_dont_learn_toggle_wins_and_numbers_recompute(
    client: AsyncClient, db: AsyncSession
) -> None:
    phone, set_id = Phone(), uuid.uuid7()
    await upload(
        client,
        phone.start(),
        phone.upsert(set_id, ago(50), **squat()),
        phone.finish(ago(5)),
    )
    session_id = (await the_session(client))["id"]
    before = await metric_versions(db, session_id)
    assert before >= 1

    await upload(client, phone.dont_learn(True, ago(2)))
    assert (await the_session(client))["dont_learn"] is True
    after_toggle = await metric_versions(db, session_id)
    assert after_toggle == before + 1

    # The other device's toggle was made earlier: it loses, nothing recomputes.
    await upload(client, Phone(phone.session).dont_learn(False, ago(3)))
    assert (await the_session(client))["dont_learn"] is True
    assert await metric_versions(db, session_id) == after_toggle

    # A later toggle wins and recomputes again.
    await upload(client, phone.dont_learn(False, ago(1)))
    assert (await the_session(client))["dont_learn"] is False
    assert await metric_versions(db, session_id) == after_toggle + 1


# --- edges --------------------------------------------------------------------


async def test_identical_edit_times_resolve_by_receive_order(
    client: AsyncClient,
) -> None:
    """Two edits made at the same instant: the one received later stands."""
    phone, set_id = Phone(), uuid.uuid7()
    other = Phone(phone.session)
    await upload(client, phone.start(), phone.upsert(set_id, ago(30), **squat()))

    await upload(client, phone.upsert(set_id, ago(10), reps=7))
    await upload(client, other.upsert(set_id, ago(10), reps=9))

    assert stored_set(await the_session(client), set_id)["reps"] == 9  # type: ignore[index]


async def test_only_session_start_creates_a_session(client: AsyncClient) -> None:
    phone, set_id = Phone(), uuid.uuid7()

    for orphan in (
        phone.upsert(set_id, ago(10), **squat()),
        phone.finish(ago(5)),
        phone.dont_learn(True, ago(5)),
    ):
        response = await client.post(
            SYNC, json={"schema_version": MAX_SCHEMA_VERSION, "mutations": [orphan]}
        )
        assert response.status_code == 422, response.text
        assert response.json()["mutation_id"] == orphan["mutation_id"]
    assert (await client.get(SESSIONS)).json()["total"] == 0

    await upload(client, phone.start())
    session = await the_session(client)
    assert session["logged_sets"] == []

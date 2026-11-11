"""`POST /sync`: the phone's outbox, applied once, in order (AC-41, AC-43).

Every claim about storage is read back from the stored rows — the session
through `GET /sessions/{id}`, the audit trail and the receipts from the
database — never from the sync response alone.
"""

import datetime as dt
import tomllib
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.sync import MAX_BATCH_MUTATIONS, MAX_SCHEMA_VERSION
from app.persistence.activity import LoggedSetRow, SessionRow
from app.persistence.audit import AuditLogEntry
from app.persistence.exercises import ExerciseNoteRow

SYNC = "/api/v1/sync"
SESSIONS = "/api/v1/sessions"

START = dt.datetime(2026, 11, 9, 17, 30, tzinfo=dt.UTC)
SESSION_ID = "0199a1b2-0000-7000-8000-00000000a001"
SET_IDS = [f"0199a1b2-0000-7000-8000-00000000b00{n}" for n in range(1, 4)]


def mid(n: int) -> str:
    """A fixed mutation id, so a replay carries the same ones."""
    return f"0199a1b2-0000-7000-8000-{n:012d}"


def edit(minutes: int) -> str:
    return (START + dt.timedelta(minutes=minutes)).isoformat()


def start(n: int = 1, **extra: Any) -> dict[str, Any]:
    return {
        "kind": "session_start",
        "mutation_id": mid(n),
        "client_edit_time": edit(0),
        "session_client_id": SESSION_ID,
        "start_time": START.isoformat(),
        "timezone": "Europe/Zurich",
        **extra,
    }


def upsert(n: int, set_id: str, minutes: int, **fields: Any) -> dict[str, Any]:
    return {
        "kind": "set_upsert",
        "mutation_id": mid(n),
        "client_edit_time": edit(minutes),
        "session_client_id": SESSION_ID,
        "set_client_id": set_id,
        "set": fields,
    }


def squat(index: int, **extra: Any) -> dict[str, Any]:
    return {
        "exercise_id": "back_squat",
        "reps": 5,
        "load_kg": 100.0,
        "set_index": index,
        **extra,
    }


def a_workout() -> list[dict[str, Any]]:
    """Start, three sets, a correction, a delete, rating, note and finish."""
    return [
        start(1),
        upsert(2, SET_IDS[0], 5, **squat(0)),
        upsert(3, SET_IDS[1], 8, **squat(1)),
        upsert(4, SET_IDS[2], 11, **squat(2)),
        upsert(5, SET_IDS[1], 12, reps=4),
        {
            "kind": "set_delete",
            "mutation_id": mid(6),
            "client_edit_time": edit(13),
            "session_client_id": SESSION_ID,
            "set_client_id": SET_IDS[2],
        },
        {
            "kind": "session_rating",
            "mutation_id": mid(7),
            "client_edit_time": edit(50),
            "session_client_id": SESSION_ID,
            "session_rating": 7,
        },
        {
            "kind": "note_upsert",
            "mutation_id": mid(8),
            "client_edit_time": edit(51),
            "exercise_id": "back_squat",
            "text": "Belt from 100 kg",
        },
        {
            "kind": "session_finish",
            "mutation_id": mid(9),
            "client_edit_time": edit(55),
            "session_client_id": SESSION_ID,
            "end_time": edit(55),
        },
    ]


def batch(mutations: list[dict[str, Any]], version: int = 1) -> dict[str, Any]:
    return {"schema_version": version, "mutations": mutations}


async def sync(client: AsyncClient, mutations: list[dict[str, Any]]) -> Any:
    response = await client.post(SYNC, json=batch(mutations))
    assert response.status_code == 200, response.text
    return response.json()


async def snapshot(db: AsyncSession) -> dict[str, Any]:
    """Every row the sync writes, as plain values, for an equality check."""
    db.expire_all()
    sessions = (await db.execute(select(SessionRow))).scalars().all()
    sets = (
        (await db.execute(select(LoggedSetRow).order_by(LoggedSetRow.set_index)))
        .scalars()
        .all()
    )
    notes = (await db.execute(select(ExerciseNoteRow))).scalars().all()
    return {
        "sessions": [
            (
                s.id,
                s.start_time,
                s.end_time,
                s.lifecycle,
                s.session_rating,
                s.dont_learn,
            )
            for s in sessions
        ],
        "sets": [
            (s.id, s.client_id, s.set_index, s.reps, s.load_kg, s.source) for s in sets
        ],
        "notes": [(n.exercise_id, n.text) for n in notes],
    }


async def sync_audit_rows(db: AsyncSession) -> list[AuditLogEntry]:
    result = await db.execute(
        select(AuditLogEntry).where(AuditLogEntry.action.like("sync.%"))
    )
    return list(result.scalars())


# --- AC-41: a replayed batch changes nothing ----------------------------------


async def test_a_replayed_batch_stores_identical_rows_and_audits_once(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Twice the same batch: same rows, one audit row per mutation."""
    mutations = a_workout()
    first = await sync(client, mutations)
    assert [r["status"] for r in first["results"]] == ["applied"] * len(mutations)
    after_first = await snapshot(db_session)
    audit_first = await sync_audit_rows(db_session)
    total_audit = await db_session.scalar(select(func.count(AuditLogEntry.id)))

    second = await sync(client, mutations)

    assert await snapshot(db_session) == after_first
    assert [r["mutation_id"] for r in second["results"]] == [
        m["mutation_id"] for m in mutations
    ]
    assert {r["status"] for r in second["results"]} == {"already_applied"}
    audit_second = await sync_audit_rows(db_session)
    assert len(audit_second) == len(mutations)
    assert sorted(row.payload_json["mutation_id"] for row in audit_second) == sorted(
        m["mutation_id"] for m in mutations
    )
    assert {row.id for row in audit_second} == {row.id for row in audit_first}
    assert await db_session.scalar(select(func.count(AuditLogEntry.id))) == total_audit


async def test_the_batch_lands_as_the_phone_logged_it(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The stored session is what the mutations say, in their order."""
    await sync(client, a_workout())
    row = (await db_session.execute(select(SessionRow))).scalar_one()

    stored = (await client.get(f"{SESSIONS}/{row.id}")).json()
    assert stored["start_time"].startswith("2026-11-09T17:30:00")
    assert stored["duration_s"] == 55 * 60
    assert stored["session_rating"] == 7
    assert [
        (s["client_id"], s["reps"], s["set_index"], s["source"])
        for s in stored["logged_sets"]
    ] == [(SET_IDS[0], 5, 0, "app"), (SET_IDS[1], 4, 1, "app")]
    note = await db_session.get(ExerciseNoteRow, "back_squat")
    assert note is not None
    assert note.text == "Belt from 100 kg"


async def test_a_mutation_repeated_inside_one_batch_applies_once(
    client: AsyncClient,
) -> None:
    body = await sync(client, [start(1), start(1)])
    assert [r["status"] for r in body["results"]] == ["applied", "already_applied"]


async def test_an_empty_batch_returns_200_with_nothing_applied(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    body = await sync(client, [])

    assert body == {"schema_version": MAX_SCHEMA_VERSION, "results": []}
    assert await db_session.scalar(select(func.count(SessionRow.id))) == 0
    assert await sync_audit_rows(db_session) == []


async def test_a_batch_of_500_is_accepted_and_501_is_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    sets = [
        upsert(1000 + n, str(uuid.uuid7()), 1, **squat(n % 200))
        for n in range(MAX_BATCH_MUTATIONS - 1)
    ]
    full = [start(1), *sets]
    assert len(full) == MAX_BATCH_MUTATIONS

    body = await sync(client, full)
    assert len(body["results"]) == MAX_BATCH_MUTATIONS
    assert {r["status"] for r in body["results"]} == {"applied"}

    over = [*full, upsert(5000, str(uuid.uuid7()), 1, **squat(0))]
    response = await client.post(SYNC, json=batch(over))
    assert response.status_code == 422
    assert ["body", "mutations"] in [e["loc"] for e in response.json()["detail"]]


# --- AC-43: version skew, login, and a single bad item ------------------------


async def test_version_skew(client: AsyncClient, db_session: AsyncSession) -> None:
    """A newer `schema_version` is a defined 409, and nothing is applied.

    The phone reads `code` to show "Update arc to upload" and keep its queue;
    a silent retry would resend the same batch forever.
    """
    response = await client.post(
        SYNC, json=batch(a_workout(), version=MAX_SCHEMA_VERSION + 1)
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "schema_version_unsupported"
    assert body["server_schema_version"] == MAX_SCHEMA_VERSION
    assert await db_session.scalar(select(func.count(SessionRow.id))) == 0
    assert await sync_audit_rows(db_session) == []


async def test_a_huge_schema_version_is_the_same_409(client: AsyncClient) -> None:
    """Found-by-design for the fuzzer: any version above ours is the 409."""
    response = await client.post(SYNC, json=batch([], version=2**63))
    assert response.status_code == 409
    assert response.json()["code"] == "schema_version_unsupported"


async def test_without_a_login_the_batch_is_401_and_unapplied(
    anon_client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await anon_client.post(SYNC, json=batch(a_workout()))
    assert response.status_code == 401
    assert await db_session.scalar(select(func.count(SessionRow.id))) == 0


async def test_one_bad_item_is_a_422_naming_it_and_nothing_is_applied(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The batch is all or nothing; the 422 names the one item to set aside."""
    bad = upsert(3, SET_IDS[1], 8, exercise_id="no_such_lift", reps=5)
    response = await client.post(
        SYNC, json=batch([start(1), upsert(2, SET_IDS[0], 5, **squat(0)), bad])
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "mutation_rejected"
    assert body["mutation_id"] == mid(3)
    assert await db_session.scalar(select(func.count(SessionRow.id))) == 0

    # Without it, the rest drain.
    drained = await sync(client, [start(1), upsert(2, SET_IDS[0], 5, **squat(0))])
    assert {r["status"] for r in drained["results"]} == {"applied"}


async def test_a_set_for_a_session_never_started_is_422(client: AsyncClient) -> None:
    response = await client.post(
        SYNC, json=batch([upsert(2, SET_IDS[0], 5, **squat(0))])
    )

    assert response.status_code == 422
    assert response.json()["mutation_id"] == mid(2)


async def test_a_set_missing_its_reps_is_422(client: AsyncClient) -> None:
    response = await client.post(
        SYNC,
        json=batch([start(1), upsert(2, SET_IDS[0], 5, exercise_id="back_squat")]),
    )

    assert response.status_code == 422
    assert response.json()["mutation_id"] == mid(2)


async def test_a_finish_before_the_start_is_422(client: AsyncClient) -> None:
    finish = {
        "kind": "session_finish",
        "mutation_id": mid(2),
        "client_edit_time": edit(1),
        "session_client_id": SESSION_ID,
        "end_time": edit(-5),
    }
    response = await client.post(SYNC, json=batch([start(1), finish]))
    assert response.status_code == 422
    assert response.json()["mutation_id"] == mid(2)


async def test_the_phone_may_not_claim_the_coachs_source(client: AsyncClient) -> None:
    response = await client.post(
        SYNC,
        json=batch([start(1), upsert(2, SET_IDS[0], 5, **squat(0), source="coach")]),
    )
    assert response.status_code == 422
    assert response.json()["mutation_id"] == mid(2)


async def test_dont_learn_lands_with_its_reason(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    toggle = {
        "kind": "dont_learn",
        "mutation_id": mid(2),
        "client_edit_time": edit(3),
        "session_client_id": SESSION_ID,
        "dont_learn": True,
        "reason": "slept badly",
    }
    await sync(client, [start(1), toggle])
    row = (await db_session.execute(select(SessionRow))).scalar_one()
    await db_session.refresh(row)
    assert row.dont_learn is True
    assert row.dont_learn_reason == "slept badly"


# --- the contract ------------------------------------------------------------


def test_schemathesis_is_told_sync_refuses_by_design() -> None:
    """The fuzzer's narrowing for `POST /sync` admits the by-design 409 and 422."""
    config = tomllib.loads(
        (Path(__file__).resolve().parents[2] / "schemathesis.toml").read_text()
    )
    entry = next(
        row for row in config["operations"] if row.get("include-name") == f"POST {SYNC}"
    )
    statuses = entry["checks"]["positive_data_acceptance"]["expected-statuses"]
    assert {409, 422} <= set(statuses)


def test_the_409_and_422_bodies_are_in_the_contract(app: FastAPI) -> None:
    """The phone's generated types know the `code` it branches on."""
    responses = app.openapi()["paths"][SYNC]["post"]["responses"]
    schemas = app.openapi()["components"]["schemas"]

    conflict = responses["409"]["content"]["application/json"]["schema"]["$ref"]
    assert schemas[conflict.rsplit("/", 1)[1]]["properties"]["code"]["const"] == (
        "schema_version_unsupported"
    )
    rejected = responses["422"]["content"]["application/json"]["schema"]["$ref"]
    assert "mutation_id" in schemas[rejected.rsplit("/", 1)[1]]["properties"]
    # Unbounded above: a newer phone's version is the 409, not a schema 422.
    version = schemas["SyncBatch"]["properties"]["schema_version"]
    assert "maximum" not in version

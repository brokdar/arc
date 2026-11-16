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

from app.api.routes.sync import get_service
from app.domain.activity import SessionLifecycle
from app.domain.actor import Actor
from app.domain.sync import MAX_BATCH_MUTATIONS, MAX_SCHEMA_VERSION
from app.persistence.activity import LoggedSetRow, SessionRow
from app.persistence.audit import AuditLogEntry
from app.persistence.db import SessionDep
from app.persistence.exercises import ExerciseNoteRow
from app.services.sync import SyncService

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

    assert body == {
        "schema_version": MAX_SCHEMA_VERSION,
        "results": [],
        "active_sessions": [],
    }
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


async def test_a_simple_rating_lands_on_the_set_and_a_later_one_replaces_it(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The logging screen rates an exercise by patching each of its sets."""
    await sync(
        client,
        [
            start(1),
            upsert(2, SET_IDS[0], 5, **squat(0)),
            upsert(3, SET_IDS[0], 6, effort_simple="good"),
            upsert(4, SET_IDS[0], 7, effort_simple="hard"),
        ],
    )
    row = (await db_session.execute(select(SessionRow))).scalar_one()

    stored = (await client.get(f"{SESSIONS}/{row.id}")).json()
    assert [s["effort_simple"] for s in stored["logged_sets"]] == ["hard"]


# --- changing the session on the fly (AC-54, AC-55) -------------------------------

PLANNED = "/api/v1/planned-sessions"

#: Three exercises, two sets each, at loads far enough apart that pairing a
#: row set against a squat prescription would fail the load criterion.
THREE_LIFTS: dict[str, Any] = {
    "discipline": "strength",
    "groups": [
        {
            "items": [
                {
                    "exercise_id": exercise,
                    "sets": 2,
                    "reps": 5,
                    "load": {"kind": "kg", "value": load},
                }
            ]
        }
        for exercise, load in (
            ("back_squat", 100.0),
            ("bench_press", 60.0),
            ("barbell_row", 40.0),
        )
    ],
}

#: line_ref -> (exercise, load), the packet's references for THREE_LIFTS.
LINES = {
    "0.0": ("back_squat", 100.0),
    "1.0": ("bench_press", 60.0),
    "2.0": ("barbell_row", 40.0),
}


def set_id(line: str, n: int, series: str = "c") -> str:
    """A fixed set client id per line and set number; one series per session."""
    group = int(line[0])
    return f"0199a1b2-0000-7000-8000-0000000{series}{group}{n:03d}"


class Phone:
    """Builds one session's mutations the way the logging screen writes them."""

    def __init__(
        self, session_id: str, start_at: dt.datetime, first: int, series: str = "c"
    ) -> None:
        self.session_id = session_id
        self.start_at = start_at
        self.n = first
        self.series = series

    def _next(self) -> str:
        self.n += 1
        return mid(self.n)

    def at(self, minutes: int) -> str:
        return (self.start_at + dt.timedelta(minutes=minutes)).isoformat()

    def start(self, **extra: Any) -> dict[str, Any]:
        return {
            "kind": "session_start",
            "mutation_id": self._next(),
            "client_edit_time": self.at(0),
            "session_client_id": self.session_id,
            "start_time": self.start_at.isoformat(),
            "timezone": "UTC",
            **extra,
        }

    def upsert(self, set_client_id: str, minutes: int, **fields: Any) -> dict[str, Any]:
        return {
            "kind": "set_upsert",
            "mutation_id": self._next(),
            "client_edit_time": self.at(minutes),
            "session_client_id": self.session_id,
            "set_client_id": set_client_id,
            "set": fields,
        }

    def log(self, line: str, n: int, index: int, minutes: int) -> dict[str, Any]:
        exercise, load = LINES[line]
        return self.upsert(
            set_id(line, n, self.series),
            minutes,
            exercise_id=exercise,
            planned_line_ref=line,
            reps=5,
            load_kg=load,
            set_index=index,
            completed_at=self.at(minutes),
        )

    def other(self, kind: str, minutes: int, **fields: Any) -> dict[str, Any]:
        return {
            "kind": kind,
            "mutation_id": self._next(),
            "client_edit_time": self.at(minutes),
            "session_client_id": self.session_id,
            **fields,
        }

    def finish(self, minutes: int) -> dict[str, Any]:
        return self.other("session_finish", minutes, end_time=self.at(minutes))


def reordered(phone: Phone) -> list[dict[str, Any]]:
    """Two sets of exercise 1 logged, then exercise 3 moved before exercise 1
    and one set added to exercise 2 — the mutations the screen writes.

    The reorder rewrites ``set_index`` of every logged set so the order is
    the new exercise order; sets logged afterwards go where their exercise
    now stands.
    """
    return [
        phone.start(),
        phone.log("0.0", 1, 0, 5),
        phone.log("0.0", 2, 1, 8),
        # Reorder 3, 1, 2: the two squat sets move down behind the rows.
        phone.upsert(set_id("0.0", 1, phone.series), 9, set_index=2),
        phone.upsert(set_id("0.0", 2, phone.series), 9, set_index=3),
        phone.log("2.0", 1, 0, 12),
        phone.log("2.0", 2, 1, 15),
        phone.log("1.0", 1, 4, 18),
        phone.log("1.0", 2, 5, 21),
        # "Add set" on exercise 2: the clone of its last set, logged.
        phone.log("1.0", 3, 6, 24),
        phone.finish(30),
    ]


def in_order(phone: Phone) -> list[dict[str, Any]]:
    """The same work, done in the planned order."""
    return [
        phone.start(),
        phone.log("0.0", 1, 0, 5),
        phone.log("0.0", 2, 1, 8),
        phone.log("1.0", 1, 2, 11),
        phone.log("1.0", 2, 3, 14),
        phone.log("1.0", 3, 4, 17),
        phone.log("2.0", 1, 5, 20),
        phone.log("2.0", 2, 6, 23),
        phone.finish(30),
    ]


async def test_a_reordered_session_replays_in_the_new_exercise_order(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """AC-54: 3-sets, 1-sets, 2-sets plus the added set — and again on replay."""
    mutations = reordered(Phone(SESSION_ID, START, 100))
    await sync(client, mutations)
    row_id = (await db_session.execute(select(SessionRow.id))).scalar_one()

    stored = (await client.get(f"{SESSIONS}/{row_id}")).json()["logged_sets"]
    order = [(s["exercise_id"], s["client_id"], s["set_index"]) for s in stored]
    assert order == [
        ("barbell_row", set_id("2.0", 1), 0),
        ("barbell_row", set_id("2.0", 2), 1),
        ("back_squat", set_id("0.0", 1), 2),
        ("back_squat", set_id("0.0", 2), 3),
        ("bench_press", set_id("1.0", 1), 4),
        ("bench_press", set_id("1.0", 2), 5),
        ("bench_press", set_id("1.0", 3), 6),
    ]

    await sync(client, mutations)
    again = (await client.get(f"{SESSIONS}/{row_id}")).json()["logged_sets"]
    assert again == stored


async def test_moving_two_sets_to_another_exercise_keeps_their_client_ids(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """AC-54 edge: "Move sets to…" patches the sets; it never recreates them."""
    phone = Phone(SESSION_ID, START, 200)
    await sync(
        client,
        [
            phone.start(),
            phone.log("1.0", 1, 0, 5),
            phone.log("1.0", 2, 1, 8),
            phone.log("1.0", 3, 2, 11),
        ],
    )
    moved = [set_id("1.0", 2), set_id("1.0", 3)]
    await sync(
        client,
        [
            phone.upsert(
                one,
                12,
                exercise_id="barbell_row",
                planned_line_ref="2.0",
                set_index=1 + i,
            )
            for i, one in enumerate(moved)
        ],
    )
    row = (await db_session.execute(select(SessionRow))).scalar_one()

    stored = (await client.get(f"{SESSIONS}/{row.id}")).json()["logged_sets"]
    assert [
        (s["client_id"], s["exercise_id"], s["planned_line_ref"]) for s in stored
    ] == [
        (set_id("1.0", 1), "bench_press", "1.0"),
        (moved[0], "barbell_row", "2.0"),
        (moved[1], "barbell_row", "2.0"),
    ]
    assert await db_session.scalar(select(func.count(LoggedSetRow.id))) == 3


async def test_scoring_treats_a_reordered_session_as_in_order(
    client: AsyncClient,
) -> None:
    """AC-54: the score of 3-1-2 equals the score of 1-2-3 for the same work."""
    monday = dt.datetime(2026, 8, 10, 9, 0, tzinfo=dt.UTC)
    tuesday = monday + dt.timedelta(days=1)
    for day in (monday, tuesday):
        response = await client.post(
            PLANNED,
            json={
                "date": day.date().isoformat(),
                "purpose": "max_strength",
                "structure": THREE_LIFTS,
            },
        )
        assert response.status_code == 201, response.text

    straight = "0199a1b2-0000-7000-8000-00000000a0a1"
    shuffled = "0199a1b2-0000-7000-8000-00000000a0a2"
    await sync(client, in_order(Phone(straight, monday, 300)))
    # Its own set ids: a set does not move between sessions.
    await sync(client, reordered(Phone(shuffled, tuesday, 400, series="d")))

    sessions = (await client.get(SESSIONS)).json()["items"]
    by_day = {s["local_date"]: s["id"] for s in sessions}
    scores = []
    for day in (monday, tuesday):
        response = await client.get(
            f"{SESSIONS}/{by_day[day.date().isoformat()]}/score"
        )
        assert response.status_code == 200, response.text
        scores.append(response.json())

    def axes(document: dict[str, Any]) -> list[tuple[str, Any]]:
        return [(one["axis"], one["value"]) for one in document["axes"]]

    assert axes(scores[1]) == axes(scores[0])
    assert scores[1]["suggested_verdict"] == scores[0]["suggested_verdict"]
    # The in-order session lifts every prescribed load: a positional pairing of
    # the reordered one would put rows against squats and fail it.
    assert dict(axes(scores[0]))["sets_load"] == 1.0


# --- abandoned sessions close themselves (AC-55) ----------------------------------

#: The last set of AC-55's session, and the moments around three hours later.
LAST_SET = dt.datetime(2026, 11, 9, 18, 10, tzinfo=dt.UTC)


def clocked(app: FastAPI, at: dt.datetime) -> None:
    """Answer `POST /sync` as a server whose clock reads ``at``."""

    def service(session: SessionDep) -> SyncService:
        return SyncService.from_session(session, now=lambda: at)

    app.dependency_overrides[get_service] = service


async def abandoned(app: FastAPI, client: AsyncClient) -> Phone:
    """A session started 17:30 whose last set was logged, live, at 18:10."""
    phone = Phone(SESSION_ID, START, 500)
    clocked(app, LAST_SET)
    await sync(
        client,
        [
            phone.start(),
            phone.log("0.0", 1, 0, 30),
            phone.log("0.0", 2, 1, 40),
        ],
    )
    return phone


async def the_session(db: AsyncSession) -> SessionRow:
    db.expire_all()
    return (await db.execute(select(SessionRow))).scalar_one()


async def test_a_session_idle_for_three_hours_closes_at_its_last_set(
    app: FastAPI, client: AsyncClient, db_session: AsyncSession
) -> None:
    """AC-55: no activity from 18:10 to 21:11 closes it with ended_at 18:10."""
    await abandoned(app, client)
    clocked(app, dt.datetime(2026, 11, 9, 21, 11, tzinfo=dt.UTC))

    await sync(client, [])

    row = await the_session(db_session)
    assert row.lifecycle is SessionLifecycle.FINISHED
    stored = (await client.get(f"{SESSIONS}/{row.id}")).json()
    assert dt.datetime.fromisoformat(stored["end_time"]) == LAST_SET


async def test_at_two_hours_fifty_nine_it_is_still_open(
    app: FastAPI, client: AsyncClient, db_session: AsyncSession
) -> None:
    """AC-55 edge: at 21:09 the session is still the athlete's."""
    await abandoned(app, client)
    clocked(app, dt.datetime(2026, 11, 9, 21, 9, tzinfo=dt.UTC))

    await sync(client, [])

    row = await the_session(db_session)
    assert row.lifecycle is SessionLifecycle.ACTIVE


async def test_sets_uploaded_late_close_the_session_at_the_last_of_them(
    app: FastAPI, client: AsyncClient, db_session: AsyncSession
) -> None:
    """The close looks at the batch it arrives with: a phone that logged
    offline and uploads at 21:30 ends the session at its own last set."""
    phone = Phone(SESSION_ID, START, 600)
    clocked(app, dt.datetime(2026, 11, 9, 21, 30, tzinfo=dt.UTC))
    await sync(
        client,
        [phone.start(), phone.log("0.0", 1, 0, 30), phone.log("0.0", 2, 1, 40)],
    )

    row = await the_session(db_session)
    assert row.lifecycle is SessionLifecycle.FINISHED
    assert row.end_time == LAST_SET


async def test_the_scheduled_sweep_closes_an_abandoned_session(
    app: FastAPI, client: AsyncClient, db_session: AsyncSession
) -> None:
    """Nobody syncing (the phone was put away) still closes it."""
    await abandoned(app, client)

    closed = await SyncService.from_session(
        db_session, now=lambda: dt.datetime(2026, 11, 9, 21, 11, tzinfo=dt.UTC)
    ).close_abandoned(actor=Actor.system())

    assert closed == 1
    row = await the_session(db_session)
    assert row.lifecycle is SessionLifecycle.FINISHED
    assert row.end_time == LAST_SET


async def test_recap_sets_keep_their_recap_source(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """AC-55: "Log it now" → "✓ all as planned" stores `source: "recap"`."""
    phone = Phone(SESSION_ID, START, 700)
    await sync(
        client,
        [
            phone.start(),
            phone.upsert(
                set_id("0.0", 1), 1, **squat(0), planned_line_ref="0.0", source="recap"
            ),
            phone.upsert(
                set_id("0.0", 2), 1, **squat(1), planned_line_ref="0.0", source="recap"
            ),
            phone.finish(60),
        ],
    )
    row = await the_session(db_session)

    stored = (await client.get(f"{SESSIONS}/{row.id}")).json()
    assert [s["source"] for s in stored["logged_sets"]] == ["recap", "recap"]


# --- reopening and handing over -----------------------------------------------------


async def test_a_finished_session_reopens_within_24_hours(
    app: FastAPI, client: AsyncClient, db_session: AsyncSession
) -> None:
    phone = Phone(SESSION_ID, START, 800)
    await sync(client, [phone.start(), phone.log("0.0", 1, 0, 5), phone.finish(30)])
    clocked(app, START + dt.timedelta(minutes=30 + 23 * 60))

    await sync(client, [phone.other("session_reopen", 30 + 23 * 60)])

    row = await the_session(db_session)
    assert row.lifecycle is SessionLifecycle.ACTIVE


async def test_a_session_ended_over_24_hours_ago_cannot_be_reopened(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    phone = Phone(SESSION_ID, START, 900)
    await sync(client, [phone.start(), phone.log("0.0", 1, 0, 5), phone.finish(30)])
    late = phone.other("session_reopen", 30 + 24 * 60 + 1)

    response = await client.post(SYNC, json=batch([late]))

    assert response.status_code == 422
    assert response.json()["mutation_id"] == late["mutation_id"]
    row = await the_session(db_session)
    assert row.lifecycle is SessionLifecycle.FINISHED


DEVICE_A = "0199a1b2-0000-7000-8000-0000000dda01"
DEVICE_B = "0199a1b2-0000-7000-8000-0000000ddb02"


async def test_every_answer_names_the_active_sessions_and_who_holds_them(
    app: FastAPI, client: AsyncClient, db_session: AsyncSession
) -> None:
    """AC-56's server half: device B learns of A's session and claims it."""
    clocked(app, START + dt.timedelta(minutes=20))
    planned = "0199a1b2-0000-7000-8000-0000000ee001"
    phone = Phone(SESSION_ID, START, 1000)
    first = await sync(
        client,
        [phone.start(device_id=DEVICE_A, planned_session_id=planned)],
    )
    row = await the_session(db_session)
    assert first["active_sessions"] == [
        {
            "session_client_id": SESSION_ID,
            "session_id": str(row.id),
            "planned_session_id": planned,
            "device_id": DEVICE_A,
            "start_time": START.isoformat().replace("+00:00", "Z"),
        }
    ]

    claimed = await sync(client, [phone.other("session_claim", 20, device_id=DEVICE_B)])
    assert [s["device_id"] for s in claimed["active_sessions"]] == [DEVICE_B]

    # Device A's next sync — nothing to send — reads that it lost the session.
    again = await sync(client, [])
    assert again["active_sessions"][0]["device_id"] == DEVICE_B

    finished = await sync(client, [phone.finish(30)])
    assert finished["active_sessions"] == []

"""Pain notes and red flags in the gym (AC-59, AC-60), and the rules behind them.

Everything the phone does arrives through `POST /sync`, so the API tests drive
that endpoint with the mutations the logging screen writes, and read the
results back where the athlete and the coach read them: the stored set
through `GET /sessions/{id}`, the cards through `GET /cards` and the `cards`
table, the block on increases through the session packet, the flag through
`GET /athlete`, the trail from `audit_log`.

"Removes sets 3–4 from today" is the phone's decision — it is in a basement
with no signal when it makes it — taken by the rule here
(`app.domain.safety.sets_left_today`) over the sets the server stored, which
the logging screen mirrors (`frontend/components/strength/safety/`).
"""

import datetime as dt
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import athlete_today
from app.domain.activity import PainKind, SessionLifecycle
from app.domain.athlete import RedFlagKind, RedFlagSeverity
from app.domain.cards import CardPriority
from app.domain.safety import (
    PHYSIO_LINE,
    RED_FLAG_FOLLOW_UP,
    RED_FLAG_GROUP,
    RED_FLAG_TEXT,
    SWAP_OFFER,
    RedFlagSymptom,
    SafetyAction,
    blocks_increase,
    card_allowed,
    pain_card,
    plural,
    precedence,
    sets_left_today,
    swap_for,
)
from app.persistence.activity import SessionRow
from app.persistence.audit import AuditLogEntry
from app.persistence.cards import CardRow
from app.services.exercises import ExerciseService
from app.services.templates import load_exercise_catalogue

SYNC = "/api/v1/sync"
SESSIONS = "/api/v1/sessions"
CARDS = "/api/v1/cards"
ATHLETE = "/api/v1/athlete"
PACKET = "/api/v1/strength/packet"
PLANS = "/api/v1/plans"
PLANNED = "/api/v1/planned-sessions"
SETTINGS = "/api/v1/settings/training"

START = dt.datetime(2026, 11, 9, 17, 30, tzinfo=dt.UTC)
SESSION_ID = "0199a1b2-0000-7000-8000-00000000a001"
SET_IDS = [f"0199a1b2-0000-7000-8000-00000000b00{n}" for n in range(1, 5)]
LUNGE = "forward_lunge"
PRESCRIBED_SETS = 4

HEADLINE = "Knee pain on lunges: try step-ups next time?"


def mid(n: int) -> str:
    return f"0199a1b2-0000-7000-8000-{n:012d}"


def edit(minutes: int) -> str:
    return (START + dt.timedelta(minutes=minutes)).isoformat()


def start() -> dict[str, Any]:
    return {
        "kind": "session_start",
        "mutation_id": mid(1),
        "client_edit_time": edit(0),
        "session_client_id": SESSION_ID,
        "start_time": START.isoformat(),
        "timezone": "Europe/Zurich",
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


def lunge(index: int) -> dict[str, Any]:
    return {
        "exercise_id": LUNGE,
        "reps": 8,
        "load_kg": 12.0,
        "per_side": True,
        "planned_line_ref": "0.0",
        "set_index": index,
    }


def red_flag(n: int, symptom: str, minutes: int) -> dict[str, Any]:
    return {
        "kind": "red_flag",
        "mutation_id": mid(n),
        "client_edit_time": edit(minutes),
        "session_client_id": SESSION_ID,
        "symptom": symptom,
    }


def finish(n: int, minutes: int) -> dict[str, Any]:
    return {
        "kind": "session_finish",
        "mutation_id": mid(n),
        "client_edit_time": edit(minutes),
        "session_client_id": SESSION_ID,
        "end_time": edit(minutes),
    }


async def sync(client: AsyncClient, *mutations: dict[str, Any]) -> None:
    response = await client.post(
        SYNC, json={"schema_version": 1, "mutations": list(mutations)}
    )
    assert response.status_code == 200, response.text


async def two_lunge_sets(client: AsyncClient) -> None:
    """Start, then sets 1 and 2 of the four prescribed lunges."""
    await sync(
        client,
        start(),
        upsert(2, SET_IDS[0], 5, **lunge(0)),
        upsert(3, SET_IDS[1], 8, **lunge(1)),
    )


async def report_pain(
    client: AsyncClient, kind: str | None, band: str | None, *, n: int = 4
) -> None:
    """The ⋯ → Pain note on set 2 (or its undo, with ``None``)."""
    await sync(client, upsert(n, SET_IDS[1], 9 + n, pain_kind=kind, pain_band=band))


async def stored_sets(client: AsyncClient) -> list[dict[str, Any]]:
    """The session's sets as `GET /sessions/{id}` returns them."""
    listing = await client.get(SESSIONS)
    assert listing.status_code == 200, listing.text
    (summary,) = listing.json()["items"]
    detail = await client.get(f"{SESSIONS}/{summary['id']}")
    assert detail.status_code == 200, detail.text
    return detail.json()["logged_sets"]


def left_today(sets: list[dict[str, Any]]) -> int:
    """`sets_left_today` over the stored working sets of the lunge line."""
    pain = [
        None if s["pain_kind"] is None else PainKind(s["pain_kind"])
        for s in sets
        if s["exercise_id"] == LUNGE and s["set_kind"] == "working"
    ]
    return sets_left_today(PRESCRIBED_SETS, pain)


async def card_rows(db_session: AsyncSession) -> list[CardRow]:
    db_session.expire_all()
    return list((await db_session.execute(select(CardRow))).scalars())


async def lunge_numbers(client: AsyncClient) -> dict[str, Any]:
    body = (await client.get(PACKET)).json()
    (numbers,) = [n for n in body["last_numbers"] if n["exercise_id"] == LUNGE]
    return numbers


async def audit(db_session: AsyncSession, action: str) -> list[AuditLogEntry]:
    result = await db_session.execute(
        select(AuditLogEntry).where(AuditLogEntry.action == action)
    )
    return list(result.scalars())


# --- the pure rules ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("red_flag_up", "sharp", "follow", "expected"),
    [
        (True, True, True, SafetyAction.STOP_SESSION),
        (True, False, False, SafetyAction.STOP_SESSION),
        (False, True, True, SafetyAction.STOP_EXERCISE),
        (False, True, False, SafetyAction.STOP_EXERCISE),
        (False, False, True, SafetyAction.FOLLOW_PLAN),
        (False, False, False, SafetyAction.NORMAL),
    ],
)
def test_precedence_is_red_flag_then_sharp_pain_then_follow_exactly(
    red_flag_up: bool, sharp: bool, follow: bool, expected: SafetyAction
) -> None:
    assert (
        precedence(red_flag=red_flag_up, sharp_pain_today=sharp, follow_exactly=follow)
        is expected
    )


def test_sharp_pain_after_set_two_of_four_leaves_no_sets_today() -> None:
    assert sets_left_today(4, [None, PainKind.SHARP]) == 0
    assert sets_left_today(4, [None, PainKind.ACHE]) == 2
    assert sets_left_today(4, [None, None]) == 2
    assert blocks_increase([None, PainKind.SHARP]) is True
    assert blocks_increase([PainKind.ACHE, None]) is False


def test_the_swap_for_lunges_spares_the_knee_and_the_card_says_so() -> None:
    catalogue = {exercise.id: exercise for exercise in load_exercise_catalogue()}
    swap = swap_for(catalogue[LUNGE], catalogue.values())

    assert swap is not None
    assert swap.id == "step_up"
    headline, because = pain_card(catalogue[LUNGE], swap)
    assert headline == HEADLINE
    assert because.endswith(PHYSIO_LINE)
    assert PHYSIO_LINE == "If pain continues, ask a physio or doctor."


def test_a_movement_with_nothing_to_swap_still_gets_a_card() -> None:
    catalogue = {exercise.id: exercise for exercise in load_exercise_catalogue()}
    headline, because = pain_card(catalogue[LUNGE], None)

    assert headline == "Knee pain on lunges: pick a different exercise next time?"
    assert because.endswith(PHYSIO_LINE)


@pytest.mark.parametrize(
    ("name", "expected"),
    [("Step-Up", "step-ups"), ("Bench Press", "bench presses"), ("Fly", "flies")],
)
def test_names_read_as_plurals(name: str, expected: str) -> None:
    assert plural(name) == expected


def test_the_two_red_flag_groups_carry_their_own_text() -> None:
    assert RED_FLAG_GROUP[RedFlagSymptom.CHEST_PAIN] is RedFlagKind.EMERGENCY
    assert RED_FLAG_GROUP[RedFlagSymptom.NUMBNESS] is RedFlagKind.ADVICE
    assert RED_FLAG_TEXT[RedFlagKind.EMERGENCY] == "Stop. Call emergency services now."
    assert (
        RED_FLAG_TEXT[RedFlagKind.ADVICE]
        == "Stop training and get medical advice today."
    )


def test_a_red_flag_lets_only_its_follow_up_through() -> None:
    assert card_allowed(RED_FLAG_FOLLOW_UP, red_flag_active=True)
    assert not card_allowed(SWAP_OFFER, red_flag_active=True)
    assert card_allowed(SWAP_OFFER, red_flag_active=False)
    assert not card_allowed(RED_FLAG_FOLLOW_UP, red_flag_active=False)


# --- AC-59: pain notes ------------------------------------------------------------


async def test_sharp_pain_on_lunges_stops_today_stores_the_note_and_queues_one_card(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await two_lunge_sets(client)

    await report_pain(client, "sharp", "4-5")

    sets = await stored_sets(client)
    by_id = {s["client_id"]: s for s in sets}
    assert by_id[SET_IDS[1]]["pain_kind"] == "sharp"
    assert by_id[SET_IDS[1]]["pain_band"] == "4-5"
    assert by_id[SET_IDS[0]]["pain_kind"] is None
    # Sets 3 and 4 are no longer to be done today.
    assert left_today(sets) == 0

    (card,) = await card_rows(db_session)
    assert card.kind == SWAP_OFFER
    assert card.priority == CardPriority.SAFETY
    assert card.headline == HEADLINE
    assert card.because.endswith("If pain continues, ask a physio or doctor.")
    assert card.payload["exercise_id"] == LUNGE
    assert card.payload["swap_exercise_id"] == "step_up"
    assert card.payload["set_client_id"] == SET_IDS[1]

    # Blocked from increases: the packet says so for the next session.
    assert (await lunge_numbers(client))["increase_blocked"] is True


async def test_muscle_ache_changes_nothing_but_the_stored_note(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await two_lunge_sets(client)

    await report_pain(client, "ache", "4-5")

    sets = await stored_sets(client)
    (second,) = [s for s in sets if s["client_id"] == SET_IDS[1]]
    assert (second["pain_kind"], second["pain_band"]) == ("ache", "4-5")
    assert left_today(sets) == 2
    assert await card_rows(db_session) == []
    assert (await lunge_numbers(client))["increase_blocked"] is False


async def test_undoing_the_pain_note_restores_the_sets_and_deletes_the_card(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await two_lunge_sets(client)
    await report_pain(client, "sharp", "4-5")
    assert len(await card_rows(db_session)) == 1

    await report_pain(client, None, None, n=5)

    sets = await stored_sets(client)
    (second,) = [s for s in sets if s["client_id"] == SET_IDS[1]]
    assert (second["pain_kind"], second["pain_band"]) == (None, None)
    assert left_today(sets) == 2
    assert await card_rows(db_session) == []
    assert (await lunge_numbers(client))["increase_blocked"] is False
    assert len(await audit(db_session, "card.withdrawn")) == 1


async def test_deleting_the_painful_set_also_withdraws_its_card(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await two_lunge_sets(client)
    await report_pain(client, "sharp", "6+")

    await sync(
        client,
        {
            "kind": "set_delete",
            "mutation_id": mid(9),
            "client_edit_time": edit(20),
            "session_client_id": SESSION_ID,
            "set_client_id": SET_IDS[1],
        },
    )

    assert await card_rows(db_session) == []


async def test_a_replayed_pain_note_queues_no_second_card(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await two_lunge_sets(client)
    await report_pain(client, "sharp", "4-5")
    # The same set re-asserted sharp with a new band: still one card.
    await report_pain(client, "sharp", "6+", n=6)

    assert len(await card_rows(db_session)) == 1


async def test_sharp_pain_leaves_a_follow_exactly_line_unchanged(
    client: AsyncClient,
) -> None:
    today = athlete_today()
    plan = await client.post(
        PLANS,
        json={
            "name": "Physio block",
            "phase": "base",
            "follow_exactly": True,
            "starts_on": today.isoformat(),
            "ends_before": (today + dt.timedelta(days=14)).isoformat(),
        },
    )
    assert plan.status_code == 201, plan.text
    line = {
        "exercise_id": LUNGE,
        "sets": PRESCRIBED_SETS,
        "reps": 8,
        "per_side": True,
        "load": {"kind": "kg", "value": 12},
    }
    created = await client.post(
        PLANNED,
        json={
            "date": today.isoformat(),
            "purpose": "max_strength",
            "structure": {"discipline": "strength", "groups": [{"items": [line]}]},
            "success_criteria": [],
        },
    )
    assert created.status_code == 201, created.text
    planned_id = created.json()["id"]
    before = (await client.get(f"{PLANNED}/{planned_id}")).json()

    await two_lunge_sets(client)
    await report_pain(client, "sharp", "4-5")

    after = (await client.get(f"{PLANNED}/{planned_id}")).json()
    assert after["intent"] == before["intent"]
    assert after["intent_versions"] == before["intent_versions"] == 1
    (session,) = (await client.get(PACKET)).json()["sessions"]
    assert session["follow_exactly"] is True
    (packet_line,) = session["lines"]
    assert packet_line["prescription"]["sets"] == PRESCRIBED_SETS
    # Today stops anyway: sharp pain outranks follow-exactly.
    assert (
        precedence(red_flag=False, sharp_pain_today=True, follow_exactly=True)
        is SafetyAction.STOP_EXERCISE
    )
    assert left_today(await stored_sets(client)) == 0


# --- AC-60: red flags -------------------------------------------------------------


async def visible(client: AsyncClient) -> list[dict[str, Any]]:
    response = await client.get(CARDS)
    assert response.status_code == 200, response.text
    return response.json()


async def test_chest_pain_raises_an_emergency_flag_ends_the_session_and_hides_cards(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await two_lunge_sets(client)
    # A swap offer queued first, same priority: only the flag can hide it.
    await report_pain(client, "sharp", "4-5")

    await sync(client, red_flag(10, "chest_pain", 15), finish(11, 15))

    athlete = (await client.get(ATHLETE)).json()
    assert athlete["red_flag_active"] is True
    assert athlete["red_flag_kind"] == "emergency"
    assert athlete["red_flag_severity"] == RedFlagSeverity.SEVERE.value
    db_session.expire_all()
    lifecycles = (await db_session.execute(select(SessionRow.lifecycle))).scalars()
    assert list(lifecycles) == [SessionLifecycle.FINISHED]

    for _ in range(2):
        cards = await visible(client)
        assert [c["kind"] for c in cards] == [RED_FLAG_FOLLOW_UP]
    assert cards[0]["priority"] == CardPriority.SAFETY
    assert "Stop. Call emergency services now." in cards[0]["because"]


async def test_numbness_raises_an_advice_flag(client: AsyncClient) -> None:
    await sync(client, start(), red_flag(2, "numbness", 3), finish(3, 3))

    athlete = (await client.get(ATHLETE)).json()
    assert athlete["red_flag_kind"] == "advice"
    assert athlete["red_flag_severity"] == RedFlagSeverity.MODERATE.value
    (card,) = await visible(client)
    assert card["kind"] == RED_FLAG_FOLLOW_UP
    assert "Stop training and get medical advice today." in card["because"]


async def test_resolving_the_flag_from_its_card_writes_audit_and_reenables_cards(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await two_lunge_sets(client)
    await report_pain(client, "sharp", "4-5")
    await sync(client, red_flag(10, "chest_pain", 15), finish(11, 15))
    (follow_up,) = await visible(client)

    response = await client.post(
        f"{CARDS}/{follow_up['id']}/act", json={"action": "accept"}
    )

    assert response.status_code == 200, response.text
    (entry,) = await audit(db_session, "athlete.red_flag_resolved")
    assert entry.payload_json["kind"] == "emergency"
    athlete = (await client.get(ATHLETE)).json()
    assert athlete["red_flag_active"] is False
    assert athlete["red_flag_kind"] is None
    (card,) = await visible(client)
    assert card["kind"] == SWAP_OFFER
    assert card["headline"] == HEADLINE


async def test_lowering_the_flag_on_the_profile_closes_the_follow_up(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await two_lunge_sets(client)
    await report_pain(client, "sharp", "4-5")
    await sync(client, red_flag(10, "numbness", 15), finish(11, 15))

    response = await client.patch(ATHLETE, json={"red_flag_active": False})

    assert response.status_code == 200, response.text
    assert len(await audit(db_session, "athlete.updated")) >= 1
    (card,) = await visible(client)
    assert card["kind"] == SWAP_OFFER
    closed = [
        row for row in await card_rows(db_session) if row.kind == RED_FLAG_FOLLOW_UP
    ]
    assert [row.state for row in closed] == ["dismissed"]


async def test_an_unknown_symptom_is_refused(client: AsyncClient) -> None:
    response = await client.post(
        SYNC,
        json={
            "schema_version": 1,
            "mutations": [start(), red_flag(2, "headache_maybe", 3)],
        },
    )
    assert response.status_code == 422


async def test_a_flag_raised_on_the_profile_without_a_kind_is_advice(
    client: AsyncClient,
) -> None:
    response = await client.patch(
        ATHLETE, json={"red_flag_active": True, "red_flag_severity": "mild"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["red_flag_kind"] == "advice"
    lowered = await client.patch(ATHLETE, json={"red_flag_active": False})
    assert lowered.json()["red_flag_kind"] is None


# --- AC-61's server half: the under-18 flag ----------------------------------------


async def test_the_under_18_answer_is_stored_as_no_max_efforts(
    client: AsyncClient,
) -> None:
    assert (await client.get(SETTINGS)).json()["no_max_efforts"] is False

    response = await client.patch(
        SETTINGS, json={"before_you_start_seen": True, "no_max_efforts": True}
    )

    assert response.status_code == 200, response.text
    stored = (await client.get(SETTINGS)).json()
    assert stored["no_max_efforts"] is True
    assert stored["before_you_start_seen"] is True


async def test_the_catalogue_is_seeded_for_these_tests(
    db_session: AsyncSession,
) -> None:
    catalogue = await ExerciseService.from_session(db_session).catalogue()
    assert {LUNGE, "step_up"} <= set(catalogue)

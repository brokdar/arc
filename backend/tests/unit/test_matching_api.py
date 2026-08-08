"""WP-6's case table, through HTTP, plus the two things HTTP cannot reach.

The build plan names seven cases and this file is them: done a day late, two
planned and one done, swap, an unplanned group ride, two files merged into one
session, a deliberate low-similarity displacement, and a confirmed link
surviving a re-match. Around them sit the guarantees the cases depend on —
unlink restores exactly, every mutation is audited — and the missed sweep,
which is driven directly because it is a scheduler job with no endpoint.

**Every session here is real.** The controlled ones are typed in
(`POST /manual-sessions`), which gives a session an exact duration and no
stream — so the score is the duration term alone and the case table can name
the band it lands in. The merge case uses two GPX files through the actual
pipeline, because what it is about is two recordings on one grid, and rows
inserted by hand would not have any.
"""

import datetime as dt
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.actor import Actor
from app.domain.matching import EveningPromptStatus, MatchLinkStatus
from app.domain.plan import PlanState
from app.domain.sessions import SessionStatus
from app.persistence.audit import AuditLogEntry
from app.persistence.matching import EveningPromptRow
from app.services.matching import MatchingService
from tests.unit.activity_files import gpx_document

ANCHORS = "/api/v1/anchors"
PLANNED = "/api/v1/planned-sessions"
SESSIONS = "/api/v1/sessions"
MANUAL = "/api/v1/manual-sessions"
MATCHES = "/api/v1/matches"
UPLOAD = "/api/v1/ingest/upload"
ATHLETE = "/api/v1/athlete"
WEEK = "/api/v1/plan/week"

#: A Monday.
MONDAY = dt.date(2026, 8, 10)
TUESDAY = dt.date(2026, 8, 11)
WEDNESDAY = dt.date(2026, 8, 12)
THURSDAY = dt.date(2026, 8, 13)

#: 600 + 3 x (480 + 240) — the same prescription the week tests use.
RIDE_DURATION_S = 2_760

RIDE: dict[str, Any] = {
    "discipline": "cycling",
    "steps": [
        {"kind": "steady", "duration_s": 600, "role": "warmup"},
        {
            "kind": "repeat",
            "times": 3,
            "children": [
                {
                    "kind": "steady",
                    "duration_s": 480,
                    "role": "work",
                    "targets": {
                        "power": {
                            "kind": "percent_of_anchor",
                            "anchor_type": "ftp",
                            "pct_low": 0.88,
                            "pct_high": 0.93,
                        }
                    },
                },
                {"kind": "steady", "duration_s": 240, "role": "recovery"},
            ],
        },
    ],
}

#: A short ride: 600 s of work and nothing else.
SHORT_RIDE: dict[str, Any] = {
    "discipline": "cycling",
    "steps": [{"kind": "steady", "duration_s": 600, "role": "work"}],
}

LIFT: dict[str, Any] = {
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


# --- helpers -------------------------------------------------------------------


async def append_ftp(client: AsyncClient, value: float = 250) -> str:
    """Append an FTP anchor so a percentage prescription can be pinned."""
    response = await client.post(
        ANCHORS,
        json={"anchor_type": "ftp", "value": value, "provenance": "estimated"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def plan(
    client: AsyncClient,
    date: dt.date,
    *,
    structure: dict[str, Any] | None = None,
    purpose: str = "sweet_spot",
    **overrides: Any,
) -> dict[str, Any]:
    """Plan a session, asserting it was accepted."""
    payload: dict[str, Any] = {
        "date": date.isoformat(),
        "purpose": purpose,
        "structure": structure or RIDE,
    } | overrides
    response = await client.post(PLANNED, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def record(
    client: AsyncClient,
    date: dt.date,
    *,
    duration_s: int,
    discipline: str = "cycling",
    sets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Type in a session on ``date``, asserting it was accepted.

    A typed-in session has no stream, so its similarity is the duration term
    alone (endurance) or the set count alone (strength) — which is what lets a
    case name the band it means.
    """
    response = await client.post(
        MANUAL,
        json={
            "start_time": f"{date.isoformat()}T09:00:00+00:00",
            "timezone": "UTC",
            "duration_s": duration_s,
            "discipline": discipline,
            "sets": sets or [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def upload_gpx(client: AsyncClient, name: str, document: str) -> str:
    """Ingest one GPX document and return the session it created."""
    response = await client.post(
        UPLOAD,
        files={"file": (name, document.encode(), "application/gpx+xml")},
    )
    assert response.status_code == 200, response.text
    [session_id] = response.json()["session_ids"]
    return session_id


async def get_planned(client: AsyncClient, planned_id: str) -> dict[str, Any]:
    """One planned session."""
    response = await client.get(f"{PLANNED}/{planned_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def get_session(client: AsyncClient, session_id: str) -> dict[str, Any]:
    """One completed session."""
    response = await client.get(f"{SESSIONS}/{session_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def inbox(client: AsyncClient, status: str = "pending") -> list[dict[str, Any]]:
    """The proposals waiting on the athlete."""
    response = await client.get(MATCHES, params={"status": status})
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def actions(session: AsyncSession) -> list[str]:
    """Every audit action so far, oldest first."""
    result = await session.execute(
        select(AuditLogEntry).order_by(AuditLogEntry.at.asc(), AuditLogEntry.id.asc())
    )
    return [entry.action for entry in result.scalars()]


# --- the case table -------------------------------------------------------------


async def test_done_a_day_late_matches_the_session_it_answers(
    client: AsyncClient,
) -> None:
    """Case 1. The window is ±1 day, so yesterday's session is still claimable."""
    await append_ftp(client)
    planned = await plan(client, MONDAY)

    done = await record(client, TUESDAY, duration_s=RIDE_DURATION_S)

    assert done["status"] == "matched"
    link = done["match"]
    assert link is not None
    assert link["planned_session_id"] == planned["id"]
    assert link["status"] == MatchLinkStatus.AUTO_HIGH.value
    assert link["similarity"] == pytest.approx(1.0)
    # The planned side moved with it, and says so on its own resource.
    after = await get_planned(client, planned["id"])
    assert after["status"] == SessionStatus.COMPLETED.value
    assert after["match"]["session_id"] == done["id"]


async def test_two_days_late_is_outside_the_window(client: AsyncClient) -> None:
    """The window's far edge, which is what makes case 1 a rule and not luck."""
    await append_ftp(client)
    planned = await plan(client, MONDAY)

    done = await record(client, WEDNESDAY, duration_s=RIDE_DURATION_S)

    assert done["status"] == "unplanned"
    assert done["match"] is None
    assert (await get_planned(client, planned["id"]))["status"] == "planned"


async def test_two_planned_one_done_gives_the_link_to_the_better_candidate(
    client: AsyncClient,
) -> None:
    """Case 2. The other session stays open for whatever answers it."""
    await append_ftp(client)
    long_ride = await plan(client, MONDAY)
    short_ride = await plan(client, MONDAY, structure=SHORT_RIDE, purpose="recovery")

    done = await record(client, MONDAY, duration_s=RIDE_DURATION_S)

    assert done["match"]["planned_session_id"] == long_ride["id"]
    assert (await get_planned(client, long_ride["id"]))["status"] == "completed"
    # Untouched, unlinked, and therefore still a candidate for the next ride.
    still_open = await get_planned(client, short_ride["id"])
    assert still_open["status"] == "planned"
    assert still_open["match"] is None

    # ...which the next ride proves by taking it.
    second = await record(client, MONDAY, duration_s=600)
    assert second["match"]["planned_session_id"] == short_ride["id"]


async def test_a_swap_retargets_the_link_and_restores_what_it_left(
    client: AsyncClient,
) -> None:
    """Case 3."""
    await append_ftp(client)
    wrong = await plan(client, MONDAY)
    right = await plan(client, TUESDAY)
    done = await record(client, MONDAY, duration_s=RIDE_DURATION_S)
    link_id = done["match"]["id"]
    assert done["match"]["planned_session_id"] == wrong["id"]

    response = await client.patch(
        f"{MATCHES}/{link_id}", json={"planned_session_id": right["id"]}
    )
    assert response.status_code == 200, response.text
    swapped = response.json()

    assert swapped["id"] == link_id
    assert swapped["planned_session_id"] == right["id"]
    # A retarget is the athlete's, so the link is confirmed and sticky.
    assert swapped["status"] == MatchLinkStatus.CONFIRMED.value
    assert swapped["confirmed_at"] is not None
    assert (await get_planned(client, right["id"]))["status"] == "completed"
    # Exactly what it was before the link existed, not a guess at the default.
    assert (await get_planned(client, wrong["id"]))["status"] == "planned"


async def test_an_unplanned_group_ride_stands_as_its_own_thing(
    client: AsyncClient,
) -> None:
    """Case 4. Below the floor nothing is proposed — and nothing is guessed."""
    await append_ftp(client)
    planned = await plan(client, MONDAY)

    # 600 s against 2 760 s prescribed: a duration ratio of 0.217, under the
    # 0.4 proposal floor.
    done = await record(client, MONDAY, duration_s=600)

    assert done["status"] == "unplanned"
    assert done["match"] is None
    assert await inbox(client) == []
    assert (await get_planned(client, planned["id"]))["status"] == "planned"


async def test_merging_two_files_makes_one_session_over_one_joined_grid(
    data_root: Path, client: AsyncClient
) -> None:
    """Case 5. Both recordings are kept; the numbers describe the whole ride."""
    first_start = dt.datetime(2026, 8, 10, 6, 0, tzinfo=dt.UTC)
    survivor_id = await upload_gpx(client, "first.gpx", gpx_document(start=first_start))
    absorbed_id = await upload_gpx(
        client,
        "second.gpx",
        gpx_document(start=first_start + dt.timedelta(minutes=20)),
    )
    before = await get_session(client, survivor_id)
    assert len(before["recordings"]) == 1
    half_the_ride = before["recording_time_s"]

    response = await client.post(
        f"{SESSIONS}/{survivor_id}/merge", json={"absorbed_session_id": absorbed_id}
    )
    assert response.status_code == 200, response.text
    merged = response.json()

    # Both recordings, one session, and the absorbed row is gone.
    assert len(merged["recordings"]) == 2
    assert merged["recording_time_s"] == pytest.approx(2 * half_the_ride)
    assert (await client.get(f"{SESSIONS}/{absorbed_id}")).status_code == 404
    # The metrics were recomputed over the join: a second version, over twice
    # the recording time.
    assert merged["metrics"]["version"] == 2
    assert merged["metrics"]["recording_time_s"] == pytest.approx(2 * half_the_ride)

    # And the chart payload is the joined grid, with the garage-door gap left
    # unrecorded rather than filled in.
    streams = (await client.get(f"{SESSIONS}/{survivor_id}/streams")).json()
    assert len(streams["recording_ids"]) == 2
    assert streams["recording_id"] == streams["recording_ids"][0]
    assert streams["length"] > 2 * before["recordings"][0]["elapsed_time_s"]
    gap = next(
        stop
        for stop in streams["recording_stops"]
        if stop["end_index"] - stop["start_index"] > 60
    )
    assert gap["start_index"] < gap["end_index"] <= streams["length"]
    power = next(
        channel for channel in streams["channels"] if channel["channel"] == "power"
    )
    assert power["values"][gap["start_index"] + 1] is None


async def test_a_low_similarity_link_can_be_made_deliberately_as_displaced(
    client: AsyncClient,
) -> None:
    """Case 6. Not missed, not completed: the athlete trained, just not this."""
    await append_ftp(client)
    planned = await plan(client, MONDAY)
    done = await record(client, MONDAY, duration_s=600)
    assert done["match"] is None  # too dissimilar to be proposed

    response = await client.post(
        MATCHES,
        json={
            "session_id": done["id"],
            "planned_session_id": planned["id"],
            "displaced": True,
        },
    )
    assert response.status_code == 201, response.text
    link = response.json()

    assert link["status"] == MatchLinkStatus.DISPLACED.value
    # The score is stored however low it is: a deliberate link at 0.22 is worth
    # being able to look at afterwards.
    assert link["similarity"] == pytest.approx(600 / RIDE_DURATION_S, abs=0.01)
    assert (await get_planned(client, planned["id"]))["status"] == "displaced"
    assert (await get_session(client, done["id"]))["status"] == "displaced"


async def test_a_confirmed_link_survives_every_rematch(client: AsyncClient) -> None:
    """Case 7. The stickiness rule, from both ends.

    A better candidate appearing afterwards must not take the link, and the
    re-match must say it decided nothing rather than quietly agreeing.
    """
    await append_ftp(client)
    chosen = await plan(client, MONDAY, structure=SHORT_RIDE, purpose="recovery")
    done = await record(client, MONDAY, duration_s=RIDE_DURATION_S)
    # Score against the short prescription is 0.22, so nothing was proposed.
    assert done["match"] is None
    made = (
        await client.post(
            MATCHES,
            json={"session_id": done["id"], "planned_session_id": chosen["id"]},
        )
    ).json()
    assert made["status"] == MatchLinkStatus.CONFIRMED.value

    # A perfect candidate now exists for the same day.
    better = await plan(client, MONDAY)
    response = await client.post(f"{SESSIONS}/{done['id']}/rematch")
    assert response.status_code == 200, response.text
    outcome = response.json()

    assert outcome["sticky"] is True
    assert outcome["candidates"] == 0
    assert outcome["match"]["id"] == made["id"]
    assert outcome["match"]["planned_session_id"] == chosen["id"]
    assert (await get_planned(client, better["id"]))["status"] == "planned"


# --- the guarantees the cases rest on -------------------------------------------


async def test_unlinking_restores_both_sides_exactly(client: AsyncClient) -> None:
    """WP-6.8. Exactly, not approximately — a `missed` session goes back to it."""
    await append_ftp(client)
    planned = await plan(client, MONDAY)
    # Marked missed by hand, standing in for the sweep having got there first.
    assert (
        await client.patch(f"{PLANNED}/{planned['id']}", json={"status": "missed"})
    ).status_code == 200
    done = await record(client, MONDAY, duration_s=RIDE_DURATION_S)
    link = done["match"]
    assert link["status"] == MatchLinkStatus.AUTO_HIGH.value
    assert (await get_planned(client, planned["id"]))["status"] == "completed"

    response = await client.delete(f"{MATCHES}/{link['id']}")
    assert response.status_code == 200, response.text
    state = response.json()

    assert state["match"] is None
    # Back to `missed`, which is where the link found it. `planned` here would
    # be a plausible default and the wrong answer.
    assert (await get_planned(client, planned["id"]))["status"] == "missed"
    assert (await get_session(client, done["id"]))["status"] == "unmatched"
    assert state["status"] == "unmatched"
    assert (await client.get(f"{MATCHES}/{link['id']}")).status_code == 404


async def test_confirming_a_proposal_moves_both_sides(client: AsyncClient) -> None:
    """The pending band: a proposal changes nothing until it is answered (D140)."""
    await append_ftp(client)
    planned = await plan(client, MONDAY)

    # 1 500 s against 2 760 s prescribed: 0.54, inside the proposal band.
    done = await record(client, MONDAY, duration_s=1_500)

    assert done["match"]["status"] == MatchLinkStatus.PENDING.value
    # Nothing has moved: the machine asked a question, it did not answer one.
    assert done["status"] == "unmatched"
    assert (await get_planned(client, planned["id"]))["status"] == "planned"
    # And the inbox can render the row without fetching either side.
    [proposal] = await inbox(client)
    assert proposal["session"]["local_date"] == MONDAY.isoformat()
    assert proposal["planned_session"]["purpose"] == "sweet_spot"
    assert proposal["planned_session"]["date"] == MONDAY.isoformat()

    confirmed = (await client.post(f"{MATCHES}/{done['match']['id']}/confirm")).json()

    assert confirmed["status"] == MatchLinkStatus.CONFIRMED.value
    assert confirmed["confirmed_at"] is not None
    assert (await get_session(client, done["id"]))["status"] == "matched"
    assert (await get_planned(client, planned["id"]))["status"] == "completed"
    assert await inbox(client) == []


async def test_rejecting_a_proposal_leaves_the_ride_unplanned(
    client: AsyncClient,
) -> None:
    await append_ftp(client)
    planned = await plan(client, MONDAY)
    done = await record(client, MONDAY, duration_s=1_500)

    response = await client.post(f"{MATCHES}/{done['match']['id']}/reject")
    assert response.status_code == 200, response.text

    assert response.json()["status"] == "unplanned"
    assert (await get_session(client, done["id"]))["match"] is None
    # The planned session goes back to being open for something else.
    assert (await get_planned(client, planned["id"]))["status"] == "planned"


async def test_marking_a_session_unplanned_drops_an_open_proposal(
    client: AsyncClient,
) -> None:
    await append_ftp(client)
    planned = await plan(client, MONDAY)
    done = await record(client, MONDAY, duration_s=1_500)
    assert done["match"]["status"] == MatchLinkStatus.PENDING.value

    response = await client.post(f"{SESSIONS}/{done['id']}/unplanned")
    assert response.status_code == 200, response.text

    assert response.json()["status"] == "unplanned"
    assert (await get_planned(client, planned["id"]))["status"] == "planned"
    assert await inbox(client) == []


async def test_a_confirmed_link_is_unlinked_never_rejected_or_overruled(
    client: AsyncClient,
) -> None:
    """Two contradictory statements are refused rather than silently ordered."""
    await append_ftp(client)
    planned = await plan(client, MONDAY)
    done = await record(client, MONDAY, duration_s=RIDE_DURATION_S)
    link_id = done["match"]["id"]
    await client.post(f"{MATCHES}/{link_id}/confirm")

    assert (await client.post(f"{MATCHES}/{link_id}/reject")).status_code == 409
    assert (await client.post(f"{SESSIONS}/{done['id']}/unplanned")).status_code == 409
    # And the second side cannot be linked twice either.
    other = await plan(client, TUESDAY)
    duplicate = await client.post(
        MATCHES,
        json={"session_id": done["id"], "planned_session_id": other["id"]},
    )
    assert duplicate.status_code == 409
    assert (await get_planned(client, planned["id"]))["status"] == "completed"


async def test_a_rematch_is_idempotent(client: AsyncClient) -> None:
    await append_ftp(client)
    await plan(client, MONDAY)
    done = await record(client, MONDAY, duration_s=1_500)
    link = done["match"]

    first = (await client.post(f"{SESSIONS}/{done['id']}/rematch")).json()
    second = (await client.post(f"{SESSIONS}/{done['id']}/rematch")).json()

    # The same row, the same verdict, the same score — not a second link.
    assert first["match"]["id"] == second["match"]["id"] == link["id"]
    assert second["match"]["status"] == MatchLinkStatus.PENDING.value
    assert second["match"]["similarity"] == pytest.approx(link["similarity"])
    assert len(await inbox(client)) == 1


async def test_a_rematch_reconsiders_a_session_called_unplanned(
    client: AsyncClient,
) -> None:
    """D142: an explicit re-run overrules an automatic verdict, on request."""
    done = await record(client, MONDAY, duration_s=RIDE_DURATION_S)
    assert done["status"] == "unplanned"  # nothing was planned yet

    await append_ftp(client)
    planned = await plan(client, MONDAY)
    outcome = (await client.post(f"{SESSIONS}/{done['id']}/rematch")).json()

    assert outcome["status"] == "matched"
    assert outcome["match"]["planned_session_id"] == planned["id"]
    assert outcome["candidates"] == 1


async def test_a_typed_in_gym_session_matches_on_its_set_list(
    client: AsyncClient,
) -> None:
    """The strength half of the score (D139): sets, not seconds, not watts."""
    planned = await plan(client, MONDAY, purpose="hypertrophy", structure=LIFT)

    done = await record(
        client,
        MONDAY,
        duration_s=3_600,
        discipline="strength",
        sets=[
            {"exercise_id": "back_squat", "reps": 3, "load_kg": 100.0} for _ in range(5)
        ],
    )

    assert done["match"]["planned_session_id"] == planned["id"]
    assert done["match"]["status"] == MatchLinkStatus.AUTO_HIGH.value
    assert done["match"]["similarity"] == pytest.approx(1.0)
    link = (await client.get(f"{MATCHES}/{done['match']['id']}")).json()
    # One component only, and the other two say why they are absent rather
    # than being scored 1.0 or 0.0 (D138).
    assert [part["component"] for part in link["breakdown"]["components"]] == [
        "structure"
    ]
    [structure] = link["breakdown"]["components"]
    assert structure["basis"] == "sets"
    assert (structure["planned"], structure["actual"]) == (5.0, 5.0)
    assert structure["weight"] == pytest.approx(1.0)
    assert {part["component"] for part in link["breakdown"]["not_assessed"]} == {
        "duration",
        "intensity",
    }


async def test_a_gym_session_with_nothing_to_compare_becomes_a_proposal(
    client: AsyncClient,
) -> None:
    """Nothing assessable is a question, not a refusal."""
    planned = await plan(client, MONDAY, purpose="hypertrophy", structure=LIFT)

    done = await record(client, MONDAY, duration_s=3_600, discipline="strength")

    assert done["match"]["status"] == MatchLinkStatus.PENDING.value
    assert done["match"]["similarity"] is None
    assert done["status"] == "unmatched"
    assert (await get_planned(client, planned["id"]))["status"] == "planned"
    link = (await client.get(f"{MATCHES}/{done['match']['id']}")).json()
    assert link["breakdown"]["components"] == []
    assert link["breakdown"]["score"] is None


async def test_a_ride_cannot_be_linked_to_a_strength_session(
    client: AsyncClient,
) -> None:
    planned = await plan(client, MONDAY, purpose="hypertrophy", structure=LIFT)
    done = await record(client, MONDAY, duration_s=3_600, discipline="cycling")

    response = await client.post(
        MATCHES,
        json={"session_id": done["id"], "planned_session_id": planned["id"]},
    )

    assert response.status_code == 422, response.text
    assert "strength" in response.json()["detail"]


async def test_the_week_card_carries_the_link_state(client: AsyncClient) -> None:
    """The calendar reads the match without a request per card."""
    await append_ftp(client)
    planned = await plan(client, MONDAY)
    done = await record(client, MONDAY, duration_s=RIDE_DURATION_S)

    week = (await client.get(WEEK, params={"start": MONDAY.isoformat()})).json()
    [card] = [card for day in week["days"] for card in day["sessions"]]

    assert card["id"] == planned["id"]
    assert card["matched_session_id"] == done["id"]
    assert card["match_status"] == MatchLinkStatus.AUTO_HIGH.value
    assert card["status"] == "completed"


# --- the freeze rule now has a real probe ---------------------------------------


async def test_an_intent_edit_after_a_proposal_is_a_post_hoc_edit(
    client: AsyncClient,
) -> None:
    """Invariant 4 through WP-6's probe: a proposal counts as matched."""
    await append_ftp(client)
    planned = await plan(client, MONDAY)
    before = await client.patch(
        f"{PLANNED}/{planned['id']}", json={"intent_text": "Before anything arrived."}
    )
    assert before.json()["intent"]["edited_post_hoc"] is False

    await record(client, MONDAY, duration_s=1_500)  # a pending proposal

    after = await client.patch(
        f"{PLANNED}/{planned['id']}", json={"intent_text": "After it arrived."}
    )
    assert after.status_code == 200, after.text
    assert after.json()["intent"]["edited_post_hoc"] is True
    # The pins were kept rather than re-pinned — the athlete executed against
    # them (D54) — which is what the flag exists to make visible.
    assert (
        after.json()["intent"]["pinned_anchor_versions"]
        == before.json()["intent"]["pinned_anchor_versions"]
    )


# --- every mutation is audited --------------------------------------------------


async def test_every_match_mutation_leaves_an_audit_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    first = await plan(client, MONDAY)
    second = await plan(client, TUESDAY)
    done = await record(client, MONDAY, duration_s=RIDE_DURATION_S)
    link_id = done["match"]["id"]

    await client.post(f"{MATCHES}/{link_id}/confirm")
    await client.patch(
        f"{MATCHES}/{link_id}", json={"planned_session_id": second["id"]}
    )
    await client.delete(f"{MATCHES}/{link_id}")
    await client.post(f"{SESSIONS}/{done['id']}/unplanned")
    linked = await client.post(
        MATCHES, json={"session_id": done["id"], "planned_session_id": first["id"]}
    )
    assert linked.status_code == 201, linked.text

    trail = await actions(db_session)

    assert [action for action in trail if action.startswith("match.")] == [
        "match.proposed",
        "match.confirmed",
        "match.swapped",
        "match.unlinked",
        "match.linked",
    ]
    assert "session.unplanned" in trail
    result = await db_session.execute(
        select(AuditLogEntry).where(AuditLogEntry.action == "match.proposed")
    )
    proposed = result.scalar_one()
    assert proposed.actor == "athlete"
    assert proposed.entity_type == "session_match"
    assert proposed.payload_json["planned_session_id"] == first["id"]
    assert proposed.payload_json["similarity"] == pytest.approx(1.0)


async def test_merging_is_audited_with_both_sides(
    data_root: Path, client: AsyncClient, db_session: AsyncSession
) -> None:
    start = dt.datetime(2026, 8, 10, 6, 0, tzinfo=dt.UTC)
    survivor = await upload_gpx(client, "a.gpx", gpx_document(start=start))
    absorbed = await upload_gpx(
        client, "b.gpx", gpx_document(start=start + dt.timedelta(minutes=20))
    )

    await client.post(
        f"{SESSIONS}/{survivor}/merge", json={"absorbed_session_id": absorbed}
    )

    result = await db_session.execute(
        select(AuditLogEntry).where(AuditLogEntry.action == "session.merged")
    )
    entry = result.scalar_one()
    assert entry.entity_id is not None
    assert str(entry.entity_id) == survivor
    assert entry.payload_json["absorbed_session_id"] == absorbed
    assert len(entry.payload_json["recordings_moved"]) == 1


async def test_merging_refuses_two_rides_that_are_not_one_recording(
    data_root: Path, client: AsyncClient
) -> None:
    start = dt.datetime(2026, 8, 10, 6, 0, tzinfo=dt.UTC)
    morning = await upload_gpx(client, "a.gpx", gpx_document(start=start))
    evening = await upload_gpx(
        client, "b.gpx", gpx_document(start=start + dt.timedelta(hours=12))
    )

    too_far = await client.post(
        f"{SESSIONS}/{morning}/merge", json={"absorbed_session_id": evening}
    )
    itself = await client.post(
        f"{SESSIONS}/{morning}/merge", json={"absorbed_session_id": morning}
    )

    assert too_far.status_code == 422
    assert "apart" in too_far.json()["detail"]
    assert itself.status_code == 422


async def test_merging_refuses_a_session_typed_in_by_hand(
    client: AsyncClient,
) -> None:
    one = await record(client, MONDAY, duration_s=1_800)
    two = await record(client, MONDAY, duration_s=1_800)

    response = await client.post(
        f"{SESSIONS}/{one['id']}/merge", json={"absorbed_session_id": two["id"]}
    )

    assert response.status_code == 422
    assert "no recording to merge" in response.json()["detail"]


# --- the missed sweep (WP-6.7) --------------------------------------------------


async def test_the_sweep_marks_a_session_missed_at_the_end_of_the_next_day(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The boundary, from both sides of it, on the athlete's local calendar."""
    await append_ftp(client)
    planned = await plan(client, MONDAY)
    service = MatchingService.from_session(db_session)

    # Still answerable for the whole of Tuesday.
    assert await service.mark_missed(actor=Actor.system(), today_local=TUESDAY) == []
    assert (await get_planned(client, planned["id"]))["status"] == "planned"

    # Wednesday: the grace has run out.
    [marked] = await service.mark_missed(actor=Actor.system(), today_local=WEDNESDAY)

    assert str(marked.id) == planned["id"]
    assert (await get_planned(client, planned["id"]))["status"] == "missed"
    prompts = (await db_session.execute(select(EveningPromptRow))).scalars().all()
    [prompt] = prompts
    assert str(prompt.planned_session_id) == planned["id"]
    assert prompt.status is EveningPromptStatus.PENDING
    assert prompt.kind.value == "missed_session"
    # 72 hours, stored as a fact about the prompt for WP-7 to expire against.
    # Approximate because `created_at` is the database's `now()` (whole seconds
    # on SQLite) and `expires_at` is the service's clock.
    assert prompt.expires_at - prompt.created_at == pytest.approx(
        dt.timedelta(hours=72), abs=dt.timedelta(seconds=5)
    )


async def test_the_sweep_is_idempotent_and_raises_one_prompt(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    await plan(client, MONDAY)
    service = MatchingService.from_session(db_session)

    first = await service.mark_missed(actor=Actor.system(), today_local=THURSDAY)
    second = await service.mark_missed(actor=Actor.system(), today_local=THURSDAY)

    assert len(first) == 1
    assert second == []
    prompts = (await db_session.execute(select(EveningPromptRow))).scalars().all()
    assert len(prompts) == 1


async def test_a_matched_session_is_never_swept(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Even an unanswered *proposal* holds the sweep off.

    A pending link means a recording is sitting against this session; calling
    it missed while asking the athlete whether it was done would be two
    contradictory things on one card.
    """
    await append_ftp(client)
    await plan(client, MONDAY)
    done = await record(client, MONDAY, duration_s=1_500)
    assert done["match"]["status"] == MatchLinkStatus.PENDING.value

    assert (
        await MatchingService.from_session(db_session).mark_missed(
            actor=Actor.system(), today_local=THURSDAY
        )
        == []
    )
    prompts = (await db_session.execute(select(EveningPromptRow))).scalars().all()
    assert prompts == []


async def test_a_paused_plan_is_never_marked_missed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """WP-3's pause is about enforcement, and this is the enforcement."""
    await append_ftp(client)
    planned = await plan(client, MONDAY)
    paused = await client.patch(ATHLETE, json={"plan_state": PlanState.PAUSED.value})
    assert paused.status_code == 200, paused.text

    marked = await MatchingService.from_session(db_session).mark_missed(
        actor=Actor.system(), today_local=THURSDAY
    )

    assert marked == []
    assert (await get_planned(client, planned["id"]))["status"] == "planned"
    prompts = (await db_session.execute(select(EveningPromptRow))).scalars().all()
    assert prompts == []


async def test_the_sweep_reads_the_athletes_own_timezone(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`MATCHING__TIMEZONE`, not the server's clock.

    The instant below is Tuesday 23:00 UTC — which is already Wednesday in
    Auckland (UTC+12) and still Tuesday afternoon in New York (UTC-4). The same
    plan therefore runs out of grace in one zone and not in the other, from one
    moment, which is why the zone is a setting rather than the server's clock.
    """
    from app.core.config import get_settings
    from app.services.matching import athlete_today

    moment = dt.datetime(2026, 8, 11, 23, 0, tzinfo=dt.UTC)

    monkeypatch.setenv("MATCHING__TIMEZONE", "Pacific/Auckland")
    get_settings.cache_clear()
    assert athlete_today(moment) == WEDNESDAY

    monkeypatch.setenv("MATCHING__TIMEZONE", "America/New_York")
    get_settings.cache_clear()
    assert athlete_today(moment) == TUESDAY

    monkeypatch.setenv("MATCHING__TIMEZONE", "Not/AZone")
    get_settings.cache_clear()
    # Loud rather than defaulted: a silent fall back to UTC would mark sessions
    # missed up to a day early for anybody east of it.
    with pytest.raises(ValueError, match="timezone"):
        athlete_today(moment)


# --- protection -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", MATCHES),
        ("post", MATCHES),
        ("get", f"{MATCHES}/019fe000-0000-7000-8000-000000000001"),
        ("delete", f"{MATCHES}/019fe000-0000-7000-8000-000000000001"),
        ("post", f"{SESSIONS}/019fe000-0000-7000-8000-000000000001/rematch"),
        ("post", f"{SESSIONS}/019fe000-0000-7000-8000-000000000001/unplanned"),
    ],
)
async def test_matching_needs_a_session(
    anon_client: AsyncClient, method: str, path: str
) -> None:
    response = await getattr(anon_client, method)(path)
    assert response.status_code == 401


async def test_a_match_that_does_not_exist_is_a_404(client: AsyncClient) -> None:
    missing = "019fe000-0000-7000-8000-0000000000ff"

    assert (await client.get(f"{MATCHES}/{missing}")).status_code == 404
    assert (await client.delete(f"{MATCHES}/{missing}")).status_code == 404
    assert (await client.post(f"{MATCHES}/{missing}/confirm")).status_code == 404
    assert (await client.post(f"{SESSIONS}/{missing}/rematch")).status_code == 404

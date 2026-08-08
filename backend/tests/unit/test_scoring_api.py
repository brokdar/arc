"""WP-7 end to end through HTTP: score, verdict, reasons, offset, expiry.

Every session here is **typed in** (`POST /manual-sessions`), for the reason
`test_matching_api.py` gives: it has an exact duration and no stream, so the
completion axis is arithmetic a reader can check and the stream-derived axes
report the absence they should. The axis math itself is
`test_domain_scoring.py`'s job — what this file tests is the lifecycle around
it: that a settled match scores, that a rescore never rewrites what the athlete
said, that only the athlete may say it, and that an unanswered prompt turns
into a reason rather than into silence.
"""

import datetime as dt
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_actor
from app.domain.actor import Actor
from app.domain.matching import EveningPromptStatus, MatchLinkStatus
from app.domain.scoring import CompletionState, Reason, Verdict
from app.domain.templates import ScoringAxis
from app.persistence.matching import EveningPromptRow
from app.persistence.scoring import SessionReasonsRow, SessionScoreRow
from app.services.matching import MatchingService
from app.services.scoring import ScoringService

ANCHORS = "/api/v1/anchors"
PLANNED = "/api/v1/planned-sessions"
SESSIONS = "/api/v1/sessions"
MANUAL = "/api/v1/manual-sessions"
MATCHES = "/api/v1/matches"
WEEK = "/api/v1/plan/week"

#: A Monday, and the day after it.
MONDAY = dt.date(2026, 8, 10)
TUESDAY = dt.date(2026, 8, 11)

#: 600 + 3 x (480 + 240) — the prescription the matching and week tests use.
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

#: The same ride with every block twice as long — what a post-hoc intent edit
#: replaces the prescription with, so the recording suddenly falls short of it.
LONGER_RIDE: dict[str, Any] = {
    "discipline": "cycling",
    "steps": [
        {"kind": "steady", "duration_s": 1_800, "role": "warmup"},
        {
            "kind": "repeat",
            "times": 3,
            "children": [
                {"kind": "steady", "duration_s": 1_440, "role": "work"},
                {"kind": "steady", "duration_s": 720, "role": "recovery"},
            ],
        },
    ],
}

LIFT: dict[str, Any] = {
    "discipline": "strength",
    "groups": [
        {
            "items": [
                {
                    "exercise_id": "back_squat",
                    "sets": 4,
                    "reps": 5,
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
    date: dt.date = MONDAY,
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
    date: dt.date = MONDAY,
    *,
    duration_s: int = RIDE_DURATION_S,
    discipline: str = "cycling",
    sets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Type in a session, asserting it was accepted."""
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


async def matched(
    client: AsyncClient, *, duration_s: int = RIDE_DURATION_S
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A planned session and the recording that auto-linked to it."""
    await append_ftp(client)
    planned = await plan(client)
    done = await record(client, duration_s=duration_s)
    assert done["match"]["status"] == MatchLinkStatus.AUTO_HIGH.value
    return planned, done


async def score(client: AsyncClient, session_id: str) -> dict[str, Any]:
    """The score in force for one session."""
    response = await client.get(f"{SESSIONS}/{session_id}/score")
    assert response.status_code == 200, response.text
    return response.json()


async def declare(client: AsyncClient, session_id: str, **body: Any) -> dict[str, Any]:
    """Declare a verdict, asserting it was accepted."""
    response = await client.put(f"{SESSIONS}/{session_id}/verdict", json=body)
    assert response.status_code == 200, response.text
    return response.json()


async def verdict(client: AsyncClient, session_id: str) -> dict[str, Any]:
    """The standing declaration."""
    response = await client.get(f"{SESSIONS}/{session_id}/verdict")
    assert response.status_code == 200, response.text
    return response.json()


def axis(document: dict[str, Any], which: ScoringAxis) -> dict[str, Any]:
    """One axis of a score payload."""
    return next(one for one in document["axes"] if one["axis"] == which.value)


# --- a settled match is scored ----------------------------------------------------


async def test_a_settled_match_scores_the_session(client: AsyncClient) -> None:
    _, done = await matched(client)

    document = await score(client, done["id"])

    assert document["version"] == 1
    assert document["recompute_reason"] is None
    assert document["purpose"] == "sweet_spot"
    assert document["standalone"] is False
    # `sweet_spot` is scored on three axes and no others.
    assert [one["axis"] for one in document["axes"]] == [
        ScoringAxis.COMPLETION.value,
        ScoringAxis.ADHERENCE.value,
        ScoringAxis.PACING.value,
    ]
    # The ride lasted exactly what was prescribed.
    assert axis(document, ScoringAxis.COMPLETION)["value"] == pytest.approx(1.0)
    # A typed-in session has no stream, so the two axes that read one say so
    # rather than scoring zero.
    assert axis(document, ScoringAxis.ADHERENCE)["not_assessed"]
    assert axis(document, ScoringAxis.PACING)["not_assessed"]
    assert document["suggested_verdict"] == Verdict.AS_INTENDED.value
    assert document["verdict_rule"] == "nothing_contradicts"
    assert document["verdict_rationale"]


async def test_the_score_records_what_it_was_computed_from(
    client: AsyncClient,
) -> None:
    planned, done = await matched(client)

    document = await score(client, done["id"])

    assert document["planned_session_id"] == planned["id"]
    assert document["intent_version"] == 1
    # The FTP the prescription pinned travels with the score, so "why is this
    # 88 % of 250 W" is answerable after the athlete's FTP has moved.
    assert set(document["pinned_anchor_versions"]) == {"ftp"}
    assert document["metrics_version_id"] is not None
    assert document["alignment_version_id"] is not None


async def test_a_short_session_is_suggested_under(client: AsyncClient) -> None:
    # 80 % of the prescribed duration: still a confident match (the duration
    # ratio is 0.8, above the auto-link floor) and plainly not the whole session.
    _, done = await matched(client, duration_s=int(RIDE_DURATION_S * 0.8))

    document = await score(client, done["id"])

    assert axis(document, ScoringAxis.COMPLETION)["value"] == pytest.approx(0.8)
    assert document["suggested_verdict"] == Verdict.UNDER.value
    assert document["verdict_rule"] == "completion_short"


async def test_a_pending_proposal_is_not_scored(client: AsyncClient) -> None:
    # Half the prescribed duration proposes rather than links, and a proposal
    # is a question: putting a verdict on the answer before the athlete has
    # given it is the mistake the status table refuses to make (D140).
    await append_ftp(client)
    await plan(client)
    done = await record(client, duration_s=RIDE_DURATION_S // 2)
    assert done["match"]["status"] == MatchLinkStatus.PENDING.value

    response = await client.get(f"{SESSIONS}/{done['id']}/score")

    assert response.status_code == 404
    assert "pending proposal" in response.json()["detail"]


async def test_confirming_a_proposal_scores_it(client: AsyncClient) -> None:
    await append_ftp(client)
    await plan(client)
    done = await record(client, duration_s=RIDE_DURATION_S // 2)

    confirmed = await client.post(f"{MATCHES}/{done['match']['id']}/confirm")

    assert confirmed.status_code == 200, confirmed.text
    document = await score(client, done["id"])
    assert axis(document, ScoringAxis.COMPLETION)["value"] == pytest.approx(0.5)
    # Exactly at the abandoned floor, which is inclusive at the bottom like
    # every other threshold in this codebase: half the session is `under`, and
    # `abandoned` starts below it.
    assert document["suggested_verdict"] == Verdict.UNDER.value


async def test_a_displaced_link_is_scored_standalone(client: AsyncClient) -> None:
    await append_ftp(client)
    planned = await plan(client)
    done = await record(client, TUESDAY, duration_s=600)

    linked = await client.post(
        MATCHES,
        json={
            "session_id": done["id"],
            "planned_session_id": planned["id"],
            "displaced": True,
        },
    )

    assert linked.status_code == 201, linked.text
    document = await score(client, done["id"])
    assert document["standalone"] is True
    assert document["suggested_verdict"] == Verdict.DIFFERENT_SESSION.value
    # Nothing is compared against a prescription the athlete did not attempt.
    assert all(one["value"] is None for one in document["axes"])


async def test_an_unlinked_session_has_nothing_to_be_scored_against(
    client: AsyncClient,
) -> None:
    done = await record(client, duration_s=RIDE_DURATION_S)

    response = await client.post(f"{SESSIONS}/{done['id']}/score/recompute")

    assert response.status_code == 422
    assert "not linked to a planned session" in response.json()["detail"]


# --- the version chain ------------------------------------------------------------


async def test_a_recompute_appends_a_version_and_supersedes_the_old_one(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, done = await matched(client)

    again = await client.post(
        f"{SESSIONS}/{done['id']}/score/recompute", json={"reason": "by hand"}
    )

    assert again.status_code == 200, again.text
    assert again.json()["version"] == 2
    assert again.json()["recompute_reason"] == "by hand"
    history = await client.get(f"{SESSIONS}/{done['id']}/score/history")
    assert [one["version"] for one in history.json()] == [1, 2]
    rows = (
        await db_session.execute(
            select(SessionScoreRow).order_by(SessionScoreRow.version)
        )
    ).scalars()
    first, second = list(rows)
    assert first.superseded_by == second.id
    assert second.superseded_by is None


async def test_unlinking_keeps_the_score_history(
    client: AsyncClient,
) -> None:
    # Nothing deletes a computed artefact (invariant 1). What unlinking removes
    # is the *claim*: the planned session stops naming this recording, so the
    # week strip stops reading a verdict off it — while the chain that says
    # what was once measured stays exactly where it was.
    planned, done = await matched(client)
    await declare(client, done["id"], verdict=Verdict.AS_INTENDED.value)

    dropped = await client.delete(f"{MATCHES}/{done['match']['id']}")

    assert dropped.status_code == 200, dropped.text
    history = await client.get(f"{SESSIONS}/{done['id']}/score/history")
    assert [one["version"] for one in history.json()] == [1]
    week = await client.get(WEEK, params={"start": MONDAY.isoformat()})
    (card,) = week.json()["days"][0]["sessions"]
    assert card["id"] == planned["id"]
    assert card["completion_state"] == CompletionState.PLANNED.value


# --- the athlete's verdict --------------------------------------------------------


async def test_the_athlete_confirms_the_suggestion(client: AsyncClient) -> None:
    _, done = await matched(client)

    declared = await declare(client, done["id"], verdict=Verdict.AS_INTENDED.value)

    assert declared["declared_verdict"] == Verdict.AS_INTENDED.value
    assert declared["suggested_at_declaration"] == Verdict.AS_INTENDED.value
    assert declared["score_version_id"] is not None
    assert declared["contested"] is False
    # `as_intended` is the one verdict that needs no reason.
    assert declared["reasons"] is None


async def test_an_override_carries_its_reasons_in_order_of_primacy(
    client: AsyncClient,
) -> None:
    _, done = await matched(client)

    declared = await declare(
        client,
        done["id"],
        verdict=Verdict.UNDER.value,
        reasons=[Reason.TIME.value, Reason.FATIGUE.value],
        note="Had to be back for the school run.",
    )

    assert declared["declared_verdict"] == Verdict.UNDER.value
    assert declared["suggested_at_declaration"] == Verdict.AS_INTENDED.value
    assert declared["reasons"]["version"] == 1
    assert declared["reasons"]["reasons"] == [Reason.TIME.value, Reason.FATIGUE.value]
    assert declared["reasons"]["note"] == "Had to be back for the school run."
    assert declared["reasons"]["recorded_by"] == "athlete"
    assert await verdict(client, done["id"]) == declared


async def test_an_override_without_a_reason_is_refused(client: AsyncClient) -> None:
    _, done = await matched(client)

    response = await client.put(
        f"{SESSIONS}/{done['id']}/verdict", json={"verdict": Verdict.UNDER.value}
    )

    assert response.status_code == 422
    assert "not_provided" in response.json()["detail"]


async def test_a_reason_may_not_be_given_twice(client: AsyncClient) -> None:
    # The order is the primacy, so a repeated reason has no second place to
    # hold — a schema `max_length` cannot say that, and the service does.
    _, done = await matched(client)

    response = await client.put(
        f"{SESSIONS}/{done['id']}/verdict",
        json={
            "verdict": Verdict.UNDER.value,
            "reasons": [Reason.TIME.value, Reason.TIME.value],
        },
    )

    assert response.status_code == 422


async def test_more_than_three_reasons_is_refused_by_the_contract(
    client: AsyncClient,
) -> None:
    _, done = await matched(client)

    response = await client.put(
        f"{SESSIONS}/{done['id']}/verdict",
        json={
            "verdict": Verdict.UNDER.value,
            "reasons": [
                Reason.TIME.value,
                Reason.HEAT.value,
                Reason.TRAFFIC.value,
                Reason.SLEEP.value,
            ],
        },
    )

    assert response.status_code == 422


async def test_the_agent_may_never_declare_a_verdict(
    app: FastAPI, client: AsyncClient
) -> None:
    """WP-7.2's one hard rule, enforced by actor and not by adapter.

    The agent presents a perfectly valid write-scoped identity. What it may not
    do is write the athlete's account of their own session — an agent that
    could would be inventing testimony to read back later.
    """
    _, done = await matched(client)
    app.dependency_overrides[current_actor] = lambda: Actor.agent("coach")

    response = await client.put(
        f"{SESSIONS}/{done['id']}/verdict",
        json={
            "verdict": Verdict.UNDER.value,
            "reasons": [Reason.FATIGUE.value],
        },
    )

    assert response.status_code == 403
    assert "Only the athlete" in response.json()["detail"]


async def test_the_agent_may_not_revise_reasons_either(
    app: FastAPI, client: AsyncClient
) -> None:
    _, done = await matched(client)
    await declare(
        client,
        done["id"],
        verdict=Verdict.UNDER.value,
        reasons=[Reason.TIME.value],
    )
    app.dependency_overrides[current_actor] = lambda: Actor.agent("coach")

    response = await client.put(
        f"{SESSIONS}/{done['id']}/verdict/reasons",
        json={"reasons": [Reason.FELT_GOOD.value]},
    )

    assert response.status_code == 403


async def test_revising_reasons_appends_rather_than_edits(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, done = await matched(client)
    await declare(
        client,
        done["id"],
        verdict=Verdict.UNDER.value,
        reasons=[Reason.TIME.value],
    )

    revised = await client.put(
        f"{SESSIONS}/{done['id']}/verdict/reasons",
        json={
            "reasons": [Reason.ILLNESS.value, Reason.TIME.value],
            "revision_reason": "it turned out to be a cold",
        },
    )

    assert revised.status_code == 200, revised.text
    assert revised.json()["version"] == 2
    assert revised.json()["revision_reason"] == "it turned out to be a cold"
    # What was said first is still readable: testimony, not a form field.
    rows = list(
        (
            await db_session.execute(
                select(SessionReasonsRow).order_by(SessionReasonsRow.version)
            )
        ).scalars()
    )
    assert [row.reasons for row in rows] == [
        [Reason.TIME.value],
        [Reason.ILLNESS.value, Reason.TIME.value],
    ]
    assert rows[0].superseded_by == rows[1].id


async def test_reasons_cannot_be_revised_before_anything_is_declared(
    client: AsyncClient,
) -> None:
    _, done = await matched(client)

    response = await client.put(
        f"{SESSIONS}/{done['id']}/verdict/reasons",
        json={"reasons": [Reason.TIME.value]},
    )

    assert response.status_code == 404
    assert "no declared verdict" in response.json()["detail"]


# --- contested (WP-7.4) -----------------------------------------------------------


async def test_a_post_hoc_intent_edit_rescores_and_contests_the_declaration(
    client: AsyncClient,
) -> None:
    """The seam, end to end: edit the intent after the fact and the machine
    changes its mind — loudly, and without touching what the athlete said."""
    _, done = await matched(client)
    planned_id = done["match"]["planned_session_id"]
    await declare(client, done["id"], verdict=Verdict.AS_INTENDED.value)

    edited = await client.patch(
        f"{PLANNED}/{planned_id}", json={"structure": LONGER_RIDE}
    )

    assert edited.status_code == 200, edited.text
    # The prescription is now three times as long, so the same ride completed a
    # third of it: version 2 says `abandoned`.
    rescored = await score(client, done["id"])
    assert rescored["version"] == 2
    assert rescored["intent_version"] == 2
    assert rescored["suggested_verdict"] == Verdict.ABANDONED.value
    standing = await verdict(client, done["id"])
    assert standing["contested"] is True
    assert standing["contested_verdict"] == Verdict.ABANDONED.value
    assert standing["contested_at"] is not None
    # And the declaration itself is exactly as the athlete left it.
    assert standing["declared_verdict"] == Verdict.AS_INTENDED.value


async def test_a_rescore_that_repeats_itself_does_not_contest_an_override(
    client: AsyncClient,
) -> None:
    # The athlete overruled `as_intended` with `under`. A rescore that still
    # says `as_intended` has not contradicted them — it has said the same thing
    # they already ruled on, and flagging that would make every deliberate
    # override light up the moment anything was recomputed.
    _, done = await matched(client)
    await declare(
        client,
        done["id"],
        verdict=Verdict.UNDER.value,
        reasons=[Reason.FELT_GOOD.value],
    )

    again = await client.post(f"{SESSIONS}/{done['id']}/score/recompute")

    assert again.status_code == 200, again.text
    assert again.json()["suggested_verdict"] == Verdict.AS_INTENDED.value
    assert (await verdict(client, done["id"]))["contested"] is False


async def test_declaring_again_clears_a_contested_flag(client: AsyncClient) -> None:
    _, done = await matched(client)
    planned_id = done["match"]["planned_session_id"]
    await declare(client, done["id"], verdict=Verdict.AS_INTENDED.value)
    await client.patch(f"{PLANNED}/{planned_id}", json={"structure": LONGER_RIDE})
    assert (await verdict(client, done["id"]))["contested"] is True

    await declare(
        client,
        done["id"],
        verdict=Verdict.ABANDONED.value,
        reasons=[Reason.TIME.value],
    )

    standing = await verdict(client, done["id"])
    assert standing["contested"] is False
    assert standing["declared_verdict"] == Verdict.ABANDONED.value


# --- the alignment offset (A7.1) ---------------------------------------------------


async def test_the_offset_creates_an_alignment_version_and_rescores(
    client: AsyncClient,
) -> None:
    _, done = await matched(client)
    before = await client.get(f"{SESSIONS}/{done['id']}/alignment")
    assert before.status_code == 200, before.text
    assert before.json() == {
        "version": 1,
        "computed_at": before.json()["computed_at"],
        "recompute_reason": None,
        "planned_session_id": done["match"]["planned_session_id"],
        "offset_s": 0,
        "aligned": [],
        "excluded": [],
        # A typed-in session has no stream, so the detector found no efforts
        # and every prescribed work step is unmatched. That is the honest
        # alignment of a session with nothing to align to.
        "unmatched_steps": [1, 3, 5],
        "unmatched_intervals": [],
    }

    moved = await client.put(
        f"{SESSIONS}/{done['id']}/alignment", json={"offset_s": 180}
    )

    assert moved.status_code == 200, moved.text
    assert moved.json()["version"] == 2
    assert moved.json()["offset_s"] == 180
    assert moved.json()["recompute_reason"] == "the alignment offset was changed"
    # The offset is a real input, so moving it rescores through the normal path
    # and the new score points at the new alignment version.
    rescored = await score(client, done["id"])
    assert rescored["version"] == 2
    assert rescored["recompute_reason"] == "the alignment offset was changed"
    # …and the new score points at the new alignment version, not the old one.
    before_score = (await client.get(f"{SESSIONS}/{done['id']}/score/history")).json()[
        0
    ]
    assert rescored["alignment_version_id"] != before_score["alignment_version_id"]


async def test_an_implausible_offset_is_refused(client: AsyncClient) -> None:
    _, done = await matched(client)

    response = await client.put(
        f"{SESSIONS}/{done['id']}/alignment", json={"offset_s": 7 * 3_600}
    )

    assert response.status_code == 422


async def test_a_strength_session_has_no_timeline_to_slide(
    client: AsyncClient,
) -> None:
    await plan(client, purpose="max_strength", structure=LIFT)
    done = await record(
        client,
        duration_s=3_600,
        discipline="strength",
        sets=[
            {"exercise_id": "back_squat", "reps": 5, "load_kg": 100.0} for _ in range(4)
        ],
    )
    assert done["match"]["status"] == MatchLinkStatus.AUTO_HIGH.value

    response = await client.put(
        f"{SESSIONS}/{done['id']}/alignment", json={"offset_s": 60}
    )

    assert response.status_code == 422
    assert "paired by position" in response.json()["detail"]


async def test_a_strength_session_is_scored_on_its_sets(client: AsyncClient) -> None:
    await plan(client, purpose="max_strength", structure=LIFT)
    done = await record(
        client,
        duration_s=3_600,
        discipline="strength",
        sets=[
            {"exercise_id": "back_squat", "reps": 5, "load_kg": load}
            for load in (100.0, 100.0, 80.0, 80.0)
        ],
    )

    document = await score(client, done["id"])

    assert [one["axis"] for one in document["axes"]] == [
        ScoringAxis.COMPLETION.value,
        ScoringAxis.SETS_LOAD.value,
    ]
    # Four of four sets logged, two of them 20 % light against a 10 % tolerance.
    assert axis(document, ScoringAxis.SETS_LOAD)["value"] == pytest.approx(0.5)
    assert document["suggested_verdict"] == Verdict.UNDER.value
    kinds = [one["kind"] for one in axis(document, ScoringAxis.SETS_LOAD)["criteria"]]
    assert kinds == ["sets_completed", "load_within"]


# --- the stream seam, end to end ----------------------------------------------------


#: A recovery ride with a hard 150 W cap: one steady work step, no targets, and
#: a ceiling the ingested GPX (a constant 210 W) plainly breaks.
RECOVERY: dict[str, Any] = {
    "discipline": "cycling",
    "steps": [{"kind": "steady", "duration_s": 600, "role": "work"}],
}
#: The athlete-local date `tests.unit.activity_files.START` falls on.
RIDE_DAY = dt.date(2026, 6, 1)

CAP: dict[str, Any] = {
    "kind": "ceiling",
    "channel": "power",
    "limit": {"kind": "absolute", "value": 150.0, "unit": "W"},
    "max_seconds_above": 60,
    "smoothing_s": 0,
}


async def test_a_ceiling_is_judged_against_the_recording_itself(
    data_root: Any, client: AsyncClient
) -> None:
    """The whole chain, with a real file at the bottom of it.

    `app.services.scoring` may not read parquet, so the cleaned columns reach
    it through a seam that `app.main.create_app` installs. Nothing else in this
    file proves that seam is connected: every other session here is typed in
    and has no stream at all. This one ingests a ride, and the discipline axis
    can only answer because the samples arrived.
    """
    from tests.unit.activity_files import gpx_document

    await plan(
        client,
        RIDE_DAY,
        purpose="recovery",
        structure=RECOVERY,
        success_criteria=[CAP],
    )
    upload = await client.post(
        "/api/v1/ingest/upload",
        files={"file": ("ride.gpx", gpx_document().encode(), "application/gpx+xml")},
    )
    assert upload.status_code == 200, upload.text
    [session_id] = upload.json()["session_ids"]

    document = await score(client, session_id)

    discipline = axis(document, ScoringAxis.DISCIPLINE)
    (outcome,) = discipline["criteria"]
    assert outcome["passed"] is False
    # A 210 W ride against a 150 W cap that allows a minute: far past the
    # five-minute window over which a broken ceiling decays to nothing.
    assert outcome["observed"] > 60
    assert discipline["value"] == pytest.approx(0.0)
    assert document["suggested_verdict"] == Verdict.OVER.value
    assert document["verdict_rule"] == "ceiling_exceeded"


# --- the missed side, and the expiry sweep (WP-7.3) ---------------------------------


async def sweep(session: AsyncSession) -> None:
    """Run the missed sweep far enough past the plan to catch it."""
    await MatchingService.from_session(session).mark_missed(
        actor=Actor.system(), today_local=MONDAY + dt.timedelta(days=3)
    )


async def test_answering_the_evening_prompt_records_reasons_and_closes_it(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    planned = await plan(client)
    await sweep(db_session)

    answered = await client.put(
        f"{PLANNED}/{planned['id']}/reasons",
        json={"reasons": [Reason.ILLNESS.value], "note": "Chest infection."},
    )

    assert answered.status_code == 200, answered.text
    assert answered.json()["reasons"] == [Reason.ILLNESS.value]
    assert answered.json()["recorded_by"] == "athlete"
    prompt = (await db_session.execute(select(EveningPromptRow))).scalar_one()
    await db_session.refresh(prompt)
    assert prompt.status is EveningPromptStatus.ANSWERED
    assert prompt.resolved_at is not None


async def test_an_unanswered_prompt_expires_into_not_provided(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Silence is an answer we asked for and did not get, and it is a row.

    Without it, a coaching agent reading "no reason recorded" cannot tell the
    athlete who declined to say from the athlete who was never asked.
    """
    await append_ftp(client)
    planned = await plan(client)
    await sweep(db_session)

    expired = await ScoringService.from_session(db_session).expire_prompts(
        actor=Actor.system(),
        now=dt.datetime.now(dt.UTC) + dt.timedelta(hours=73),
    )

    assert len(expired) == 1
    assert expired[0].status is EveningPromptStatus.EXPIRED
    response = await client.get(f"{PLANNED}/{planned['id']}/reasons")
    assert response.status_code == 200, response.text
    assert response.json()["reasons"] == [Reason.NOT_PROVIDED.value]
    assert response.json()["recorded_by"] == "system"


async def test_a_prompt_inside_its_seventy_two_hours_is_left_alone(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    await plan(client)
    await sweep(db_session)

    expired = await ScoringService.from_session(db_session).expire_prompts(
        actor=Actor.system(),
        now=dt.datetime.now(dt.UTC) + dt.timedelta(hours=71),
    )

    assert expired == []


async def test_answering_after_an_expiry_revises_rather_than_replaces(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    planned = await plan(client)
    await sweep(db_session)
    await ScoringService.from_session(db_session).expire_prompts(
        actor=Actor.system(),
        now=dt.datetime.now(dt.UTC) + dt.timedelta(hours=73),
    )

    answered = await client.put(
        f"{PLANNED}/{planned['id']}/reasons",
        json={"reasons": [Reason.ILLNESS.value]},
    )

    assert answered.status_code == 200, answered.text
    assert answered.json()["version"] == 2
    assert answered.json()["reasons"] == [Reason.ILLNESS.value]


async def test_a_planned_session_nobody_has_answered_for_has_no_reasons(
    client: AsyncClient,
) -> None:
    await append_ftp(client)
    planned = await plan(client)

    response = await client.get(f"{PLANNED}/{planned['id']}/reasons")

    assert response.status_code == 404


# --- the week strip (WP-7.5) --------------------------------------------------------


async def test_the_week_strip_carries_a_state_per_card_and_per_day(
    client: AsyncClient,
) -> None:
    planned, done = await matched(client, duration_s=int(RIDE_DURATION_S * 0.8))

    payload = (await client.get(WEEK, params={"start": MONDAY.isoformat()})).json()

    monday, tuesday = payload["days"][0], payload["days"][1]
    (card,) = monday["sessions"]
    assert card["id"] == planned["id"]
    # No declaration yet, so the strip shows the machine's suggestion.
    assert card["completion_state"] == CompletionState.UNDER.value
    assert monday["completion_state"] == CompletionState.UNDER.value
    # A day with nothing planned and nothing done has no outcome to colour.
    assert tuesday["completion_state"] is None
    assert done["id"]


async def test_the_declaration_wins_over_the_suggestion_on_the_strip(
    client: AsyncClient,
) -> None:
    # A calendar that kept showing the suggestion after the athlete had
    # overruled it would be arguing with them once a week.
    _, done = await matched(client, duration_s=int(RIDE_DURATION_S * 0.8))
    await declare(client, done["id"], verdict=Verdict.AS_INTENDED.value)

    payload = (await client.get(WEEK, params={"start": MONDAY.isoformat()})).json()

    (card,) = payload["days"][0]["sessions"]
    assert card["completion_state"] == CompletionState.COMPLETED_AS_INTENDED.value


async def test_a_missed_session_colours_its_day_missed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    await plan(client)
    await sweep(db_session)

    payload = (await client.get(WEEK, params={"start": MONDAY.isoformat()})).json()

    assert payload["days"][0]["completion_state"] == CompletionState.MISSED.value


async def test_a_ride_nothing_was_planned_for_colours_its_day_unplanned(
    client: AsyncClient,
) -> None:
    done = await record(client, TUESDAY, duration_s=3_600)
    assert done["status"] == "unplanned"

    payload = (await client.get(WEEK, params={"start": MONDAY.isoformat()})).json()

    assert payload["days"][1]["completion_state"] == CompletionState.UNPLANNED.value

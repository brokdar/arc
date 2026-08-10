"""Plan-change proposals end to end: the lifecycle, the guardrails, the sweeps.

Invariant 6 in one file. The athlete's half goes through HTTP (there is an
inbox and two answers); writing a proposal has no endpoint by design — it is
the coaching agent's move — so :func:`propose` drives the same service the
phase-2 MCP tool will.

Everything an agent does is done as `agent:coach`, because that is the actor
the guardrails are stated in terms of: the rate cap and the red flag bind
agents and nobody else.
"""

import datetime as dt
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    RateLimitedError,
    RedFlagError,
    ValidationError,
)
from app.domain.actor import Actor
from app.domain.athlete import Discipline
from app.domain.proposals import (
    UPDATE_FIELDS,
    CreateChange,
    DeleteChange,
    MoveChange,
    PlanChange,
    ProposalStatus,
    UpdateChange,
)
from app.domain.purpose import Purpose
from app.main import create_app
from app.persistence.activity import SessionRow
from app.persistence.audit import AuditLogEntry
from app.persistence.proposals import PlanProposalRow
from app.services.anchors import AnchorService
from app.services.proposals import (
    EXPIRY_JOB_ID,
    ProposalOutcome,
    ProposalService,
    run_proposal_expiry,
)

PROPOSALS = "/api/v1/proposals"
SESSIONS = "/api/v1/planned-sessions"
WORKOUTS = "/api/v1/workouts"
ANCHORS = "/api/v1/anchors"
ATHLETE = "/api/v1/athlete"
MANUAL = "/api/v1/manual-sessions"

AGENT = Actor.agent("coach")

#: A one-hour steady ride at 60-70% FTP. Cheap to predict and easy to make
#: harder, which is what the red-flag tests need.
EASY_RIDE: dict[str, Any] = {
    "discipline": "cycling",
    "steps": [
        {
            "kind": "steady",
            "duration_s": 3_600,
            "targets": {
                "power": {
                    "kind": "percent_of_anchor",
                    "anchor_type": "ftp",
                    "pct_low": 0.6,
                    "pct_high": 0.7,
                }
            },
        }
    ],
}

#: The same hour at 95-105% FTP: same purpose, much more load.
HARD_RIDE: dict[str, Any] = {
    "discipline": "cycling",
    "steps": [
        {
            "kind": "steady",
            "duration_s": 3_600,
            "targets": {
                "power": {
                    "kind": "percent_of_anchor",
                    "anchor_type": "ftp",
                    "pct_low": 0.95,
                    "pct_high": 1.05,
                }
            },
        }
    ],
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
                    "load": {"kind": "kg", "value": 100},
                }
            ]
        }
    ],
}


# --- helpers ---------------------------------------------------------------------


async def append_ftp(client: AsyncClient, value: float = 250) -> str:
    """Append an FTP anchor version and return its id."""
    response = await client.post(
        ANCHORS,
        json={"anchor_type": "ftp", "value": value, "provenance": "estimated"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def plan(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    """Plan a session, asserting it was accepted."""
    payload: dict[str, Any] = {
        "date": "2026-08-10",
        "purpose": "endurance",
        "structure": EASY_RIDE,
    } | overrides
    response = await client.post(SESSIONS, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def propose(
    session: AsyncSession,
    changes: Sequence[PlanChange],
    *,
    actor: Actor = AGENT,
    rationale: str = "Tuesday looks heavy after Sunday's ride.",
    hours: int = 48,
    dry_run: bool = False,
) -> ProposalOutcome:
    """Write (or dry-run) a proposal through the service the MCP tool will use."""
    return await ProposalService.from_session(session).propose(
        actor=actor,
        changes=changes,
        rationale=rationale,
        expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=hours),
        dry_run=dry_run,
    )


async def stored(session: AsyncSession) -> list[PlanProposalRow]:
    """Every proposal in the database, oldest first."""
    session.expire_all()
    result = await session.execute(
        select(PlanProposalRow).order_by(PlanProposalRow.created_at, PlanProposalRow.id)
    )
    return list(result.scalars())


async def audit_actions(session: AsyncSession) -> list[str]:
    """Every audit action so far, oldest first."""
    session.expire_all()
    result = await session.execute(
        select(AuditLogEntry.action).order_by(AuditLogEntry.at, AuditLogEntry.id)
    )
    return list(result.scalars())


async def raise_red_flag(client: AsyncClient, severity: str = "moderate") -> None:
    """Set the athlete's illness/injury flag."""
    response = await client.patch(
        ATHLETE,
        json={
            "red_flag_active": True,
            "red_flag_severity": severity,
            "red_flag_note": "chest infection",
        },
    )
    assert response.status_code == 200, response.text


# --- writing a proposal ----------------------------------------------------------


async def test_a_proposal_stores_its_rationale_diff_and_expiry(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    session = await plan(client)

    outcome = await propose(
        db_session,
        [
            MoveChange(
                planned_session_id=uuid.UUID(session["id"]),
                expected_intent_version=session["intent"]["version"],
                date=dt.date(2026, 8, 12),
            )
        ],
    )

    assert outcome.proposal is not None
    assert outcome.proposal.status is ProposalStatus.PENDING
    assert outcome.proposal.created_by == "agent:coach"
    assert outcome.proposal.rationale
    assert outcome.proposal.expires_at > dt.datetime.now(dt.UTC)
    [entry] = outcome.diff
    assert entry["kind"] == "move"
    assert entry["before"]["date"] == "2026-08-10"
    assert entry["after"]["date"] == "2026-08-12"


async def test_a_proposal_without_a_rationale_is_refused(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Invariant 6: the athlete has to be able to weigh it, and whitespace is
    # the shape a required field takes when nobody wanted to fill it in.
    await append_ftp(client)
    session = await plan(client)

    with pytest.raises(ValidationError, match="needs a rationale"):
        await propose(
            db_session,
            [
                DeleteChange(
                    planned_session_id=uuid.UUID(session["id"]),
                    expected_intent_version=1,
                )
            ],
            rationale="   ",
        )


async def test_a_proposal_targeting_a_session_that_does_not_exist_is_a_404(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(NotFoundError):
        await propose(
            db_session,
            [DeleteChange(planned_session_id=uuid.uuid7(), expected_intent_version=1)],
        )


async def test_two_changes_about_one_session_are_refused(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # The token is checked once per change against the plan as it stands, so
    # two changes on one session both validate against the version in force
    # and the second lands on a version it was never computed against. The
    # shape is refused rather than the outcome patched up.
    await append_ftp(client)
    session = await plan(client)
    target = uuid.UUID(session["id"])

    def revision(note: str) -> UpdateChange:
        return UpdateChange(
            planned_session_id=target,
            expected_intent_version=1,
            updates={"coach_notes": note},
        )

    with pytest.raises(ValidationError, match="changes 0 and 1 both target"):
        await propose(db_session, [revision("spin easy"), revision("or don't")])
    assert await stored(db_session) == []


async def test_two_changes_about_two_sessions_are_fine(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # The distinctness rule bites on duplicate targets and nothing else: a
    # multi-entity proposal is the ordinary case.
    await append_ftp(client)
    monday = await plan(client)
    tuesday = await plan(client, date="2026-08-11")
    outcome = await propose(
        db_session,
        [
            UpdateChange(
                planned_session_id=uuid.UUID(monday["id"]),
                expected_intent_version=1,
                updates={"coach_notes": "spin easy"},
            ),
            DeleteChange(
                planned_session_id=uuid.UUID(tuesday["id"]), expected_intent_version=1
            ),
        ],
    )
    assert outcome.proposal is not None

    response = await client.post(f"{PROPOSALS}/{outcome.proposal.id}/accept")

    assert response.status_code == 200, response.text
    revised = await client.get(f"{SESSIONS}/{monday['id']}")
    assert revised.json()["intent"]["coach_notes"] == "spin easy"
    assert (await client.get(f"{SESSIONS}/{tuesday['id']}")).status_code == 404


async def test_a_stale_token_is_refused_when_the_proposal_is_written(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    session = await plan(client)

    with pytest.raises(ConflictError, match="intent version 7, but version 1"):
        await propose(
            db_session,
            [
                DeleteChange(
                    planned_session_id=uuid.UUID(session["id"]),
                    expected_intent_version=7,
                )
            ],
        )
    assert await stored(db_session) == []


# --- the dry run -----------------------------------------------------------------


async def test_a_dry_run_returns_the_diff_and_writes_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    session = await plan(client)
    before = await audit_actions(db_session)

    outcome = await propose(
        db_session,
        [
            UpdateChange(
                planned_session_id=uuid.UUID(session["id"]),
                expected_intent_version=1,
                updates={"purpose": "recovery"},
            )
        ],
        dry_run=True,
    )

    assert outcome.proposal is None
    assert outcome.superseded == ()
    [entry] = outcome.diff
    assert entry["before"]["purpose"] == "endurance"
    assert entry["after"]["purpose"] == "recovery"
    assert await stored(db_session) == []
    assert await audit_actions(db_session) == before


async def test_a_dry_run_does_not_bootstrap_the_athlete_profile(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # `AthleteService.get` creates the singleton row on first access and
    # commits; the red-flag read must not go through it, or "check before you
    # act" writes a row.
    await append_ftp(client)
    session = await plan(client)

    await propose(
        db_session,
        [
            MoveChange(
                planned_session_id=uuid.UUID(session["id"]),
                expected_intent_version=1,
                date=dt.date(2026, 8, 12),
            )
        ],
        dry_run=True,
    )

    assert "athlete.created" not in await audit_actions(db_session)


# --- the diff --------------------------------------------------------------------


async def test_an_update_that_touches_nothing_else_reports_nothing_else(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # A note-only revision keeps the library workout the session was
    # prescribed from — `stage_update` never touches it — so a diff showing
    # `after.workout_id = null` would be telling the athlete the prescription
    # is about to be thrown away.
    await append_ftp(client)
    library = await client.post(
        WORKOUTS, json={"name": "Easy hour", "structure": EASY_RIDE}
    )
    assert library.status_code == 201, library.text
    workout_id = library.json()["id"]
    session = await plan(client, structure=None, workout_id=workout_id)

    outcome = await propose(
        db_session,
        [
            UpdateChange(
                planned_session_id=uuid.UUID(session["id"]),
                expected_intent_version=session["intent"]["version"],
                updates={"coach_notes": "spin easy"},
            )
        ],
        dry_run=True,
    )

    [entry] = outcome.diff
    assert entry["before"]["workout_id"] == workout_id
    assert entry["after"]["workout_id"] == workout_id
    # Everything the change does not name is carried through unchanged, the
    # predicted load included: it is priced against the session's own pins.
    assert entry["after"] == {**entry["before"], "coach_notes": "spin easy"}


# --- supersede -------------------------------------------------------------------


async def test_a_second_proposal_about_one_session_supersedes_the_first(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    session = await plan(client)
    target = uuid.UUID(session["id"])
    first = await propose(
        db_session,
        [
            MoveChange(
                planned_session_id=target,
                expected_intent_version=1,
                date=dt.date(2026, 8, 12),
            )
        ],
    )

    second = await propose(
        db_session,
        [DeleteChange(planned_session_id=target, expected_intent_version=1)],
    )

    assert first.proposal is not None
    assert second.proposal is not None
    assert second.superseded == (first.proposal,)
    old, new = await stored(db_session)
    assert old.status is ProposalStatus.SUPERSEDED
    assert old.superseded_by_id == new.id
    assert new.supersedes_id == old.id
    assert new.status is ProposalStatus.PENDING
    assert "plan_proposal.superseded" in await audit_actions(db_session)


async def test_a_proposal_about_another_session_supersedes_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    one = await plan(client)
    other = await plan(client, date="2026-08-11")
    await propose(
        db_session,
        [
            MoveChange(
                planned_session_id=uuid.UUID(one["id"]),
                expected_intent_version=1,
                date=dt.date(2026, 8, 12),
            )
        ],
    )

    second = await propose(
        db_session,
        [
            DeleteChange(
                planned_session_id=uuid.UUID(other["id"]), expected_intent_version=1
            )
        ],
    )

    assert second.superseded == ()
    assert [row.status for row in await stored(db_session)] == [
        ProposalStatus.PENDING,
        ProposalStatus.PENDING,
    ]


# --- the inbox -------------------------------------------------------------------


async def test_the_inbox_lists_proposals_and_filters_by_status(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    session = await plan(client)
    outcome = await propose(
        db_session,
        [
            MoveChange(
                planned_session_id=uuid.UUID(session["id"]),
                expected_intent_version=1,
                date=dt.date(2026, 8, 12),
            )
        ],
    )
    assert outcome.proposal is not None

    listing = await client.get(PROPOSALS, params={"status": "pending"})

    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == str(outcome.proposal.id)
    assert body["items"][0]["diff"][0]["kind"] == "move"
    assert (await client.get(PROPOSALS, params={"status": "accepted"})).json()[
        "total"
    ] == 0


async def test_an_unknown_proposal_is_a_404(client: AsyncClient) -> None:
    assert (await client.get(f"{PROPOSALS}/{uuid.uuid7()}")).status_code == 404


async def test_the_inbox_needs_a_session(anon_client: AsyncClient) -> None:
    assert (await anon_client.get(PROPOSALS)).status_code == 401


# --- accepting -------------------------------------------------------------------


async def test_accepting_applies_every_change_and_audits_each_one(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    monday = await plan(client)
    tuesday = await plan(client, date="2026-08-11")
    outcome = await propose(
        db_session,
        [
            MoveChange(
                planned_session_id=uuid.UUID(monday["id"]),
                expected_intent_version=1,
                date=dt.date(2026, 8, 13),
            ),
            UpdateChange(
                planned_session_id=uuid.UUID(tuesday["id"]),
                expected_intent_version=1,
                updates={"purpose": "recovery"},
            ),
        ],
    )
    assert outcome.proposal is not None

    response = await client.post(f"{PROPOSALS}/{outcome.proposal.id}/accept")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "accepted"
    assert response.json()["resolved_at"] is not None
    moved = (await client.get(f"{SESSIONS}/{monday['id']}")).json()
    revised = (await client.get(f"{SESSIONS}/{tuesday['id']}")).json()
    assert moved["date"] == "2026-08-13"
    assert revised["intent"]["purpose"] == "recovery"
    assert revised["intent"]["version"] == 2
    actions = await audit_actions(db_session)
    # The plan change is recorded as the athlete's write (they decided) and
    # the proposal's part in it is recorded beside it (the agent suggested).
    assert actions.count("plan_proposal.change_applied") == 2
    assert "planned_session.moved" in actions
    assert "planned_session.intent_revised" in actions
    assert "plan_proposal.accepted" in actions


async def test_accepting_credits_the_athlete_and_records_who_suggested_it(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    session = await plan(client)
    outcome = await propose(
        db_session,
        [
            MoveChange(
                planned_session_id=uuid.UUID(session["id"]),
                expected_intent_version=1,
                date=dt.date(2026, 8, 12),
            )
        ],
    )
    assert outcome.proposal is not None

    await client.post(f"{PROPOSALS}/{outcome.proposal.id}/accept")

    db_session.expire_all()
    rows = (
        await db_session.execute(
            select(AuditLogEntry).where(
                AuditLogEntry.action == "plan_proposal.change_applied"
            )
        )
    ).scalars()
    [applied] = list(rows)
    assert applied.actor == "athlete"
    assert applied.payload_json["proposed_by"] == "agent:coach"


async def test_a_change_that_fails_rolls_the_whole_proposal_back(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Atomicity: the move is applied before the update is attempted, so a
    # failure in the second change must undo the first. The library workout
    # the second change names is deleted between proposing and accepting.
    await append_ftp(client)
    library = await client.post(
        WORKOUTS, json={"name": "Threshold hour", "structure": HARD_RIDE}
    )
    assert library.status_code == 201, library.text
    workout_id = library.json()["id"]
    monday = await plan(client)
    tuesday = await plan(client, date="2026-08-11")
    outcome = await propose(
        db_session,
        [
            MoveChange(
                planned_session_id=uuid.UUID(monday["id"]),
                expected_intent_version=1,
                date=dt.date(2026, 8, 13),
            ),
            UpdateChange(
                planned_session_id=uuid.UUID(tuesday["id"]),
                expected_intent_version=1,
                updates={"workout_id": workout_id},
            ),
        ],
    )
    assert outcome.proposal is not None
    assert (await client.delete(f"{WORKOUTS}/{workout_id}")).status_code == 204

    response = await client.post(f"{PROPOSALS}/{outcome.proposal.id}/accept")

    assert response.status_code == 404, response.text
    assert (await client.get(f"{SESSIONS}/{monday['id']}")).json()["date"] == (
        "2026-08-10"
    )
    [row] = await stored(db_session)
    assert row.status is ProposalStatus.PENDING
    assert "plan_proposal.change_applied" not in await audit_actions(db_session)


async def test_a_session_edited_since_makes_accepting_a_409_and_keeps_it_pending(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    session = await plan(client)
    outcome = await propose(
        db_session,
        [
            UpdateChange(
                planned_session_id=uuid.UUID(session["id"]),
                expected_intent_version=1,
                updates={"purpose": "recovery"},
            )
        ],
    )
    assert outcome.proposal is not None
    # The athlete edits the same session; the intent chain moves to version 2.
    edit = await client.patch(
        f"{SESSIONS}/{session['id']}", json={"intent_text": "steady spin"}
    )
    assert edit.status_code == 200, edit.text

    response = await client.post(f"{PROPOSALS}/{outcome.proposal.id}/accept")

    assert response.status_code == 409, response.text
    assert "version 2 is in force" in response.json()["detail"]
    [row] = await stored(db_session)
    # It stays pending: the suggestion may still be a good one against the
    # plan as it now stands, and deciding that is the agent's job.
    assert row.status is ProposalStatus.PENDING
    assert (await client.get(f"{SESSIONS}/{session['id']}")).json()["intent"][
        "purpose"
    ] == "endurance"


async def test_a_proposal_can_only_be_answered_once(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    session = await plan(client)
    outcome = await propose(
        db_session,
        [
            MoveChange(
                planned_session_id=uuid.UUID(session["id"]),
                expected_intent_version=1,
                date=dt.date(2026, 8, 12),
            )
        ],
    )
    assert outcome.proposal is not None
    assert (
        await client.post(f"{PROPOSALS}/{outcome.proposal.id}/accept")
    ).status_code == 200

    again = await client.post(f"{PROPOSALS}/{outcome.proposal.id}/accept")
    rejected = await client.post(
        f"{PROPOSALS}/{outcome.proposal.id}/reject", json={"reason": "no"}
    )

    assert again.status_code == 409
    assert "already accepted" in again.json()["detail"]
    assert rejected.status_code == 409


async def test_an_expired_proposal_cannot_be_accepted(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    session = await plan(client)
    outcome = await propose(
        db_session,
        [
            MoveChange(
                planned_session_id=uuid.UUID(session["id"]),
                expected_intent_version=1,
                date=dt.date(2026, 8, 12),
            )
        ],
    )
    assert outcome.proposal is not None
    outcome.proposal.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    await db_session.commit()

    response = await client.post(f"{PROPOSALS}/{outcome.proposal.id}/accept")

    assert response.status_code == 409
    assert "expired" in response.json()["detail"]
    assert (await client.get(f"{SESSIONS}/{session['id']}")).json()["date"] == (
        "2026-08-10"
    )


async def test_accepting_a_create_change_plans_the_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    outcome = await propose(
        db_session,
        [
            CreateChange(
                date=dt.date(2026, 8, 14),
                purpose=Purpose.ENDURANCE,
                structure=EASY_RIDE,
                coach_notes="keep it easy",
            )
        ],
    )
    assert outcome.proposal is not None

    response = await client.post(f"{PROPOSALS}/{outcome.proposal.id}/accept")

    assert response.status_code == 200, response.text
    listing = (await client.get(SESSIONS, params={"start": "2026-08-14"})).json()
    assert listing["total"] == 1
    assert listing["items"][0]["intent"]["coach_notes"] == "keep it easy"


# --- rejecting -------------------------------------------------------------------


async def test_rejecting_stores_the_reason_and_changes_no_plan(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    session = await plan(client)
    outcome = await propose(
        db_session,
        [
            DeleteChange(
                planned_session_id=uuid.UUID(session["id"]), expected_intent_version=1
            )
        ],
    )
    assert outcome.proposal is not None

    response = await client.post(
        f"{PROPOSALS}/{outcome.proposal.id}/reject",
        json={"reason": "I want to keep that ride."},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "rejected"
    assert body["resolution_note"] == "I want to keep that ride."
    assert (await client.get(f"{SESSIONS}/{session['id']}")).status_code == 200
    assert "plan_proposal.rejected" in await audit_actions(db_session)


async def test_a_reason_is_optional(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    session = await plan(client)
    outcome = await propose(
        db_session,
        [
            DeleteChange(
                planned_session_id=uuid.UUID(session["id"]), expected_intent_version=1
            )
        ],
    )
    assert outcome.proposal is not None

    response = await client.post(f"{PROPOSALS}/{outcome.proposal.id}/reject", json={})

    assert response.status_code == 200, response.text
    assert response.json()["resolution_note"] is None


# --- expiry ----------------------------------------------------------------------


async def test_the_sweep_lapses_the_oldest_deadlines_first_and_changes_no_plan(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await append_ftp(client)
    sessions = [
        await plan(client, date=day)
        for day in ("2026-08-10", "2026-08-11", "2026-08-12")
    ]
    rows: list[PlanProposalRow] = []
    for index, session in enumerate(sessions):
        outcome = await propose(
            db_session,
            [
                MoveChange(
                    planned_session_id=uuid.UUID(session["id"]),
                    expected_intent_version=1,
                    date=dt.date(2026, 8, 20),
                )
            ],
        )
        assert outcome.proposal is not None
        # Deadlines in the past, newest proposal expiring longest ago, so a
        # sweep that paged before filtering would take the wrong two.
        outcome.proposal.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(
            hours=index + 1
        )
        rows.append(outcome.proposal)
    await db_session.commit()
    monkeypatch.setenv("PROPOSALS__EXPIRY_BATCH", "2")
    get_settings.cache_clear()

    lapsed = await ProposalService.from_session(db_session).expire(actor=Actor.system())

    assert [row.id for row in lapsed] == [rows[2].id, rows[1].id]
    assert [row.status for row in lapsed] == [ProposalStatus.LAPSED] * 2
    assert rows[0].status is ProposalStatus.PENDING
    # Default on expiry: the committed plan stands — nothing was moved.
    assert (await client.get(f"{SESSIONS}/{sessions[0]['id']}")).json()["date"] == (
        "2026-08-10"
    )
    assert (await audit_actions(db_session)).count("plan_proposal.lapsed") == 2


async def test_the_sweep_leaves_a_standing_proposal_alone(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    session = await plan(client)
    await propose(
        db_session,
        [
            MoveChange(
                planned_session_id=uuid.UUID(session["id"]),
                expected_intent_version=1,
                date=dt.date(2026, 8, 12),
            )
        ],
    )

    assert (
        await ProposalService.from_session(db_session).expire(actor=Actor.system())
        == []
    )


async def test_an_expiry_deadline_in_the_past_is_refused_at_write_time(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    session = await plan(client)

    with pytest.raises(ValidationError, match="must be in the future"):
        await propose(
            db_session,
            [
                DeleteChange(
                    planned_session_id=uuid.UUID(session["id"]),
                    expected_intent_version=1,
                )
            ],
            hours=-1,
        )


# --- resolved by reality ---------------------------------------------------------


async def test_a_session_on_the_proposed_day_resolves_the_proposal(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    session = await plan(client, purpose="max_strength", structure=LIFT)
    outcome = await propose(
        db_session,
        [
            UpdateChange(
                planned_session_id=uuid.UUID(session["id"]),
                expected_intent_version=1,
                updates={"purpose": "mobility"},
            )
        ],
    )
    assert outcome.proposal is not None

    # The athlete trained instead of answering. A typed-in gym session is a
    # strength session on that local date, which is exactly what the proposal
    # was asking about.
    logged = await client.post(
        MANUAL,
        json={
            "start_time": "2026-08-10T17:00:00Z",
            "timezone": "UTC",
            "duration_s": 3_600,
            "discipline": "strength",
        },
    )

    assert logged.status_code == 201, logged.text
    [row] = await stored(db_session)
    assert row.status is ProposalStatus.RESOLVED_BY_REALITY
    assert "plan_proposal.resolved_by_reality" in await audit_actions(db_session)


async def test_a_failing_resolution_still_answers_the_manual_entry(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The session is committed before the hook runs, so a failure in it must
    # cost the athlete a stale inbox row and nothing else. The rollback that
    # follows expires every instance in the session, so the caller has to be
    # handed a freshly loaded row — matching runs next, and reading even
    # `row.id` off an expired handle is a lazy read (`MissingGreenlet`, 500).
    async def explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the proposal service fell over")

    monkeypatch.setattr("app.services.activity.resolve_proposals_for_session", explode)

    logged = await client.post(
        MANUAL,
        json={
            "start_time": "2026-08-10T17:00:00Z",
            "timezone": "UTC",
            "duration_s": 3_600,
            "discipline": "strength",
        },
    )

    assert logged.status_code == 201, logged.text
    db_session.expire_all()
    sessions = (await db_session.execute(select(SessionRow))).scalars().all()
    assert len(sessions) == 1, "a retry of a 500 would have made a second one"
    assert str(sessions[0].id) == logged.json()["id"]


async def test_a_discipline_changing_update_is_resolved_by_the_old_discipline(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Turning Monday's ride into a lift is a statement about Monday's *ride*
    # too: an athlete who rides that Monday has answered the question, and
    # matching on the after discipline alone would leave the proposal standing
    # over a day that is already spent.
    await append_ftp(client)
    session = await plan(client)
    outcome = await propose(
        db_session,
        [
            UpdateChange(
                planned_session_id=uuid.UUID(session["id"]),
                expected_intent_version=1,
                updates={"purpose": "max_strength", "structure": LIFT},
            )
        ],
    )
    assert outcome.proposal is not None
    [entry] = outcome.diff
    assert entry["before"]["discipline"] == "cycling"
    assert entry["after"]["discipline"] == "strength"

    resolved = await ProposalService.from_session(db_session).resolve_by_reality(
        actor=Actor.system(), date=dt.date(2026, 8, 10), discipline=Discipline.CYCLING
    )

    assert [row.id for row in resolved] == [outcome.proposal.id]
    assert resolved[0].status is ProposalStatus.RESOLVED_BY_REALITY


async def test_another_day_resolves_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    session = await plan(client, purpose="max_strength", structure=LIFT)
    await propose(
        db_session,
        [
            UpdateChange(
                planned_session_id=uuid.UUID(session["id"]),
                expected_intent_version=1,
                updates={"purpose": "mobility"},
            )
        ],
    )

    resolved = await ProposalService.from_session(db_session).resolve_by_reality(
        actor=Actor.system(), date=dt.date(2026, 8, 11), discipline=Discipline.STRENGTH
    )

    assert resolved == []
    [row] = await stored(db_session)
    assert row.status is ProposalStatus.PENDING


async def test_another_discipline_resolves_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    session = await plan(client)

    await propose(
        db_session,
        [
            MoveChange(
                planned_session_id=uuid.UUID(session["id"]),
                expected_intent_version=1,
                date=dt.date(2026, 8, 12),
            )
        ],
    )
    resolved = await ProposalService.from_session(db_session).resolve_by_reality(
        actor=Actor.system(), date=dt.date(2026, 8, 10), discipline=Discipline.STRENGTH
    )

    assert resolved == []


async def test_a_move_is_resolved_by_a_session_on_either_of_its_days(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Moving a session off Monday is a statement about Monday as much as
    # about the day it lands on.
    await append_ftp(client)
    session = await plan(client)
    await propose(
        db_session,
        [
            MoveChange(
                planned_session_id=uuid.UUID(session["id"]),
                expected_intent_version=1,
                date=dt.date(2026, 8, 12),
            )
        ],
    )

    resolved = await ProposalService.from_session(db_session).resolve_by_reality(
        actor=Actor.system(), date=dt.date(2026, 8, 12), discipline=Discipline.CYCLING
    )

    assert len(resolved) == 1


# --- the rate cap ----------------------------------------------------------------


async def test_the_agent_is_capped_and_the_athlete_is_not(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await append_ftp(client)
    session = await plan(client)
    monkeypatch.setenv("MCP__WRITE_CAP_PER_HOUR", "1")
    get_settings.cache_clear()

    def change(day: int) -> list[PlanChange]:
        return [
            MoveChange(
                planned_session_id=uuid.UUID(session["id"]),
                expected_intent_version=1,
                date=dt.date(2026, 8, day),
            )
        ]

    # The first agent write lands and leaves audit rows behind it.
    assert (await propose(db_session, change(12))).proposal is not None

    with pytest.raises(RateLimitedError, match="cap of 1 per hour"):
        await propose(db_session, change(13))
    # A dry run writes nothing, so it costs nothing.
    assert (await propose(db_session, change(13), dry_run=True)).diff
    # And the athlete is never capped on their own plan.
    assert (
        await propose(db_session, change(14), actor=Actor.athlete())
    ).proposal is not None


# --- the red flag ----------------------------------------------------------------


async def test_a_create_is_refused_while_the_flag_is_up(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    await raise_red_flag(client)

    with pytest.raises(RedFlagError, match="not on the calendar"):
        await propose(
            db_session,
            [
                CreateChange(
                    date=dt.date(2026, 8, 14),
                    purpose=Purpose.ENDURANCE,
                    structure=EASY_RIDE,
                )
            ],
        )
    assert await stored(db_session) == []


async def test_a_purpose_raising_update_is_refused_while_the_flag_is_up(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    session = await plan(client)
    await raise_red_flag(client, severity="severe")

    with pytest.raises(RedFlagError, match="raises the purpose from endurance"):
        await propose(
            db_session,
            [
                UpdateChange(
                    planned_session_id=uuid.UUID(session["id"]),
                    expected_intent_version=1,
                    updates={"purpose": "vo2max", "structure": EASY_RIDE},
                )
            ],
        )


async def test_a_load_raising_update_is_refused_while_the_flag_is_up(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Same purpose, same hour, much harder targets: the purpose rank says
    # nothing and the predicted load says everything.
    await append_ftp(client)
    session = await plan(client)
    await raise_red_flag(client)

    with pytest.raises(RedFlagError, match="raises the predicted load"):
        await propose(
            db_session,
            [
                UpdateChange(
                    planned_session_id=uuid.UUID(session["id"]),
                    expected_intent_version=1,
                    updates={"structure": HARD_RIDE},
                )
            ],
        )


@pytest.mark.parametrize("kind", ["move", "delete", "reduce"])
async def test_lightening_the_plan_is_always_allowed(
    client: AsyncClient, db_session: AsyncSession, kind: str
) -> None:
    await append_ftp(client)
    session = await plan(client, purpose="threshold", structure=HARD_RIDE)
    await raise_red_flag(client)
    target = uuid.UUID(session["id"])
    change: PlanChange = {
        "move": MoveChange(
            planned_session_id=target,
            expected_intent_version=1,
            date=dt.date(2026, 8, 12),
        ),
        "delete": DeleteChange(planned_session_id=target, expected_intent_version=1),
        "reduce": UpdateChange(
            planned_session_id=target,
            expected_intent_version=1,
            updates={"purpose": "recovery", "structure": EASY_RIDE},
        ),
    }[kind]

    outcome = await propose(db_session, [change])

    assert outcome.proposal is not None


async def test_clearing_the_flag_unblocks_the_agent(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    await raise_red_flag(client)
    create = CreateChange(
        date=dt.date(2026, 8, 14), purpose=Purpose.ENDURANCE, structure=EASY_RIDE
    )
    with pytest.raises(RedFlagError):
        await propose(db_session, [create])

    # One field: lowering the flag retracts the note and the grade with it.
    cleared = await client.patch(ATHLETE, json={"red_flag_active": False})

    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["red_flag_severity"] is None
    assert cleared.json()["red_flag_note"] is None
    assert (await propose(db_session, [create])).proposal is not None


async def test_the_flag_restrains_the_agent_and_not_the_athlete(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # An athlete who is ill may still plan whatever they like — for
    # themselves, and through their own endpoints.
    await append_ftp(client)
    await raise_red_flag(client)

    outcome = await propose(
        db_session,
        [
            CreateChange(
                date=dt.date(2026, 8, 14),
                purpose=Purpose.VO2MAX,
                structure=HARD_RIDE,
            )
        ],
        actor=Actor.athlete(),
    )

    assert outcome.proposal is not None
    assert (
        await plan(client, date="2026-08-15", purpose="vo2max", structure=HARD_RIDE)
    )["id"]


# --- the scheduled sweep ---------------------------------------------------------


async def test_the_expiry_sweep_is_registered_at_startup(data_root: Path) -> None:
    app = create_app()

    async with LifespanManager(app):
        job = app.state.scheduler.get_job(EXPIRY_JOB_ID)

        assert job is not None
        assert job.trigger.interval.total_seconds() == (
            get_settings().proposals.expiry_interval_seconds
        )
        assert job.next_run_time is not None


async def test_the_scheduled_sweep_lapses_through_its_own_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # The job opens `session_scope()` rather than borrowing a request's
    # session; the fixture binds that process-wide, which is what this proves.
    await append_ftp(client)
    session = await plan(client)
    outcome = await propose(
        db_session,
        [
            MoveChange(
                planned_session_id=uuid.UUID(session["id"]),
                expected_intent_version=1,
                date=dt.date(2026, 8, 12),
            )
        ],
    )
    assert outcome.proposal is not None
    outcome.proposal.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    await db_session.commit()

    await run_proposal_expiry()

    [row] = await stored(db_session)
    assert row.status is ProposalStatus.LAPSED


# --- what the agent surface still cannot reach -----------------------------------


async def test_no_proposal_can_declare_a_session_completed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # D174: a planned session's status is derived from what the athlete did.
    # The red flag cannot catch this one either — a status carries neither a
    # purpose nor a load — so the vocabulary refuses it instead.
    await append_ftp(client)
    session = await plan(client)
    await raise_red_flag(client)

    with pytest.raises(ValueError, match="status is not a proposable field"):
        UpdateChange(
            planned_session_id=uuid.UUID(session["id"]),
            expected_intent_version=1,
            updates={"status": "completed"},
        )
    assert (await client.get(f"{SESSIONS}/{session['id']}")).json()["status"] == (
        "planned"
    )
    assert await stored(db_session) == []


def test_no_plan_change_can_touch_an_anchor() -> None:
    # Invariant 3: anchors are append-only, and WP-8 adds no path around it.
    # The change vocabulary names planned sessions and nothing else, and the
    # anchor service still offers no way to alter one.
    assert not [field for field in UPDATE_FIELDS if "anchor" in field]
    assert not [
        name
        for name in dir(AnchorService)
        if name in {"update", "delete", "revise", "amend", "remove"}
    ]

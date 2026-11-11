"""Plans over HTTP (AC-37): phase, follow-exactly, overlap, week tags.

A plan is the container planned sessions belong to. The default plan — the one
migration `0028` moves every existing session into — is pinned in
`tests/integration/test_plans_migration.py`; here it appears only as what a
fresh unit database creates on the first planned session, and as the one plan
the overlap rule does not apply to.
"""

import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.audit import AuditLogEntry
from tests.unit.mcp_harness import connected_as, server_for

PLANS = "/api/v1/plans"
SESSIONS = "/api/v1/planned-sessions"

#: A read-only MCP key, for the coach's view of the same plan.
READER = f"reader:read:{'a1b2c3d4' * 4}"

#: A strength session needs no anchor, so it plans without any setup.
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


async def create_plan(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    """Create a plan, asserting it was accepted."""
    payload: dict[str, Any] = {
        "name": "Spring block",
        "phase": "build",
        "follow_exactly": False,
        "starts_on": "2026-03-02",
        "ends_before": "2026-05-04",
    } | overrides
    response = await client.post(PLANS, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def plan_audit(session: AsyncSession, plan_id: str) -> list[str]:
    """Every audit action written about one plan, oldest first."""
    result = await session.execute(
        select(AuditLogEntry.action)
        .where(AuditLogEntry.entity_type == "plan")
        .where(AuditLogEntry.entity_id == uuid.UUID(plan_id))
        .order_by(AuditLogEntry.id)
    )
    return list(result.scalars())


# --- AC-37: create, read, patch -------------------------------------------------


async def test_a_peak_plan_followed_exactly_reads_back_with_both(
    client: AsyncClient,
) -> None:
    created = await create_plan(client, phase="peak", follow_exactly=True)

    response = await client.get(f"{PLANS}/{created['id']}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["phase"] == "peak"
    assert body["follow_exactly"] is True
    assert body["name"] == "Spring block"
    assert body["active"] is True
    assert body["is_default"] is False
    assert body["starts_on"] == "2026-03-02"
    assert body["ends_before"] == "2026-05-04"
    assert body["created_at"]


async def test_changing_the_phase_writes_exactly_one_audit_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    created = await create_plan(client, phase="peak", follow_exactly=True)

    response = await client.patch(
        f"{PLANS}/{created['id']}", json={"phase": "maintenance"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["phase"] == "maintenance"
    # Untouched fields stay as they were.
    assert response.json()["follow_exactly"] is True
    assert await plan_audit(db_session, created["id"]) == [
        "plan.created",
        "plan.updated",
    ]
    entry = (
        await db_session.execute(
            select(AuditLogEntry)
            .where(AuditLogEntry.action == "plan.updated")
            .where(AuditLogEntry.entity_id == uuid.UUID(created["id"]))
        )
    ).scalar_one()
    assert entry.payload_json == {
        "changed": {"phase": {"from": "peak", "to": "maintenance"}}
    }


async def test_a_patch_that_changes_nothing_writes_no_audit_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    created = await create_plan(client, phase="peak")

    response = await client.patch(f"{PLANS}/{created['id']}", json={"phase": "peak"})

    assert response.status_code == 200, response.text
    assert await plan_audit(db_session, created["id"]) == ["plan.created"]


async def test_an_unknown_phase_is_refused_on_create(client: AsyncClient) -> None:
    response = await client.post(
        PLANS, json={"name": "Offseason", "phase": "offseason"}
    )

    assert response.status_code == 422, response.text


async def test_an_unknown_phase_is_refused_on_patch(client: AsyncClient) -> None:
    created = await create_plan(client)

    response = await client.patch(
        f"{PLANS}/{created['id']}", json={"phase": "offseason"}
    )

    assert response.status_code == 422, response.text
    assert (await client.get(f"{PLANS}/{created['id']}")).json()["phase"] == "build"


async def test_an_unknown_plan_is_a_404(client: AsyncClient) -> None:
    missing = "0190f0f0-0000-7000-8000-000000000000"

    assert (await client.get(f"{PLANS}/{missing}")).status_code == 404
    assert (
        await client.patch(f"{PLANS}/{missing}", json={"phase": "peak"})
    ).status_code == 404


async def test_the_list_returns_every_plan(client: AsyncClient) -> None:
    first = await create_plan(client)
    second = await create_plan(
        client, name="Autumn", starts_on="2026-09-07", ends_before="2026-11-02"
    )

    response = await client.get(PLANS)

    assert response.status_code == 200, response.text
    ids = [plan["id"] for plan in response.json()]
    assert first["id"] in ids
    assert second["id"] in ids


async def test_a_plan_whose_end_is_not_after_its_start_is_refused(
    client: AsyncClient,
) -> None:
    response = await client.post(
        PLANS,
        json={
            "name": "Backwards",
            "phase": "base",
            "starts_on": "2026-05-04",
            "ends_before": "2026-05-04",
        },
    )

    assert response.status_code == 422, response.text


# --- AC-37 edge: the guard -----------------------------------------------------


async def test_every_plan_route_needs_a_session(anon_client: AsyncClient) -> None:
    some_id = "0190f0f0-0000-7000-8000-000000000000"

    assert (await anon_client.get(PLANS)).status_code == 401
    assert (
        await anon_client.post(PLANS, json={"name": "x", "phase": "peak"})
    ).status_code == 401
    assert (await anon_client.get(f"{PLANS}/{some_id}")).status_code == 401
    assert (
        await anon_client.patch(f"{PLANS}/{some_id}", json={"phase": "peak"})
    ).status_code == 401
    assert (
        await anon_client.put(
            f"{PLANS}/{some_id}/weeks/2026-03-02", json={"tags": ["recovery"]}
        )
    ).status_code == 401


# --- AC-37 edge: two active plans may not overlap ------------------------------


async def test_two_active_plans_with_overlapping_dates_conflict(
    client: AsyncClient,
) -> None:
    first = await create_plan(client)

    response = await client.post(
        PLANS,
        json={
            "name": "Overlapping",
            "phase": "peak",
            "starts_on": "2026-05-01",
            "ends_before": "2026-06-01",
        },
    )

    assert response.status_code == 409, response.text
    assert first["id"] in response.json()["detail"]
    names = [plan["name"] for plan in (await client.get(PLANS)).json()]
    assert "Overlapping" not in names


async def test_an_open_ended_active_plan_overlaps_everything_after_its_start(
    client: AsyncClient,
) -> None:
    await create_plan(client, starts_on="2026-03-02", ends_before=None)

    response = await client.post(
        PLANS,
        json={"name": "Later", "phase": "peak", "starts_on": "2027-01-04"},
    )

    assert response.status_code == 409, response.text


async def test_adjacent_plans_do_not_overlap(client: AsyncClient) -> None:
    """Ranges are half-open: a plan ending before the 4th and one starting on it
    share a boundary and no day."""
    await create_plan(client)

    await create_plan(
        client, name="Next", starts_on="2026-05-04", ends_before="2026-06-01"
    )


async def test_an_inactive_plan_may_overlap_an_active_one(
    client: AsyncClient,
) -> None:
    await create_plan(client)

    draft = await create_plan(client, name="Draft", active=False)

    assert draft["active"] is False


async def test_activating_an_overlapping_plan_conflicts(client: AsyncClient) -> None:
    await create_plan(client)
    draft = await create_plan(client, name="Draft", active=False)

    response = await client.patch(f"{PLANS}/{draft['id']}", json={"active": True})

    assert response.status_code == 409, response.text
    assert (await client.get(f"{PLANS}/{draft['id']}")).json()["active"] is False


async def test_moving_a_plans_dates_into_another_conflicts(
    client: AsyncClient,
) -> None:
    await create_plan(client)
    later = await create_plan(
        client, name="Later", starts_on="2026-06-01", ends_before="2026-07-06"
    )

    response = await client.patch(
        f"{PLANS}/{later['id']}", json={"starts_on": "2026-04-06"}
    )

    assert response.status_code == 409, response.text


async def test_the_default_plan_does_not_count_as_overlapping(
    client: AsyncClient,
) -> None:
    """The default plan is undated and catches every date no other plan covers,
    so a dated plan carves out of it rather than colliding with it."""
    session = await client.post(
        SESSIONS,
        json={"date": "2026-03-10", "purpose": "max_strength", "structure": LIFT},
    )
    assert session.status_code == 201, session.text
    [default] = (await client.get(PLANS)).json()
    assert default["is_default"] is True

    await create_plan(client)


async def test_the_default_plan_stays_undated_and_active(
    client: AsyncClient,
) -> None:
    await client.post(
        SESSIONS,
        json={"date": "2026-03-10", "purpose": "max_strength", "structure": LIFT},
    )
    [default] = (await client.get(PLANS)).json()

    dated = await client.patch(
        f"{PLANS}/{default['id']}", json={"starts_on": "2026-03-02"}
    )
    paused = await client.patch(f"{PLANS}/{default['id']}", json={"active": False})
    phased = await client.patch(f"{PLANS}/{default['id']}", json={"phase": "peak"})

    assert dated.status_code == 422, dated.text
    assert paused.status_code == 422, paused.text
    assert phased.status_code == 200, phased.text
    assert phased.json()["phase"] == "peak"


# --- planned sessions join a plan ----------------------------------------------


async def test_a_session_joins_the_active_plan_covering_its_date(
    client: AsyncClient,
) -> None:
    block = await create_plan(client)

    inside = await client.post(
        SESSIONS,
        json={"date": "2026-03-10", "purpose": "max_strength", "structure": LIFT},
    )
    outside = await client.post(
        SESSIONS,
        json={"date": "2026-08-10", "purpose": "max_strength", "structure": LIFT},
    )

    assert inside.json()["plan_id"] == block["id"]
    default = next(p for p in (await client.get(PLANS)).json() if p["is_default"])
    assert outside.json()["plan_id"] == default["id"]


# --- plan-week tags ------------------------------------------------------------


async def test_a_week_can_be_tagged_recovery(client: AsyncClient) -> None:
    block = await create_plan(client)

    response = await client.put(
        f"{PLANS}/{block['id']}/weeks/2026-03-09", json={"tags": ["recovery"]}
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "plan_id": block["id"],
        "week_start": "2026-03-09",
        "tags": ["recovery"],
    }
    read = await client.get(f"{PLANS}/{block['id']}/weeks/2026-03-09")
    assert read.json()["tags"] == ["recovery"]
    untagged = await client.get(f"{PLANS}/{block['id']}/weeks/2026-03-16")
    assert untagged.json()["tags"] == []


async def test_clearing_a_weeks_tags_leaves_none(client: AsyncClient) -> None:
    block = await create_plan(client)
    url = f"{PLANS}/{block['id']}/weeks/2026-03-09"
    await client.put(url, json={"tags": ["recovery"]})

    response = await client.put(url, json={"tags": []})

    assert response.json()["tags"] == []
    assert (await client.get(url)).json()["tags"] == []


async def test_a_week_is_keyed_by_its_monday(client: AsyncClient) -> None:
    block = await create_plan(client)

    response = await client.put(
        f"{PLANS}/{block['id']}/weeks/2026-03-11", json={"tags": ["recovery"]}
    )

    assert response.status_code == 422, response.text


async def test_an_unknown_week_tag_is_refused(client: AsyncClient) -> None:
    block = await create_plan(client)

    response = await client.put(
        f"{PLANS}/{block['id']}/weeks/2026-03-09", json={"tags": ["deload-ish"]}
    )

    assert response.status_code == 422, response.text


async def test_tagging_a_week_is_audited(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    block = await create_plan(client)

    await client.put(
        f"{PLANS}/{block['id']}/weeks/2026-03-09", json={"tags": ["recovery"]}
    )

    assert await plan_audit(db_session, block["id"]) == [
        "plan.created",
        "plan.week_tagged",
    ]


# --- the coach sees it -----------------------------------------------------------


async def test_the_coach_reads_the_weeks_plan_phase_and_tags(
    client: AsyncClient,
) -> None:
    block = await create_plan(client, phase="peak", follow_exactly=True)
    await client.put(
        f"{PLANS}/{block['id']}/weeks/2026-03-09", json={"tags": ["recovery"]}
    )
    session = await client.post(
        SESSIONS,
        json={"date": "2026-03-10", "purpose": "max_strength", "structure": LIFT},
    )

    async with connected_as(server_for(READER), READER) as mcp:
        week = (await mcp.call_tool("get_plan_week", {"start": "2026-03-09"})).data

    assert week["week"]["plan"] == {
        "id": block["id"],
        "name": "Spring block",
        "phase": "peak",
        "follow_exactly": True,
        "is_default": False,
        "week_tags": ["recovery"],
    }
    [card] = week["week"]["sessions"]
    assert card["id"] == session.json()["id"]
    assert card["plan_id"] == block["id"]

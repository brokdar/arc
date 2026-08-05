"""Planned sessions through HTTP, and the freeze rule (build-plan invariant 4).

The three cases the plan states, each pinned here:

* creating a session pins the anchor versions in force,
* an intent edit **before** a match exists writes a new version and re-pins,
* an intent edit **after** a match exists writes a new version, flags it
  ``edited_post_hoc``, keeps the pins, and triggers a rescore.

Matching (WP-6) and scoring (WP-7) do not exist yet, so the service exposes
both as explicit seams. These tests drive them, which is the point of having
seams rather than silence: the machinery is complete and exercised now.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.versioning import current_version, next_version
from app.persistence.audit import AuditLogEntry
from app.persistence.planned_sessions import (
    PlannedSessionIntentRow,
    PlannedSessionRow,
)
from app.services.planned_sessions import set_match_probe, set_rescore_trigger

SESSIONS = "/api/v1/planned-sessions"
ANCHORS = "/api/v1/anchors"
WORKOUTS = "/api/v1/workouts"

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

ABSOLUTE_RIDE: dict[str, Any] = {
    "discipline": "cycling",
    "steps": [
        {
            "kind": "steady",
            "duration_s": 3_600,
            "targets": {
                "power": {"kind": "absolute", "low": 180, "high": 200, "unit": "W"}
            },
        }
    ],
}


def hr_step(anchor: str) -> dict[str, Any]:
    """A steady step whose heart-rate target is a percentage of ``anchor``."""
    return {
        "kind": "steady",
        "duration_s": 900,
        "targets": {
            "hr": {
                "kind": "percent_of_anchor",
                "anchor_type": anchor,
                "pct_low": 0.8,
                "pct_high": 0.85,
            }
        },
    }


#: Refers to ftp and lthr; the pair `technique` (a template with no anchor of
#: its own) needs to show which pins an edit keeps, drops and adds.
FTP_AND_LTHR_RIDE: dict[str, Any] = {
    "discipline": "cycling",
    "steps": [
        {
            "kind": "steady",
            "duration_s": 900,
            "targets": {
                "power": {
                    "kind": "percent_of_anchor",
                    "anchor_type": "ftp",
                    "pct_low": 0.6,
                    "pct_high": 0.7,
                }
            },
        },
        hr_step("lthr"),
    ],
}

#: The same ride re-prescribed: lthr survives, ftp goes, max_hr arrives.
LTHR_AND_MAX_HR_RIDE: dict[str, Any] = {
    "discipline": "cycling",
    "steps": [hr_step("lthr"), hr_step("max_hr")],
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
                    "load": {"kind": "percent_e1rm", "value": 0.85},
                }
            ]
        }
    ],
}


@pytest.fixture
def matched() -> Iterator[None]:
    """Pretend WP-6 has matched an activity to every planned session."""

    async def always(_session: AsyncSession, _planned_session_id: uuid.UUID) -> bool:
        return True

    set_match_probe(always)
    yield
    set_match_probe(None)


@pytest.fixture
def rescores() -> Iterator[list[tuple[uuid.UUID, int]]]:
    """Record what WP-7's rescore trigger would have been asked to do."""
    calls: list[tuple[uuid.UUID, int]] = []

    async def record(
        _session: AsyncSession, planned_session_id: uuid.UUID, version: int
    ) -> None:
        calls.append((planned_session_id, version))

    set_rescore_trigger(record)
    yield calls
    set_rescore_trigger(None)


async def append_anchor(client: AsyncClient, anchor_type: str, value: float) -> str:
    """Append an anchor version and return its id."""
    response = await client.post(
        ANCHORS,
        json={
            "anchor_type": anchor_type,
            "value": value,
            "provenance": "estimated",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def append_ftp(client: AsyncClient, value: float = 250) -> str:
    """Append an FTP anchor and return its version id."""
    return await append_anchor(client, "ftp", value)


async def plan(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    """Plan a session, asserting it was accepted."""
    payload: dict[str, Any] = {
        "date": "2026-08-10",
        "purpose": "sweet_spot",
        "structure": RIDE,
    } | overrides
    response = await client.post(SESSIONS, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def audit_actions(session: AsyncSession) -> list[str]:
    """Every audit action so far, oldest first."""
    result = await session.execute(
        select(AuditLogEntry.action).order_by(AuditLogEntry.at, AuditLogEntry.id)
    )
    return list(result.scalars())


# --- creation: pinning and derivation -----------------------------------------


async def test_creation_pins_the_anchor_version_in_force(
    client: AsyncClient,
) -> None:
    await append_ftp(client, 240)
    in_force = await append_ftp(client, 255)

    session = await plan(client)

    assert session["intent"]["pinned_anchor_versions"] == {"ftp": in_force}


async def test_a_prescription_with_no_anchor_in_force_is_refused(
    client: AsyncClient,
) -> None:
    # An unresolvable prescription is not a plan: the targets are percentages
    # of a number nobody has entered.
    response = await client.post(
        SESSIONS,
        json={"date": "2026-08-10", "purpose": "sweet_spot", "structure": RIDE},
    )

    assert response.status_code == 422
    assert "no ftp anchor is in force" in response.json()["detail"]


# The refusal names *which half* of the prescription needs the anchor, because
# the two halves are edited in different places: the targets are what the
# planner wrote, and the criteria usually arrive from the purpose template. One
# wording for both sent an athlete who had prescribed nothing but absolute
# watts off to "prescribe absolute targets".


@pytest.mark.parametrize(
    ("purpose", "structure", "expected"),
    [
        pytest.param(
            "sweet_spot",
            RIDE,
            "This prescription is expressed as a percentage of ftp",
            id="targets-only",
        ),
        pytest.param(
            "endurance",
            ABSOLUTE_RIDE,
            "The success criteria (from the purpose template, editable) reference ftp",
            id="criteria-only",
        ),
        pytest.param(
            "endurance",
            RIDE,
            "This prescription's targets and its success criteria (from the "
            "purpose template, editable) both reference ftp",
            id="both",
        ),
    ],
)
async def test_the_missing_anchor_refusal_names_what_asked_for_it(
    client: AsyncClient, purpose: str, structure: dict[str, Any], expected: str
) -> None:
    response = await client.post(
        SESSIONS,
        json={"date": "2026-08-10", "purpose": purpose, "structure": structure},
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert expected in detail
    assert "no ftp anchor is in force" in detail


@pytest.mark.parametrize(
    ("purpose", "structure", "remedy"),
    [
        ("sweet_spot", RIDE, "or prescribe absolute targets."),
        ("endurance", ABSOLUTE_RIDE, "or edit the criteria."),
        ("endurance", RIDE, "or prescribe absolute targets and edit the criteria."),
    ],
)
async def test_the_missing_anchor_refusal_stays_actionable(
    client: AsyncClient, purpose: str, structure: dict[str, Any], remedy: str
) -> None:
    response = await client.post(
        SESSIONS,
        json={"date": "2026-08-10", "purpose": purpose, "structure": structure},
    )

    detail = response.json()["detail"]
    assert "Append one before planning the session" in detail
    assert detail.endswith(remedy)


async def test_an_absolute_prescription_needs_no_anchor(client: AsyncClient) -> None:
    # `technique`'s template asks only for a duration floor, so nothing in
    # this session — targets or criteria — refers to an anchor.
    session = await plan(client, structure=ABSOLUTE_RIDE, purpose="technique")

    assert session["intent"]["pinned_anchor_versions"] == {}


async def test_a_template_ceiling_is_an_anchor_the_session_must_pin(
    client: AsyncClient,
) -> None:
    # The `endurance` template caps power at 100 % FTP, so even an absolutely
    # prescribed endurance ride needs an FTP: the criterion is as much part of
    # the frozen prescription as a target is.
    response = await client.post(
        SESSIONS,
        json={"date": "2026-08-10", "purpose": "endurance", "structure": ABSOLUTE_RIDE},
    )
    assert response.status_code == 422

    await append_ftp(client)
    session = await plan(client, structure=ABSOLUTE_RIDE, purpose="endurance")

    assert set(session["intent"]["pinned_anchor_versions"]) == {"ftp"}


async def test_criteria_are_derived_from_the_purpose_template(
    client: AsyncClient,
) -> None:
    await append_ftp(client)

    session = await plan(client, purpose="sweet_spot")

    template = (await client.get("/api/v1/purposes/sweet_spot")).json()
    assert session["intent"]["success_criteria"] == template["default_criteria"]


async def test_supplied_criteria_replace_the_template_defaults(
    client: AsyncClient,
) -> None:
    await append_ftp(client)

    session = await plan(
        client,
        success_criteria=[{"kind": "duration_floor", "min_seconds": 900}],
    )

    assert session["intent"]["success_criteria"] == [
        {"kind": "duration_floor", "min_seconds": 900}
    ]


async def test_a_criterion_the_discipline_cannot_evaluate_is_refused(
    client: AsyncClient,
) -> None:
    await append_ftp(client)

    response = await client.post(
        SESSIONS,
        json={
            "date": "2026-08-10",
            "purpose": "sweet_spot",
            "structure": RIDE,
            "success_criteria": [{"kind": "sets_completed", "min_fraction": 0.9}],
        },
    )

    assert response.status_code == 422
    assert "cannot be evaluated for a cycling session" in response.json()["detail"]


async def test_the_purpose_must_match_the_prescription_discipline(
    client: AsyncClient,
) -> None:
    response = await client.post(
        SESSIONS,
        json={"date": "2026-08-10", "purpose": "hypertrophy", "structure": RIDE},
    )

    assert response.status_code == 422
    assert "strength purpose" in response.json()["detail"]


async def test_a_session_needs_exactly_one_prescription_source(
    client: AsyncClient,
) -> None:
    for payload in (
        {"date": "2026-08-10", "purpose": "endurance"},
        {
            "date": "2026-08-10",
            "purpose": "endurance",
            "structure": ABSOLUTE_RIDE,
            "workout_id": str(uuid.uuid4()),
        },
    ):
        response = await client.post(SESSIONS, json=payload)

        assert response.status_code == 422, response.text
        assert "exactly one of workout_id or structure" in response.json()["detail"]


async def test_planning_from_the_library_snapshots_the_prescription(
    client: AsyncClient,
) -> None:
    # The snapshot is what makes the frozen prescription survive a later edit
    # to the library entry — invariant 4 would be unenforceable otherwise.
    await append_ftp(client)
    workout = (
        await client.post(WORKOUTS, json={"name": "SS 3x8", "structure": RIDE})
    ).json()
    session = await plan(client, workout_id=workout["id"], structure=None)

    await client.patch(f"{WORKOUTS}/{workout['id']}", json={"structure": ABSOLUTE_RIDE})

    fetched = (await client.get(f"{SESSIONS}/{session['id']}")).json()
    assert fetched["intent"]["structure"] == session["intent"]["structure"]
    assert fetched["intent"]["workout_id"] == workout["id"]


async def test_deleting_the_library_entry_keeps_the_frozen_prescription(
    client: AsyncClient,
) -> None:
    await append_ftp(client)
    workout = (
        await client.post(WORKOUTS, json={"name": "SS 3x8", "structure": RIDE})
    ).json()
    session = await plan(client, workout_id=workout["id"], structure=None)

    await client.delete(f"{WORKOUTS}/{workout['id']}")

    fetched = (await client.get(f"{SESSIONS}/{session['id']}")).json()
    assert fetched["intent"]["workout_id"] is None
    assert fetched["intent"]["summary"]["step_count"] == 7


async def test_planning_from_an_unknown_workout_returns_404(
    client: AsyncClient,
) -> None:
    response = await client.post(
        SESSIONS,
        json={
            "date": "2026-08-10",
            "purpose": "endurance",
            "workout_id": str(uuid.uuid4()),
        },
    )

    assert response.status_code == 404


async def test_a_strength_session_can_be_planned(client: AsyncClient) -> None:
    session = await plan(client, purpose="max_strength", structure=LIFT)

    assert session["discipline"] == "strength"
    assert session["intent"]["summary"]["total_sets"] == 5
    assert {
        criterion["kind"] for criterion in session["intent"]["success_criteria"]
    } == {"sets_completed", "load_within"}


# --- the intent version chain -------------------------------------------------


async def test_version_one_is_an_original_computation(client: AsyncClient) -> None:
    await append_ftp(client)

    session = await plan(client)

    assert session["intent"]["version"] == 1
    assert session["intent"]["recompute_reason"] is None
    assert session["intent"]["superseded_by"] is None
    assert session["intent"]["edited_post_hoc"] is False
    assert session["intent_versions"] == 1


async def test_the_intent_row_satisfies_the_versioning_protocol() -> None:
    # WP-1's `VersionRecord` is structural precisely so persistence rows
    # satisfy it, and this is the assertion the pyrefly suppressions in
    # `app.persistence.planned_sessions` and `app.services.planned_sessions`
    # stand on: the mismatch pyrefly reports is that SQLAlchemy's `Mapped[X]`
    # attributes are not descriptors while the protocol's members are
    # properties. At runtime the chain helpers work on these rows exactly as
    # they do on the domain's own `Versioned`.
    artefact = uuid.uuid7()
    first = PlannedSessionIntentRow(planned_session_id=artefact, version=1)
    second = PlannedSessionIntentRow(planned_session_id=artefact, version=2)
    first.superseded_by = second.id = uuid.uuid7()
    chain = [first, second]

    # pyrefly: ignore[bad-specialization]
    assert current_version(chain) is second
    # pyrefly: ignore[bad-specialization]
    assert next_version(chain) == 3
    assert first.artefact_id == artefact


async def test_an_edit_before_any_match_versions_without_the_flag(
    client: AsyncClient,
) -> None:
    await append_ftp(client)
    session = await plan(client)

    updated = (
        await client.patch(
            f"{SESSIONS}/{session['id']}", json={"coach_notes": "eat first"}
        )
    ).json()

    assert updated["intent"]["version"] == 2
    assert updated["intent"]["edited_post_hoc"] is False
    assert updated["intent"]["recompute_reason"] == "intent edited"
    assert updated["intent"]["coach_notes"] == "eat first"
    assert updated["intent_versions"] == 2


async def test_the_old_version_stays_retrievable(client: AsyncClient) -> None:
    await append_ftp(client)
    session = await plan(client, intent_text="hold the band")
    await client.patch(f"{SESSIONS}/{session['id']}", json={"intent_text": "just ride"})

    history = (await client.get(f"{SESSIONS}/{session['id']}/intents")).json()["items"]

    assert [intent["version"] for intent in history] == [1, 2]
    assert history[0]["intent_text"] == "hold the band"
    assert history[1]["intent_text"] == "just ride"


async def test_the_chain_is_a_walkable_linked_list(client: AsyncClient) -> None:
    # A reader holding an old id has to be able to walk forward; a
    # `superseded_by` pointing nowhere would strand them.
    await append_ftp(client)
    session = await plan(client)
    await client.patch(f"{SESSIONS}/{session['id']}", json={"coach_notes": "one"})
    await client.patch(f"{SESSIONS}/{session['id']}", json={"coach_notes": "two"})

    history = (await client.get(f"{SESSIONS}/{session['id']}/intents")).json()["items"]

    assert history[0]["superseded_by"] == history[1]["id"]
    assert history[1]["superseded_by"] == history[2]["id"]
    assert history[2]["superseded_by"] is None


async def test_one_version_can_be_fetched_by_number(client: AsyncClient) -> None:
    await append_ftp(client)
    session = await plan(client, intent_text="hold the band")
    await client.patch(f"{SESSIONS}/{session['id']}", json={"intent_text": "just ride"})

    original = (await client.get(f"{SESSIONS}/{session['id']}/intents/1")).json()

    assert original["intent_text"] == "hold the band"
    assert original["version"] == 1


async def test_an_unknown_version_returns_404(client: AsyncClient) -> None:
    await append_ftp(client)
    session = await plan(client)

    assert (
        await client.get(f"{SESSIONS}/{session['id']}/intents/9")
    ).status_code == 404


async def test_a_pre_execution_edit_re_pins_the_anchors(
    client: AsyncClient,
) -> None:
    # "Frozen at creation or last pre-execution edit": a session re-planned
    # after a new FTP test uses the new FTP.
    first = await append_ftp(client, 240)
    session = await plan(client)
    assert session["intent"]["pinned_anchor_versions"] == {"ftp": first}
    second = await append_ftp(client, 265)

    updated = (
        await client.patch(f"{SESSIONS}/{session['id']}", json={"coach_notes": "hard"})
    ).json()

    assert updated["intent"]["pinned_anchor_versions"] == {"ftp": second}


async def test_changing_the_purpose_re_derives_the_criteria(
    client: AsyncClient,
) -> None:
    # The criteria came from the purpose's template, so leaving the old
    # purpose's rules behind after a purpose change would be a session judged
    # by something it is no longer for.
    await append_ftp(client)
    session = await plan(client, purpose="sweet_spot")

    updated = (
        await client.patch(f"{SESSIONS}/{session['id']}", json={"purpose": "threshold"})
    ).json()

    threshold = (await client.get("/api/v1/purposes/threshold")).json()
    assert updated["intent"]["success_criteria"] == threshold["default_criteria"]


# --- editing after a match ----------------------------------------------------


@pytest.mark.usefixtures("matched")
async def test_a_post_hoc_edit_is_flagged_and_keeps_its_pins(
    client: AsyncClient,
) -> None:
    original = await append_ftp(client, 240)
    session = await plan(client)
    await append_ftp(client, 265)

    updated = (
        await client.patch(
            f"{SESSIONS}/{session['id']}", json={"intent_text": "meant to be easy"}
        )
    ).json()

    assert updated["intent"]["version"] == 2
    assert updated["intent"]["edited_post_hoc"] is True
    assert "after the session was matched" in updated["intent"]["recompute_reason"]
    # The athlete executed against the original FTP; re-pinning would rewrite
    # the prescription the ride was judged by.
    assert updated["intent"]["pinned_anchor_versions"] == {"ftp": original}


@pytest.mark.usefixtures("matched")
async def test_a_post_hoc_edit_triggers_a_rescore(
    client: AsyncClient, rescores: list[tuple[uuid.UUID, int]]
) -> None:
    await append_ftp(client)
    session = await plan(client)

    await client.patch(f"{SESSIONS}/{session['id']}", json={"coach_notes": "later"})

    assert rescores == [(uuid.UUID(session["id"]), 2)]


async def test_a_pre_execution_edit_triggers_no_rescore(
    client: AsyncClient, rescores: list[tuple[uuid.UUID, int]]
) -> None:
    await append_ftp(client)
    session = await plan(client)

    await client.patch(f"{SESSIONS}/{session['id']}", json={"coach_notes": "earlier"})

    assert rescores == []


@pytest.mark.usefixtures("matched")
async def test_a_post_hoc_edit_drops_pins_it_no_longer_needs_and_adds_the_rest(
    client: AsyncClient,
) -> None:
    # "Keep the pins" is about the anchors the new version still refers to
    # (D54). One the edit removed is dropped — an intent may not carry a pin
    # its prescription has no use for — and one the edit *introduced* is
    # pinned at today's version, because there is no older answer to keep: it
    # was never part of what the athlete executed against.
    await append_anchor(client, "ftp", 250)
    lthr = await append_anchor(client, "lthr", 165)
    session = await plan(client, purpose="technique", structure=FTP_AND_LTHR_RIDE)
    assert set(session["intent"]["pinned_anchor_versions"]) == {"ftp", "lthr"}

    await append_anchor(client, "lthr", 168)
    max_hr = await append_anchor(client, "max_hr", 190)
    updated = (
        await client.patch(
            f"{SESSIONS}/{session['id']}", json={"structure": LTHR_AND_MAX_HR_RIDE}
        )
    ).json()

    assert updated["intent"]["edited_post_hoc"] is True
    assert updated["intent"]["pinned_anchor_versions"] == {
        # Still required, so the version the athlete rode against is kept —
        # not the one appended since.
        "lthr": lthr,
        # Newly required: nothing older to keep, so today's version.
        "max_hr": max_hr,
    }


@pytest.mark.usefixtures("matched")
async def test_the_original_version_survives_a_post_hoc_edit(
    client: AsyncClient,
) -> None:
    await append_ftp(client)
    session = await plan(client, intent_text="hold the band")

    await client.patch(
        f"{SESSIONS}/{session['id']}", json={"intent_text": "rewritten afterwards"}
    )

    original = (await client.get(f"{SESSIONS}/{session['id']}/intents/1")).json()
    assert original["intent_text"] == "hold the band"
    assert original["edited_post_hoc"] is False


# --- what is not an intent edit -----------------------------------------------


async def test_moving_a_session_does_not_version_its_intent(
    client: AsyncClient,
) -> None:
    # A date is a fact about the calendar, not about what the session is for.
    await append_ftp(client)
    session = await plan(client, date="2026-08-10")

    updated = (
        await client.patch(f"{SESSIONS}/{session['id']}", json={"date": "2026-08-12"})
    ).json()

    assert updated["date"] == "2026-08-12"
    assert updated["intent_versions"] == 1
    assert updated["intent"]["version"] == 1


async def test_changing_status_does_not_version_its_intent(
    client: AsyncClient,
) -> None:
    await append_ftp(client)
    session = await plan(client)

    updated = (
        await client.patch(f"{SESSIONS}/{session['id']}", json={"status": "missed"})
    ).json()

    assert updated["status"] == "missed"
    assert updated["intent_versions"] == 1


# --- what an edit may not do --------------------------------------------------
#
# A PATCH body distinguishes "absent" from "null", and the three fields below
# have no null to mean anything: a session without a purpose has no template to
# derive criteria from, and one without a date or a status has no row. Left
# unguarded, `{"purpose": null}` reached the template lookup as a 500 and
# `{"date": null}` reached a NOT NULL column as a 409 quoting driver SQL.


@pytest.mark.parametrize("field", ["purpose", "date", "status"])
async def test_a_field_a_session_cannot_be_without_may_not_be_cleared(
    client: AsyncClient, field: str
) -> None:
    await append_ftp(client)
    session = await plan(client)

    response = await client.patch(f"{SESSIONS}/{session['id']}", json={field: None})

    assert response.status_code == 422, response.text
    assert f"{field} cannot be cleared" in response.json()["detail"]
    # No half-applied edit: neither a new intent version nor a changed row.
    unchanged = (await client.get(f"{SESSIONS}/{session['id']}")).json()
    assert unchanged["intent_versions"] == 1
    assert unchanged["date"] == session["date"]
    assert unchanged["status"] == session["status"]
    assert unchanged["intent"]["purpose"] == session["intent"]["purpose"]


@pytest.mark.parametrize("field", ["intent_text", "coach_notes"])
async def test_the_free_text_fields_are_still_clearable(
    client: AsyncClient, field: str
) -> None:
    # Nullable by design: the athlete's own words are optional, and removing
    # them is an edit like any other.
    await append_ftp(client)
    session = await plan(client, **{field: "something"})

    updated = (
        await client.patch(f"{SESSIONS}/{session['id']}", json={field: None})
    ).json()

    assert updated["intent"][field] is None
    assert updated["intent"]["version"] == 2


async def test_an_empty_edit_is_refused_rather_than_audited(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # A 200 plus an audit row reading `changed: []` records that nothing
    # happened, which is worse than saying so.
    await append_ftp(client)
    session = await plan(client)

    response = await client.patch(f"{SESSIONS}/{session['id']}", json={})

    assert response.status_code == 422
    assert "at least one field" in response.json()["detail"]
    assert await audit_actions(db_session) == [
        "anchor.appended",
        "planned_session.created",
    ]


# --- listing, deleting, guards ------------------------------------------------


async def test_sessions_list_in_date_order_within_a_range(
    client: AsyncClient,
) -> None:
    await append_ftp(client)
    await plan(client, date="2026-08-12")
    await plan(client, date="2026-08-10")
    await plan(client, date="2026-08-20")

    page = (
        await client.get(SESSIONS, params={"start": "2026-08-10", "end": "2026-08-13"})
    ).json()

    assert [session["date"] for session in page["items"]] == [
        "2026-08-10",
        "2026-08-12",
    ]
    assert page["total"] == 2


async def test_sessions_can_be_filtered_by_status(client: AsyncClient) -> None:
    await append_ftp(client)
    session = await plan(client)
    await plan(client, date="2026-08-11")
    await client.patch(f"{SESSIONS}/{session['id']}", json={"status": "completed"})

    page = (await client.get(SESSIONS, params={"status": "completed"})).json()

    assert page["total"] == 1
    assert page["items"][0]["id"] == session["id"]


async def test_a_page_is_a_slice_of_the_whole_list(client: AsyncClient) -> None:
    await append_ftp(client)
    for day in range(1, 6):
        await plan(client, date=f"2026-09-0{day}")

    page = (await client.get(SESSIONS, params={"offset": 1, "limit": 2})).json()

    assert page["total"] == 5
    assert page["offset"] == 1
    assert page["limit"] == 2
    assert [session["date"] for session in page["items"]] == [
        "2026-09-02",
        "2026-09-03",
    ]


async def test_delete_removes_the_session_and_its_whole_chain(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    session = await plan(client)
    await client.patch(f"{SESSIONS}/{session['id']}", json={"coach_notes": "note"})

    response = await client.delete(f"{SESSIONS}/{session['id']}")

    assert response.status_code == 204
    assert (await client.get(f"{SESSIONS}/{session['id']}")).status_code == 404
    remaining = await db_session.execute(select(PlannedSessionIntentRow))
    assert list(remaining.scalars()) == []


async def test_the_intent_chain_cascades_in_the_database_not_the_orm(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Deleting through the service proves the chain goes; it does not prove
    # WHAT takes it, because the ORM's own cascade deletes the rows it has
    # loaded whatever the schema says. This statement goes around the ORM, so
    # only `ON DELETE CASCADE` can be left holding the chain — and the unit
    # suite runs with SQLite's foreign keys on (D51), so it is checked here as
    # well as on Postgres.
    await append_ftp(client)
    session = await plan(client)
    await client.patch(f"{SESSIONS}/{session['id']}", json={"coach_notes": "note"})

    await db_session.execute(
        delete(PlannedSessionRow).where(
            PlannedSessionRow.id == uuid.UUID(session["id"])
        )
    )
    await db_session.commit()

    remaining = await db_session.execute(select(PlannedSessionIntentRow))
    assert list(remaining.scalars()) == []


async def test_unknown_ids_return_404(client: AsyncClient) -> None:
    unknown = uuid.uuid4()

    assert (await client.get(f"{SESSIONS}/{unknown}")).status_code == 404
    assert (await client.patch(f"{SESSIONS}/{unknown}", json={})).status_code == 404
    assert (await client.delete(f"{SESSIONS}/{unknown}")).status_code == 404
    assert (await client.get(f"{SESSIONS}/{unknown}/intents")).status_code == 404


async def test_planned_sessions_need_a_session_cookie(
    anon_client: AsyncClient,
) -> None:
    assert (await anon_client.get(SESSIONS)).status_code == 401
    assert (await anon_client.post(SESSIONS, json={})).status_code == 401
    assert (await anon_client.delete(f"{SESSIONS}/{uuid.uuid4()}")).status_code == 401


# --- audit --------------------------------------------------------------------


async def test_every_mutation_is_audited(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    session = await plan(client)
    await client.patch(f"{SESSIONS}/{session['id']}", json={"coach_notes": "note"})
    await client.patch(f"{SESSIONS}/{session['id']}", json={"date": "2026-08-12"})
    await client.delete(f"{SESSIONS}/{session['id']}")

    assert await audit_actions(db_session) == [
        "anchor.appended",
        "planned_session.created",
        "planned_session.intent_revised",
        "planned_session.updated",
        "planned_session.deleted",
    ]


@pytest.mark.usefixtures("matched")
async def test_the_revision_audit_records_the_freeze_decision(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    pinned = await append_ftp(client)
    session = await plan(client)
    await client.patch(f"{SESSIONS}/{session['id']}", json={"coach_notes": "note"})

    result = await db_session.execute(
        select(AuditLogEntry).where(
            AuditLogEntry.action == "planned_session.intent_revised"
        )
    )
    payload = result.scalar_one().payload_json

    assert payload["from_version"] == 1
    assert payload["to_version"] == 2
    assert payload["edited_post_hoc"] is True
    assert payload["changed"] == ["coach_notes"]
    assert payload["pinned_anchor_versions"] == {"ftp": pinned}


async def test_a_combined_edit_records_both_facts(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Moving the session and rewriting its intent are two facts, and the
    # revision does not imply the move: auditing only the revision left the
    # date change with no trace at all.
    await append_ftp(client)
    session = await plan(client, date="2026-08-10")

    updated = (
        await client.patch(
            f"{SESSIONS}/{session['id']}",
            json={"date": "2026-08-12", "coach_notes": "moved and rewritten"},
        )
    ).json()

    assert updated["date"] == "2026-08-12"
    assert updated["intent"]["version"] == 2
    assert await audit_actions(db_session) == [
        "anchor.appended",
        "planned_session.created",
        "planned_session.intent_revised",
        "planned_session.updated",
    ]
    result = await db_session.execute(
        select(AuditLogEntry).where(AuditLogEntry.action == "planned_session.updated")
    )
    payload = result.scalar_one().payload_json
    assert payload["changed"] == ["date"]
    assert payload["date"] == "2026-08-12"
    # The version in force once the whole edit landed, not the one it started
    # from — the audit row describes the session as it now stands.
    assert payload["intent_version"] == 2


async def test_a_rejected_plan_leaves_no_audit_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post(
        SESSIONS,
        json={"date": "2026-08-10", "purpose": "sweet_spot", "structure": RIDE},
    )

    assert response.status_code == 422
    assert await audit_actions(db_session) == []

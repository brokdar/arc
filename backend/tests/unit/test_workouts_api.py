"""The workout library through HTTP: CRUD, search, folders, tags, audit.

Nothing here is versioned or frozen — that is the planned session's job. What
these tests pin is that the library round-trips a prescription unchanged, that
the derived summary is computed rather than stored, and that every mutation
leaves an audit row.
"""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.athlete import Discipline
from app.persistence.audit import AuditLogEntry
from app.persistence.workouts import WorkoutRow, WorkoutTagRow
from app.services.workouts import step_count_of

WORKOUTS = "/api/v1/workouts"
LABELS = "/api/v1/workout-labels"

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
        {"kind": "steady", "duration_s": 600, "role": "cooldown"},
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
                    "load": {"kind": "percent_e1rm", "value": 0.85},
                    "rir": 2,
                    "rest_s": 180,
                }
            ]
        },
        {
            "label": "B1/B2",
            "items": [
                {
                    "exercise_id": "romanian_deadlift",
                    "sets": 3,
                    "reps": 8,
                    "load": {"kind": "kg", "value": 80},
                },
                {
                    "exercise_id": "front_plank",
                    "sets": 3,
                    "reps": 1,
                    "load": {"kind": "bodyweight"},
                },
            ],
        },
    ],
}


async def create(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    """Add a workout to the library, asserting it was accepted."""
    payload: dict[str, Any] = {"name": "Sweet spot 3x8", "structure": RIDE} | overrides
    response = await client.post(WORKOUTS, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def audit_actions(session: AsyncSession) -> list[str]:
    """Every audit action so far, oldest first."""
    result = await session.execute(
        select(AuditLogEntry.action).order_by(AuditLogEntry.at, AuditLogEntry.id)
    )
    return list(result.scalars())


# --- create -------------------------------------------------------------------


async def test_a_prescription_round_trips_unchanged(client: AsyncClient) -> None:
    created = await create(client)

    fetched = (await client.get(f"{WORKOUTS}/{created['id']}")).json()

    assert fetched["structure"]["steps"][1]["times"] == 3
    assert fetched["structure"]["steps"][1]["children"][0]["targets"]["power"] == {
        "kind": "percent_of_anchor",
        "anchor_type": "ftp",
        "pct_low": 0.88,
        "pct_high": 0.93,
    }


async def test_the_discipline_is_derived_from_the_structure(
    client: AsyncClient,
) -> None:
    # Not a field the client sets: the prescription already says which it is,
    # and two sources of one truth is one too many.
    assert (await create(client))["discipline"] == "cycling"
    assert (await create(client, name="Squat day", structure=LIFT))[
        "discipline"
    ] == "strength"


async def test_the_summary_is_computed_from_the_structure(
    client: AsyncClient,
) -> None:
    ride = await create(client)
    lift = await create(client, name="Squat day", structure=LIFT)

    assert ride["summary"] == {
        "step_count": 8,
        "total_duration_s": 600 + 3 * 720 + 600,
        "total_sets": None,
    }
    assert lift["summary"] == {
        "step_count": 3,
        "total_duration_s": None,
        "total_sets": 11,
    }


async def test_tags_are_lower_cased_and_deduplicated(client: AsyncClient) -> None:
    created = await create(client, tags=["Bike", "bike", "SweetSpot"])

    assert created["tags"] == ["bike", "sweetspot"]


async def test_a_strength_prescription_must_reference_the_catalogue(
    client: AsyncClient,
) -> None:
    structure = {
        "discipline": "strength",
        "groups": [
            {
                "items": [
                    {
                        "exercise_id": "kettlebell_juggling",
                        "sets": 3,
                        "reps": 8,
                        "load": {"kind": "bodyweight"},
                    }
                ]
            }
        ],
    }

    response = await client.post(
        WORKOUTS, json={"name": "Circus", "structure": structure}
    )

    assert response.status_code == 422
    assert "unknown exercise(s): kettlebell_juggling" in response.json()["detail"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"steps": []}, "at least one step"),
        (
            {"steps": [{"kind": "steady", "duration_s": 60, "distance_m": 100}]},
            "exactly one of duration_s or distance_m",
        ),
        (
            {
                "steps": [
                    {
                        "kind": "steady",
                        "duration_s": 60,
                        "targets": {
                            "cadence": {
                                "kind": "percent_of_anchor",
                                "anchor_type": "ftp",
                                "pct_low": 0.8,
                                "pct_high": 0.9,
                            }
                        },
                    }
                ]
            },
            "cadence cannot be prescribed",
        ),
    ],
)
async def test_domain_rules_reach_the_client_as_422s(
    client: AsyncClient, mutation: dict[str, Any], message: str
) -> None:
    # The bounds live in `app.domain`, not in the schema, so the MCP tools
    # WP-8 adds obey exactly the same grammar as the web UI.
    response = await client.post(
        WORKOUTS,
        json={"name": "Broken", "structure": {"discipline": "cycling", **mutation}},
    )

    assert response.status_code == 422, response.text
    assert message in response.json()["detail"]


async def test_a_pathologically_nested_structure_is_a_422_not_a_crash(
    client: AsyncClient,
) -> None:
    # The nesting bound is checked while decoding, so a document deep enough
    # to exhaust the interpreter stack is refused like any other illegal
    # prescription rather than becoming a 500.
    document: dict[str, Any] = {"kind": "steady", "duration_s": 60}
    for _ in range(400):
        document = {"kind": "repeat", "times": 2, "children": [document]}

    response = await client.post(
        WORKOUTS,
        json={
            "name": "Turtles",
            "structure": {"discipline": "cycling", "steps": [document]},
        },
    )

    assert response.status_code == 422, response.text


async def test_an_unknown_structure_field_is_rejected_by_the_contract(
    client: AsyncClient,
) -> None:
    response = await client.post(
        WORKOUTS,
        json={
            "name": "Broken",
            "structure": {
                "discipline": "cycling",
                "steps": [{"kind": "steady", "duration_s": 60, "colour": "red"}],
            },
        },
    )

    assert response.status_code == 422


# --- read, search, filter -----------------------------------------------------


async def test_search_matches_name_and_description(client: AsyncClient) -> None:
    await create(client, name="Sweet spot 3x8")
    await create(client, name="Recovery spin", description="Easy sweet nothing")
    await create(client, name="Threshold 2x20")

    page = (await client.get(WORKOUTS, params={"q": "sweet"})).json()

    assert page["total"] == 2


async def test_search_is_case_insensitive(client: AsyncClient) -> None:
    await create(client, name="Sweet spot 3x8")

    assert (await client.get(WORKOUTS, params={"q": "SWEET"})).json()["total"] == 1


async def test_a_like_wildcard_is_a_literal_not_a_pattern(
    client: AsyncClient,
) -> None:
    await create(client, name="Sweet spot 3x8")

    assert (await client.get(WORKOUTS, params={"q": "%"})).json()["total"] == 0


async def test_filtering_by_folder_tag_and_discipline(client: AsyncClient) -> None:
    await create(client, name="A", folder="Base", tags=["bike"])
    await create(client, name="B", folder="Build", tags=["bike", "hard"])
    await create(client, name="C", structure=LIFT, tags=["gym"])

    assert (await client.get(WORKOUTS, params={"folder": "Base"})).json()["total"] == 1
    assert (await client.get(WORKOUTS, params={"tag": "bike"})).json()["total"] == 2
    assert (await client.get(WORKOUTS, params={"discipline": "strength"})).json()[
        "total"
    ] == 1


async def test_filters_combine(client: AsyncClient) -> None:
    await create(client, name="A", folder="Base", tags=["bike"])
    await create(client, name="B", folder="Build", tags=["bike"])

    page = (await client.get(WORKOUTS, params={"folder": "Base", "tag": "bike"})).json()

    assert page["total"] == 1
    assert page["items"][0]["name"] == "A"


async def test_folders_and_tags_in_use_are_listable(client: AsyncClient) -> None:
    # The workout creator needs both to offer what already exists rather than
    # inviting a fourth spelling of "base".
    await create(client, name="A", folder="Base", tags=["bike", "z2"])
    await create(client, name="B", folder="Build", tags=["bike"])
    await create(client, name="C")

    assert (await client.get(LABELS)).json() == {
        "folders": ["Base", "Build"],
        "tags": ["bike", "z2"],
    }


# Found by Schemathesis: the label lists used to live at `/workouts/folders`
# and `/workouts/tags`, which also match `/workouts/{workout_id}`. An
# undocumented method on them therefore fell through to the id route and
# answered 422 about uuid syntax, where 405 is the true answer — the same
# mismatch already fixed for the append-only refusals. They are a sibling
# collection now, so the collision does not exist to be papered over.


@pytest.mark.parametrize("method", ["patch", "delete", "put", "post"])
async def test_an_undocumented_method_on_the_label_route_is_a_405(
    client: AsyncClient, method: str
) -> None:
    response = await getattr(client, method)(LABELS)

    assert response.status_code == 405


async def test_the_label_route_is_outside_the_workout_id_namespace(
    client: AsyncClient,
) -> None:
    # `/workouts/labels` would be parsed as a workout id, which is exactly why
    # the facet lives one level up.
    assert (await client.get(f"{WORKOUTS}/labels")).status_code == 422


async def test_a_page_is_a_slice_of_the_whole_library(client: AsyncClient) -> None:
    for index in range(5):
        await create(client, name=f"Workout {index}")

    page = (await client.get(WORKOUTS, params={"offset": 1, "limit": 2})).json()

    assert page["total"] == 5
    assert page["offset"] == 1
    assert page["limit"] == 2
    # Newest first, so offset 1 skips the last one created.
    assert [workout["name"] for workout in page["items"]] == ["Workout 3", "Workout 2"]


async def test_get_unknown_id_returns_404(client: AsyncClient) -> None:
    assert (await client.get(f"{WORKOUTS}/{uuid.uuid4()}")).status_code == 404


# --- update -------------------------------------------------------------------


async def test_a_partial_update_leaves_the_rest_alone(client: AsyncClient) -> None:
    created = await create(client, folder="Base", tags=["bike"])

    updated = (
        await client.patch(f"{WORKOUTS}/{created['id']}", json={"name": "Renamed"})
    ).json()

    assert updated["name"] == "Renamed"
    assert updated["folder"] == "Base"
    assert updated["tags"] == ["bike"]
    assert updated["structure"] == created["structure"]


async def test_tags_can_be_replaced_and_cleared(client: AsyncClient) -> None:
    created = await create(client, tags=["bike", "z2"])

    replaced = (
        await client.patch(
            f"{WORKOUTS}/{created['id']}", json={"tags": ["bike", "hard"]}
        )
    ).json()
    cleared = (
        await client.patch(f"{WORKOUTS}/{created['id']}", json={"tags": []})
    ).json()

    assert replaced["tags"] == ["bike", "hard"]
    assert cleared["tags"] == []


async def test_a_folder_can_be_cleared_with_an_explicit_null(
    client: AsyncClient,
) -> None:
    created = await create(client, folder="Base")

    updated = (
        await client.patch(f"{WORKOUTS}/{created['id']}", json={"folder": None})
    ).json()

    assert updated["folder"] is None


async def test_changing_the_structure_changes_the_derived_fields(
    client: AsyncClient,
) -> None:
    created = await create(client)

    updated = (
        await client.patch(f"{WORKOUTS}/{created['id']}", json={"structure": LIFT})
    ).json()

    assert updated["discipline"] == "strength"
    assert updated["summary"]["total_sets"] == 11


async def test_a_name_cannot_be_cleared(client: AsyncClient) -> None:
    created = await create(client)

    response = await client.patch(f"{WORKOUTS}/{created['id']}", json={"name": None})

    assert response.status_code == 422
    assert "cannot be cleared" in response.json()["detail"]


async def test_an_unknown_field_is_rejected_rather_than_silently_dropped(
    client: AsyncClient,
) -> None:
    created = await create(client)

    response = await client.patch(f"{WORKOUTS}/{created['id']}", json={"colour": "red"})

    assert response.status_code == 422


# --- delete -------------------------------------------------------------------


async def test_delete_removes_the_workout_and_its_tags(client: AsyncClient) -> None:
    created = await create(client, tags=["bike"])

    response = await client.delete(f"{WORKOUTS}/{created['id']}")

    assert response.status_code == 204
    assert (await client.get(f"{WORKOUTS}/{created['id']}")).status_code == 404
    assert (await client.get(LABELS)).json()["tags"] == []


async def test_tags_cascade_in_the_database_not_the_orm(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # The test above proves the tags go; it cannot prove what takes them,
    # because the ORM deletes the rows it has loaded whatever the schema says.
    # This statement goes around the ORM, leaving `ON DELETE CASCADE` as the
    # only thing that can — and the unit suite runs with SQLite's foreign keys
    # on, so the clause is exercised on both dialects.
    created = await create(client, tags=["bike", "z2"])

    await db_session.execute(
        delete(WorkoutRow).where(WorkoutRow.id == uuid.UUID(created["id"]))
    )
    await db_session.commit()

    remaining = await db_session.execute(select(WorkoutTagRow))
    assert list(remaining.scalars()) == []


async def test_deleting_an_unknown_id_returns_404(client: AsyncClient) -> None:
    assert (await client.delete(f"{WORKOUTS}/{uuid.uuid4()}")).status_code == 404


# --- audit --------------------------------------------------------------------


async def test_every_mutation_is_audited(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    created = await create(client)
    await client.patch(f"{WORKOUTS}/{created['id']}", json={"name": "Renamed"})
    await client.delete(f"{WORKOUTS}/{created['id']}")

    assert await audit_actions(db_session) == [
        "workout.created",
        "workout.updated",
        "workout.deleted",
    ]


async def test_the_update_audit_records_the_before_and_after(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    created = await create(client, tags=["bike"])
    await client.patch(f"{WORKOUTS}/{created['id']}", json={"name": "Renamed"})

    result = await db_session.execute(
        select(AuditLogEntry).where(AuditLogEntry.action == "workout.updated")
    )
    payload = result.scalar_one().payload_json

    assert payload["before"]["name"] == "Sweet spot 3x8"
    assert payload["after"]["name"] == "Renamed"
    # The step tree itself is deliberately absent: a full structure per edit
    # makes the trail unreadable, and its job is to say *that* it changed.
    assert "structure" not in payload["after"]
    assert payload["after"]["step_count"] == 8


async def test_a_rejected_write_leaves_no_audit_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post(
        WORKOUTS,
        json={"name": "Broken", "structure": {"discipline": "cycling", "steps": []}},
    )

    assert response.status_code == 422
    assert await audit_actions(db_session) == []


async def test_workouts_need_a_session(anon_client: AsyncClient) -> None:
    assert (await anon_client.get(WORKOUTS)).status_code == 401
    assert (await anon_client.post(WORKOUTS, json={})).status_code == 401
    assert (await anon_client.delete(f"{WORKOUTS}/{uuid.uuid4()}")).status_code == 401


@pytest.mark.parametrize(
    "failure",
    [ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError],
)
def test_a_stale_stored_structure_costs_only_its_own_step_count(
    monkeypatch: pytest.MonkeyPatch, failure: type[Exception]
) -> None:
    # `step_count_of` is what every *list* projection uses — the athlete's
    # audit payload and the agent's library page. One document the model no
    # longer accepts must cost that row its count and nothing else, and the
    # kinds it can fail with are not only `ValueError`: this reads a JSON
    # column, not a value the decoder built.
    def explode(_row: WorkoutRow) -> Any:
        raise failure("this document went stale")

    monkeypatch.setattr("app.services.workouts.summarize", explode)
    row = WorkoutRow(
        name="Stale",
        description=None,
        discipline=Discipline.CYCLING,
        structure={},
        folder=None,
    )

    assert step_count_of(row) is None

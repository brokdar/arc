"""The exercise catalogue through HTTP, including how it gets seeded.

Seeding is lazy and idempotent — first access, not a migration and not the
lifespan — so the first read of the catalogue is also its only write. These
tests pin both halves: that a fresh database serves the bundled file, and
that reading it again writes nothing.
"""

import dataclasses

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.actor import Actor
from app.domain.muscles import REGION_GROUP, MuscleGroup, MuscleRegion, main_groups
from app.domain.strength import Exercise, Restriction
from app.persistence.audit import AuditLogEntry
from app.persistence.exercises import ExerciseNoteRow, ExerciseRow
from app.services import exercises as exercises_module
from app.services.exercises import ExerciseService
from app.services.templates import load_exercise_catalogue

EXERCISES = "/api/v1/exercises"


async def audit_actions(session: AsyncSession) -> list[str]:
    """Every audit action so far, oldest first."""
    result = await session.execute(
        select(AuditLogEntry.action).order_by(AuditLogEntry.id)
    )
    return list(result.scalars())


# --- the bundled file ---------------------------------------------------------


def test_the_bundled_catalogue_covers_the_movement_families() -> None:
    catalogue = load_exercise_catalogue()
    families = {exercise.category.value for exercise in catalogue}

    assert len(catalogue) >= 80
    assert {"squat", "hinge", "press", "pull", "core"} <= families


# --- reading ------------------------------------------------------------------


async def test_a_first_read_seeds_the_catalogue(client: AsyncClient) -> None:
    page = (await client.get(EXERCISES, params={"limit": 200})).json()

    assert page["total"] == len(load_exercise_catalogue())
    assert page["items"][0]["id"]


async def test_the_seed_is_credited_to_the_system_and_happens_once(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await client.get(EXERCISES)
    await client.get(EXERCISES)
    await client.get(EXERCISES)

    assert await audit_actions(db_session) == ["exercise_catalogue.seeded"]
    result = await db_session.execute(select(AuditLogEntry.actor))
    assert set(result.scalars()) == {str(Actor.system())}


async def test_reseeding_is_a_no_op(db_session: AsyncSession) -> None:
    service = ExerciseService.from_session(db_session)

    first = await service.ensure_seeded()
    second = await service.ensure_seeded()

    assert first == len(load_exercise_catalogue())
    assert second == 0


async def test_seeding_repairs_a_drifted_row(db_session: AsyncSession) -> None:
    # Idempotent means "matches the file", not "has some rows": an entry whose
    # name was changed in the file is brought back into line.
    service = ExerciseService.from_session(db_session)
    await service.ensure_seeded()
    row = await db_session.get(ExerciseRow, "back_squat")
    assert row is not None
    row.name = "Squat, Back, Barbell, Olympic"
    await db_session.flush()

    written = await service.ensure_seeded()

    assert written == 1
    assert (await service.get("back_squat")).name == "Back Squat"


async def test_seeding_inserts_a_slug_the_table_is_missing(
    db_session: AsyncSession,
) -> None:
    # The upgrade path for #26: the catalogue file gains entries after a
    # database has already been seeded. The seeder diffs by slug, not by
    # "is the table empty", so a missing movement is inserted on the next
    # access without touching the rest.
    service = ExerciseService.from_session(db_session)
    await service.ensure_seeded()
    row = await db_session.get(ExerciseRow, "reverse_fly")
    assert row is not None
    await db_session.delete(row)
    await db_session.flush()

    written = await service.ensure_seeded()

    assert written == 1
    assert (await service.get("reverse_fly")).name == "Dumbbell Reverse Fly"


async def test_the_home_gym_movements_are_served(client: AsyncClient) -> None:
    # The two additions of #26, reachable through the HTTP catalogue too.
    calf = (await client.get(f"{EXERCISES}/single_leg_calf_raise")).json()
    fly = (await client.get(f"{EXERCISES}/reverse_fly")).json()

    assert calf["unilateral"] is True
    assert calf["category"] == "squat"
    assert fly["category"] == "pull"


async def test_filtering_by_family(client: AsyncClient) -> None:
    page = (await client.get(EXERCISES, params={"category": "core"})).json()

    assert page["total"] > 0
    assert {item["category"] for item in page["items"]} == {"core"}


async def test_searching_by_name_is_case_insensitive(client: AsyncClient) -> None:
    page = (await client.get(EXERCISES, params={"q": "SQUAT"})).json()

    assert page["total"] > 0
    assert all("squat" in item["name"].lower() for item in page["items"])


async def test_a_like_wildcard_is_a_literal_not_a_pattern(
    client: AsyncClient,
) -> None:
    # Unescaped, `%` would match every exercise in the catalogue.
    page = (await client.get(EXERCISES, params={"q": "%"})).json()

    assert page["total"] == 0


async def test_get_returns_one_exercise(client: AsyncClient) -> None:
    exercise = (await client.get(f"{EXERCISES}/back_squat")).json()

    assert exercise["id"] == "back_squat"
    assert exercise["category"] == "squat"
    assert exercise["unilateral"] is False


async def test_a_unilateral_movement_says_so(client: AsyncClient) -> None:
    assert (await client.get(f"{EXERCISES}/pistol_squat")).json()["unilateral"] is True


async def test_an_unknown_slug_returns_404(client: AsyncClient) -> None:
    response = await client.get(f"{EXERCISES}/kettlebell_juggling")

    assert response.status_code == 404
    assert "catalogue" in response.json()["detail"]


# --- the catalogue is not writable through the API ----------------------------


async def test_the_catalogue_offers_no_write_endpoints(client: AsyncClient) -> None:
    # Adding a movement is a reviewed change to the bundled file, which is
    # what keeps every deployment's slugs identical — and therefore keeps a
    # stored prescription readable anywhere.
    assert (await client.post(EXERCISES, json={})).status_code == 405
    assert (await client.delete(f"{EXERCISES}/back_squat")).status_code == 405


async def test_exercises_need_a_session(anon_client: AsyncClient) -> None:
    assert (await anon_client.get(EXERCISES)).status_code == 401
    assert (await anon_client.get(f"{EXERCISES}/back_squat")).status_code == 401


# --- tags re-seed from the file (AC-34) ---------------------------------------


def catalogue_with_bench(**changes: object) -> tuple[Exercise, ...]:
    """The bundled catalogue with the bench press entry's tags changed."""
    return tuple(
        dataclasses.replace(entry, **changes)  # type: ignore[arg-type]
        if entry.id == "bench_press"
        else entry
        for entry in load_exercise_catalogue()
    )


async def audit_count(session: AsyncSession) -> int:
    """How many audit rows exist."""
    return await session.scalar(select(func.count()).select_from(AuditLogEntry)) or 0


async def test_changed_tags_in_the_file_reseed_the_row(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ExerciseService.from_session(db_session)
    await service.ensure_seeded()
    changed = catalogue_with_bench(
        muscles={MuscleRegion.PEC_STERNAL: 1.0, MuscleRegion.TRICEPS_LONG: 0.5},
        restrictions=frozenset(
            {
                Restriction.SUPINE_LOADED,
                Restriction.LONG_BREATH_HOLD,
                Restriction.OVERHEAD,
            }
        ),
    )
    monkeypatch.setattr(exercises_module, "load_exercise_catalogue", lambda: changed)

    written = await service.ensure_seeded()

    assert written == 1
    row = await db_session.get(ExerciseRow, "bench_press")
    assert row is not None
    await db_session.refresh(row)
    assert row.muscles == {"pec_sternal": 1.0, "triceps_long": 0.5}
    assert row.restrictions == ["long_breath_hold", "overhead", "supine_loaded"]


async def test_a_removed_restriction_tag_is_removed_from_the_row(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ExerciseService.from_session(db_session)
    await service.ensure_seeded()
    before = await db_session.get(ExerciseRow, "bench_press")
    assert before is not None
    assert "long_breath_hold" in before.restrictions
    changed = catalogue_with_bench(restrictions=frozenset({Restriction.SUPINE_LOADED}))
    monkeypatch.setattr(exercises_module, "load_exercise_catalogue", lambda: changed)

    assert await service.ensure_seeded() == 1

    row = await db_session.get(ExerciseRow, "bench_press")
    assert row is not None
    await db_session.refresh(row)
    assert row.restrictions == ["supine_loaded"]


async def test_an_unchanged_catalogue_writes_no_audit_row(
    db_session: AsyncSession,
) -> None:
    service = ExerciseService.from_session(db_session)
    await service.ensure_seeded()
    after_seed = await audit_count(db_session)

    assert await service.ensure_seeded() == 0

    assert await audit_count(db_session) == after_seed == 1


@pytest.mark.parametrize(
    ("column", "drifted"),
    [
        ("mechanic", "single_joint"),
        ("pattern", "vertical_push"),
        ("equipment", ["dumbbell"]),
        ("load_type", "dumbbell"),
    ],
)
async def test_every_tag_column_is_diffed_against_the_file(
    db_session: AsyncSession, column: str, drifted: object
) -> None:
    # A tag left out of the seeder's diff would never re-seed after a file
    # edit: the row would keep the old value forever.
    service = ExerciseService.from_session(db_session)
    await service.ensure_seeded()
    row = await db_session.get(ExerciseRow, "bench_press")
    assert row is not None
    setattr(row, column, drifted)
    await db_session.flush()

    assert await service.ensure_seeded() == 1


async def test_the_api_serves_the_tags(client: AsyncClient) -> None:
    bench = (await client.get(f"{EXERCISES}/bench_press")).json()

    assert bench["muscles"]["pec_sternal"] == 1.0
    assert bench["muscles"]["delt_anterior"] == 0.5
    assert bench["mechanic"] == "multi_joint"
    assert bench["pattern"] == "horizontal_push"
    assert bench["equipment"] == ["barbell", "bench", "rack"]
    assert bench["load_type"] == "barbell"
    assert bench["restrictions"] == ["long_breath_hold", "supine_loaded"]


# --- filters (AC-35) ----------------------------------------------------------


def expected_slugs(
    *, group: MuscleGroup | None = None, equipment: str | None = None
) -> set[str]:
    """What the bundled file says a filter should return, from the domain."""
    return {
        entry.id
        for entry in load_exercise_catalogue()
        if (group is None or group in main_groups(entry.muscles))
        and (equipment is None or equipment in entry.equipment)
    }


async def test_filtering_by_group_and_equipment(client: AsyncClient) -> None:
    page = (
        await client.get(
            EXERCISES, params={"group": "quads", "equipment": "dumbbell", "limit": 200}
        )
    ).json()

    slugs = {item["id"] for item in page["items"]}
    assert slugs == expected_slugs(group=MuscleGroup.QUADS, equipment="dumbbell")
    assert {"goblet_squat", "bulgarian_split_squat"} <= slugs
    assert page["total"] == len(slugs)
    for item in page["items"]:
        assert "dumbbell" in item["equipment"]
        mains = {
            REGION_GROUP[MuscleRegion(region)]
            for region, weight in item["muscles"].items()
            if weight == 1.0
        }
        assert MuscleGroup.QUADS in mains, item["id"]


async def test_a_helper_region_does_not_put_an_exercise_in_a_group(
    client: AsyncClient,
) -> None:
    # A conventional deadlift loads the vasti only as a helper, so it is not a
    # quad exercise; a back squat loads them as a main muscle, so it is.
    page = (await client.get(EXERCISES, params={"group": "quads", "limit": 200})).json()
    slugs = {item["id"] for item in page["items"]}

    assert "conventional_deadlift" not in slugs
    assert "back_squat" in slugs


async def test_an_unknown_group_is_422(client: AsyncClient) -> None:
    response = await client.get(EXERCISES, params={"group": "wings"})

    assert response.status_code == 422


async def test_filtering_by_pattern_and_mechanic(client: AsyncClient) -> None:
    page = (
        await client.get(
            EXERCISES,
            params={
                "pattern": "vertical_pull",
                "mechanic": "multi_joint",
                "limit": 200,
            },
        )
    ).json()

    assert {item["id"] for item in page["items"]} == {
        entry.id
        for entry in load_exercise_catalogue()
        if entry.pattern.value == "vertical_pull"
        and entry.mechanic.value == "multi_joint"
    }
    assert page["total"] > 0


async def test_filtered_pages_are_consistent(client: AsyncClient) -> None:
    # The JSON filters run before paging, so `total` counts matches, not rows,
    # and two pages together are the whole result.
    params = {"group": "quads", "equipment": "dumbbell"}
    first = (await client.get(EXERCISES, params={**params, "limit": 3})).json()
    rest = (
        await client.get(EXERCISES, params={**params, "limit": 200, "offset": 3})
    ).json()

    combined = [item["id"] for item in first["items"] + rest["items"]]
    assert len(combined) == len(set(combined)) == first["total"] == rest["total"]


async def test_an_unknown_equipment_or_pattern_is_422(client: AsyncClient) -> None:
    assert (
        await client.get(EXERCISES, params={"equipment": "anvil"})
    ).status_code == 422
    assert (
        await client.get(EXERCISES, params={"pattern": "juggle"})
    ).status_code == 422
    assert (await client.get(EXERCISES, params={"mechanic": "both"})).status_code == 422


# --- notes (AC-35) ------------------------------------------------------------


def note_url(slug: str) -> str:
    """The note sub-resource of one exercise."""
    return f"{EXERCISES}/{slug}/note"


async def test_a_note_is_stored_and_read_back(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    put = await client.put(note_url("bench_press"), json={"text": "Pins at hole 4."})

    assert put.status_code == 200
    assert put.json()["text"] == "Pins at hole 4."
    got = (await client.get(note_url("bench_press"))).json()
    assert got["exercise_id"] == "bench_press"
    assert got["text"] == "Pins at hole 4."
    assert got["updated_at"] is not None
    row = await db_session.get(ExerciseNoteRow, "bench_press")
    assert row is not None
    assert row.text == "Pins at hole 4."


async def test_one_note_per_exercise_a_second_put_replaces_it(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await client.put(note_url("bench_press"), json={"text": "first"})
    await client.put(note_url("bench_press"), json={"text": "second"})

    assert (await client.get(note_url("bench_press"))).json()["text"] == "second"
    count = await db_session.scalar(select(func.count()).select_from(ExerciseNoteRow))
    assert count == 1


async def test_an_exercise_without_a_note_reads_as_empty(client: AsyncClient) -> None:
    got = await client.get(note_url("back_squat"))

    assert got.status_code == 200
    assert got.json() == {"exercise_id": "back_squat", "text": "", "updated_at": None}


async def test_a_note_of_zero_characters_deletes_it(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await client.put(note_url("bench_press"), json={"text": "Pins at hole 4."})

    response = await client.put(note_url("bench_press"), json={"text": ""})

    assert response.status_code == 200
    assert response.json() == {
        "exercise_id": "bench_press",
        "text": "",
        "updated_at": None,
    }
    assert await db_session.get(ExerciseNoteRow, "bench_press") is None
    assert (await client.get(note_url("bench_press"))).json()["text"] == ""


async def test_a_note_over_500_characters_is_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.put(note_url("bench_press"), json={"text": "x" * 501})

    assert response.status_code == 422
    assert await db_session.get(ExerciseNoteRow, "bench_press") is None


async def test_a_note_of_exactly_500_characters_is_stored(client: AsyncClient) -> None:
    response = await client.put(note_url("bench_press"), json={"text": "x" * 500})

    assert response.status_code == 200
    assert len(response.json()["text"]) == 500


async def test_a_note_write_is_audited_and_credited_to_the_athlete(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await client.put(note_url("bench_press"), json={"text": "Pins at hole 4."})
    await client.put(note_url("bench_press"), json={"text": ""})

    actions = await audit_actions(db_session)
    assert actions[-2:] == ["exercise_note.set", "exercise_note.deleted"]


async def test_deleting_an_absent_note_writes_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await client.get(EXERCISES)
    before = await audit_count(db_session)

    await client.put(note_url("bench_press"), json={"text": ""})

    assert await audit_count(db_session) == before


async def test_a_note_on_an_unknown_exercise_is_404(client: AsyncClient) -> None:
    assert (await client.get(note_url("kettlebell_juggling"))).status_code == 404
    put = await client.put(note_url("kettlebell_juggling"), json={"text": "hi"})
    assert put.status_code == 404


async def test_notes_need_a_session(anon_client: AsyncClient) -> None:
    assert (await anon_client.get(note_url("bench_press"))).status_code == 401
    put = await anon_client.put(note_url("bench_press"), json={"text": "hi"})
    assert put.status_code == 401

"""The exercise catalogue through HTTP, including how it gets seeded.

Seeding is lazy and idempotent — first access, not a migration and not the
lifespan — so the first read of the catalogue is also its only write. These
tests pin both halves: that a fresh database serves the bundled file, and
that reading it again writes nothing.
"""

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.actor import Actor
from app.persistence.audit import AuditLogEntry
from app.persistence.exercises import ExerciseRow
from app.services.exercises import ExerciseService
from app.services.templates import load_exercise_catalogue

EXERCISES = "/api/v1/exercises"


async def audit_actions(session: AsyncSession) -> list[str]:
    """Every audit action so far, oldest first."""
    result = await session.execute(
        select(AuditLogEntry.action).order_by(AuditLogEntry.at, AuditLogEntry.id)
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

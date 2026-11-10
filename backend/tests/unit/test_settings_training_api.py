"""Training settings through HTTP: defaults, the first-run answer, the audit (AC-15)."""

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.athlete_settings import AthleteSettingsRow
from app.persistence.audit import AuditLogEntry

SETTINGS = "/api/v1/settings/training"

#: What a database nobody has touched answers.
FRESH = {
    "mode": "simple",
    "plain_logbook": False,
    "ask_frequency": "always",
    "units": "kg",
    "locale": "en",
    "high_contrast": False,
    "before_you_start_seen": False,
    "first_run_seen": False,
    "lifted_before": None,
}


async def audit_rows(db_session: AsyncSession) -> list[AuditLogEntry]:
    result = await db_session.execute(
        select(AuditLogEntry).where(AuditLogEntry.entity_type == "athlete_settings")
    )
    return list(result.scalars())


async def settings_rows(db_session: AsyncSession) -> list[AthleteSettingsRow]:
    result = await db_session.execute(select(AthleteSettingsRow))
    return list(result.scalars())


async def test_a_fresh_database_returns_the_defaults(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.get(SETTINGS)

    assert response.status_code == 200
    assert response.json() == FRESH
    # A read is a read: the defaults are answered, not written.
    assert await settings_rows(db_session) == []
    assert await audit_rows(db_session) == []


async def test_answering_regularly_sets_standard_and_writes_one_audit_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.patch(SETTINGS, json={"lifted_before": "regularly"})

    assert response.status_code == 200
    assert response.json() == {
        **FRESH,
        "mode": "standard",
        "lifted_before": "regularly",
        "first_run_seen": True,
    }
    assert (await client.get(SETTINGS)).json() == response.json()

    [stored] = await settings_rows(db_session)
    assert stored.mode == "standard"
    assert stored.lifted_before == "regularly"

    [entry] = await audit_rows(db_session)
    assert entry.action == "athlete_settings.updated"
    assert entry.actor == "athlete"
    assert entry.entity_id == stored.id
    assert entry.payload_json == {
        "changed": {
            "lifted_before": {"from": None, "to": "regularly"},
            "mode": {"from": "simple", "to": "standard"},
            "first_run_seen": {"from": False, "to": True},
        }
    }


async def test_answering_never_keeps_simple(client: AsyncClient) -> None:
    response = await client.patch(SETTINGS, json={"lifted_before": "never"})

    assert response.json()["mode"] == "simple"
    assert response.json()["lifted_before"] == "never"


async def test_skipping_leaves_no_answer_and_simple(client: AsyncClient) -> None:
    response = await client.patch(SETTINGS, json={"first_run_seen": True})

    assert response.json() == {**FRESH, "first_run_seen": True}


async def test_a_second_patch_updates_the_same_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await client.patch(SETTINGS, json={"units": "lb"})
    response = await client.patch(SETTINGS, json={"locale": "de"})

    assert response.json() == {**FRESH, "units": "lb", "locale": "de"}
    assert len(await settings_rows(db_session)) == 1
    assert len(await audit_rows(db_session)) == 2


async def test_full_control_is_a_choice_the_api_accepts(client: AsyncClient) -> None:
    response = await client.patch(
        SETTINGS, json={"mode": "full", "high_contrast": True, "ask_frequency": "never"}
    )

    assert response.json() == {
        **FRESH,
        "mode": "full",
        "high_contrast": True,
        "ask_frequency": "never",
    }


async def test_an_unknown_mode_is_422_naming_the_field(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.patch(SETTINGS, json={"mode": "expert"})

    assert response.status_code == 422
    errors = response.json()["detail"]
    # One entry per union branch the value failed; every one names `mode`.
    assert errors
    assert all(error["loc"][:2] == ["body", "mode"] for error in errors)
    assert any("simple" in error["msg"] for error in errors)
    assert await settings_rows(db_session) == []
    assert await audit_rows(db_session) == []


async def test_an_explicit_null_is_422_naming_the_field(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.patch(SETTINGS, json={"units": None})

    assert response.status_code == 422
    assert "units" in response.json()["detail"]
    assert await settings_rows(db_session) == []


async def test_an_unknown_field_is_422(client: AsyncClient) -> None:
    response = await client.patch(SETTINGS, json={"timezone": "Europe/Berlin"})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "timezone"]


async def test_an_empty_patch_changes_nothing_and_writes_no_audit_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await client.patch(SETTINGS, json={"units": "lb"})
    before = await audit_rows(db_session)

    response = await client.patch(SETTINGS, json={})

    assert response.status_code == 200
    assert response.json() == {**FRESH, "units": "lb"}
    assert len(await audit_rows(db_session)) == len(before) == 1


async def test_an_empty_patch_on_a_fresh_database_writes_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.patch(SETTINGS, json={})

    assert response.status_code == 200
    assert response.json() == FRESH
    assert await settings_rows(db_session) == []
    assert await audit_rows(db_session) == []


async def test_a_patch_that_repeats_the_current_values_writes_no_audit_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.patch(SETTINGS, json={"units": "kg", "mode": "simple"})

    assert response.json() == FRESH
    assert await audit_rows(db_session) == []


async def test_anonymous_callers_get_401(anon_client: AsyncClient) -> None:
    assert (await anon_client.get(SETTINGS)).status_code == 401
    assert (await anon_client.patch(SETTINGS, json={"units": "lb"})).status_code == 401

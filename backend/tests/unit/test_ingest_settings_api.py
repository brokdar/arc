"""AC-19: the local drop's sweep interval, set in the app and applied at once.

Asserted on the **running scheduler's job**, not on the row that was written.
The row is the easy half: a stored interval nothing re-times is a number in a
table that the sweep never reads, and the athlete would set it, see it echoed
back, and go on getting a sweep every thirty seconds until the next deploy.
So every test here that claims "it took effect" reaches for
``app.state.scheduler.get_job(INBOX_JOB_ID)`` and reads the trigger.

That is also why these tests boot the application through `LifespanManager`
rather than using the module-wide `client` fixture: the `app` fixture builds
the app *without* a lifespan, so it has no scheduler at all, and a test that
asserted against a scheduler it created itself would prove nothing about the
one the process actually runs.
"""

import tomllib
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import (
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_SCAN_INTERVAL_SECONDS,
    get_settings,
)
from app.ingest.inbox import INBOX_JOB_ID, run_inbox_job
from app.persistence.audit import AuditLogEntry
from app.persistence.ingest_settings import IngestSettingsRow
from tests.unit.conftest import TEST_PASSWORD

SETTINGS_URL = "/api/v1/integrations/local-drop/settings"
INTEGRATIONS = "/api/v1/integrations"

#: The operation the fuzzer has to be told about, spelled as Schemathesis
#: names it.
FUZZ_OPERATION = "PUT /api/v1/integrations/local-drop/settings"


@pytest.fixture
def env_interval(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[[int], None]]:
    """Set `INGEST__SCAN_INTERVAL_SECONDS` for one test, before the app boots.

    Depends on `data_root` so it runs after that fixture's cache clear, and
    clears again on the way out — `get_settings` is `lru_cache`d.
    """

    def put(seconds: int) -> None:
        monkeypatch.setenv("INGEST__SCAN_INTERVAL_SECONDS", str(seconds))
        get_settings.cache_clear()

    yield put
    get_settings.cache_clear()


@pytest.fixture
async def running_app(data_root: Path, app: FastAPI) -> AsyncIterator[FastAPI]:
    """The application with its real lifespan — scheduler started, jobs on it."""
    async with LifespanManager(app):
        yield app


@pytest.fixture
async def live_client(running_app: FastAPI) -> AsyncIterator[AsyncClient]:
    """An authenticated client against the app whose scheduler is running."""
    transport = ASGITransport(app=running_app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        login = await http.post("/api/v1/auth/login", json={"password": TEST_PASSWORD})
        assert login.status_code == 204, login.text
        yield http


def sweep_interval_of(app: FastAPI) -> float:
    """What the running scheduler thinks the inbox sweep's interval is."""
    job = app.state.scheduler.get_job(INBOX_JOB_ID)
    assert job is not None, "the inbox sweep is not registered"
    return float(job.trigger.interval.total_seconds())


# --- AC-19: the interval takes effect on the running sweep -------------------


async def test_setting_the_interval_retimes_the_running_sweep(
    running_app: FastAPI, live_client: AsyncClient, db_session: AsyncSession
) -> None:
    scheduler = running_app.state.scheduler
    assert sweep_interval_of(running_app) == 30

    response = await live_client.put(SETTINGS_URL, json={"scan_interval_seconds": 120})

    assert response.status_code == 200, response.text
    assert response.json()["scan_interval_seconds"] == 120
    assert response.json()["source"] == "stored"
    # The claim, on the artifact the criterion names: the job the scheduler is
    # holding right now, in the same process, with nothing restarted.
    assert sweep_interval_of(running_app) == 120
    assert running_app.state.scheduler is scheduler
    assert scheduler.running is True
    assert (
        await db_session.scalar(select(func.count()).select_from(IngestSettingsRow))
        == 1
    )


async def test_the_stored_interval_is_what_every_read_reports(
    live_client: AsyncClient,
) -> None:
    await live_client.put(SETTINGS_URL, json={"scan_interval_seconds": 120})

    facet = await live_client.get(SETTINGS_URL)
    assert facet.status_code == 200, facet.text
    assert facet.json()["scan_interval_seconds"] == 120
    assert facet.json()["source"] == "stored"

    # And the list the panel renders, which would otherwise keep quoting the
    # environment at an athlete who just changed it.
    listed = await live_client.get(INTEGRATIONS)
    local = next(
        item for item in listed.json()["items"] if item["kind"] == "local_drop"
    )
    assert local["local"]["scan_interval_seconds"] == 120


async def test_setting_it_twice_leaves_one_row_and_the_later_interval(
    running_app: FastAPI, live_client: AsyncClient, db_session: AsyncSession
) -> None:
    await live_client.put(SETTINGS_URL, json={"scan_interval_seconds": 120})
    await live_client.put(SETTINGS_URL, json={"scan_interval_seconds": 300})

    assert sweep_interval_of(running_app) == 300
    assert (
        await db_session.scalar(select(func.count()).select_from(IngestSettingsRow))
        == 1
    )


async def test_the_change_is_audited(
    live_client: AsyncClient, db_session: AsyncSession
) -> None:
    await live_client.put(SETTINGS_URL, json={"scan_interval_seconds": 120})

    rows = (
        (
            await db_session.execute(
                select(AuditLogEntry).where(
                    AuditLogEntry.action == "ingest_settings.updated"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].actor == "athlete"
    assert rows[0].payload_json["scan_interval_seconds"] == 120


# --- AC-19 edge: the facet is outside the id namespace -----------------------


async def test_posting_to_the_settings_facet_is_405(live_client: AsyncClient) -> None:
    """Not a 422 about `{integration_id}` syntax.

    The facet is a literal two-segment path and nothing declares
    `/{integration_id}/settings`, so no other method falls through to the id
    route (`.claude/rules/api-collection-facets.md`) and Starlette's own 405
    is correct for free.
    """
    response = await live_client.post(SETTINGS_URL, json={"scan_interval_seconds": 120})

    assert response.status_code == 405, response.text


async def test_the_facet_needs_a_session(anon_client: AsyncClient) -> None:
    assert (await anon_client.get(SETTINGS_URL)).status_code == 401
    assert (
        await anon_client.put(SETTINGS_URL, json={"scan_interval_seconds": 120})
    ).status_code == 401


# --- AC-19 edge: the refusals ------------------------------------------------


@pytest.mark.parametrize("seconds", [0, -1, -120])
async def test_zero_and_negative_intervals_are_refused(
    running_app: FastAPI, live_client: AsyncClient, seconds: int
) -> None:
    response = await live_client.put(
        SETTINGS_URL, json={"scan_interval_seconds": seconds}
    )

    assert response.status_code == 422, response.text
    # And nothing was applied to the sweep on the way to refusing it.
    assert sweep_interval_of(running_app) == 30


async def test_the_documented_minimum_is_accepted(
    running_app: FastAPI, live_client: AsyncClient
) -> None:
    response = await live_client.put(
        SETTINGS_URL, json={"scan_interval_seconds": MIN_SCAN_INTERVAL_SECONDS}
    )

    assert response.status_code == 200, response.text
    assert sweep_interval_of(running_app) == MIN_SCAN_INTERVAL_SECONDS


async def test_one_below_the_documented_minimum_is_refused(
    running_app: FastAPI, live_client: AsyncClient
) -> None:
    response = await live_client.put(
        SETTINGS_URL, json={"scan_interval_seconds": MIN_SCAN_INTERVAL_SECONDS - 1}
    )

    assert response.status_code == 422, response.text
    assert str(MIN_SCAN_INTERVAL_SECONDS) in response.text
    assert sweep_interval_of(running_app) == 30


async def test_the_documented_maximum_is_accepted(
    running_app: FastAPI, live_client: AsyncClient
) -> None:
    response = await live_client.put(
        SETTINGS_URL, json={"scan_interval_seconds": MAX_SCAN_INTERVAL_SECONDS}
    )

    assert response.status_code == 200, response.text
    assert sweep_interval_of(running_app) == MAX_SCAN_INTERVAL_SECONDS


async def test_above_the_documented_maximum_is_refused(
    running_app: FastAPI, live_client: AsyncClient
) -> None:
    response = await live_client.put(
        SETTINGS_URL, json={"scan_interval_seconds": MAX_SCAN_INTERVAL_SECONDS + 1}
    )

    assert response.status_code == 422, response.text
    assert str(MAX_SCAN_INTERVAL_SECONDS) in response.text
    assert sweep_interval_of(running_app) == 30


async def test_the_bounds_are_reported_so_the_form_can_state_them(
    live_client: AsyncClient,
) -> None:
    """The panel does not hardcode them: it renders what the server allows."""
    body = (await live_client.get(SETTINGS_URL)).json()

    assert body["minimum_seconds"] == MIN_SCAN_INTERVAL_SECONDS
    assert body["maximum_seconds"] == MAX_SCAN_INTERVAL_SECONDS


# --- AC-19 edge: nothing stored means the environment ------------------------


async def test_with_nothing_stored_the_read_is_the_environment_value(
    env_interval: Callable[[int], None],
    data_root: Path,
    live_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    assert (
        await db_session.scalar(select(func.count()).select_from(IngestSettingsRow))
        == 0
    )
    # A value the default (30) cannot be mistaken for: the read consults
    # `get_settings()` per request, so setting the environment after boot is
    # exactly what this read must reflect.
    env_interval(45)

    body = (await live_client.get(SETTINGS_URL)).json()

    assert body["scan_interval_seconds"] == 45
    assert body["source"] == "environment"
    assert body["inbox_path"] == str((data_root / "inbox").resolve())
    assert Path(body["inbox_path"]).is_absolute()


async def test_the_environment_seeds_the_value_the_sweep_boots_on(
    env_interval: Callable[[int], None],
    data_root: Path,
    app: FastAPI,
) -> None:
    """`INGEST__SCAN_INTERVAL_SECONDS` still decides the interval at boot."""
    env_interval(45)

    async with LifespanManager(app):
        assert sweep_interval_of(app) == 45


async def test_a_stored_interval_survives_a_restart(
    data_root: Path, app: FastAPI
) -> None:
    """The point of storing it: the next boot sweeps on the athlete's number.

    Reconciled by the sweep itself rather than at startup — `lifespan` reads no
    database on purpose — so this asserts the job function's own effect.
    """
    from app.domain.actor import Actor
    from app.ingest.inbox import apply_stored_scan_interval
    from app.persistence.db import session_scope
    from app.services.ingest_settings import IngestSettingsService

    async with session_scope() as session:
        await IngestSettingsService.from_session(session).set_scan_interval(
            600, actor=Actor.athlete()
        )

    async with LifespanManager(app):
        assert sweep_interval_of(app) == 30  # the environment's, at boot

        await apply_stored_scan_interval()

        assert sweep_interval_of(app) == 600


async def test_a_reconcile_that_fails_does_not_cost_the_sweep(
    data_root: Path,
    session_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The interval is not worth a missed sweep.

    The reconcile reads the database and the sweep does not need it to have
    worked; folded into one `try`, a hiccup reading one small row would skip
    the sweep — the failure this job exists to avoid, wearing a settings
    problem as a disguise.
    """
    swept = False

    async def explode() -> None:
        raise RuntimeError("the database is gone")

    async def sweep() -> list[Any]:
        nonlocal swept
        swept = True
        return []

    monkeypatch.setattr("app.ingest.inbox.apply_stored_scan_interval", explode)
    monkeypatch.setattr("app.ingest.inbox.scan_inbox", sweep)

    await run_inbox_job()

    assert swept is True


# --- AC-19 edge: the fuzzer is told about the refusals -----------------------


def test_schemathesis_is_told_this_operation_refuses_valid_bodies() -> None:
    """The bounds are a service rule, not a JSON-schema one.

    `scan_interval_seconds` is a plain integer in the contract, so a fuzz run
    sends `0` and `2**40` and gets the service's sentence back. Without an
    entry here `positive_data_acceptance` calls that a lie
    (`backend/schemathesis.toml`, and the repo convention: narrow the check per
    operation, never disable it globally).
    """
    config = tomllib.loads(
        (Path(__file__).resolve().parents[2] / "schemathesis.toml").read_text()
    )
    operations: list[dict[str, Any]] = config["operations"]

    entry = next(
        (row for row in operations if row.get("include-name") == FUZZ_OPERATION), None
    )
    assert entry is not None, f"no schemathesis entry for {FUZZ_OPERATION}"
    assert 422 in entry["checks"]["positive_data_acceptance"]["expected-statuses"]

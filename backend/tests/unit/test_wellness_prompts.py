"""The daily wellness prompt: raised once a day, and expiring into silence.

Against the service and the stored row rather than through HTTP, because what
these tests are about is the *row* — one per date, and what a sweep run twice
over it does. The HTTP half (`GET`/`POST /wellness/prompt`) lives in
`test_wellness_api.py`, which is where the athlete's browser talks.

The clock is frozen by passing `now` explicitly everywhere, the way
`ScoringService.expire_prompts`' tests do: a sweep whose behaviour depends on
when the suite happens to run is a sweep nobody can assert about.
"""

import datetime as dt
from collections.abc import Sequence
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.domain.actor import Actor
from app.domain.wellness import WellnessPromptStatus, WellnessSource
from app.persistence.wellness_prompt import WellnessPromptRow
from app.services.wellness import (
    DayInput,
    WellnessService,
    run_wellness_prompt_sweep,
)

TODAY = dt.date.today()
YESTERDAY = TODAY - dt.timedelta(days=1)

#: A fixed moment to raise prompts at, so every deadline in this module is
#: arithmetic rather than a race with the wall clock.
NOON = dt.datetime.combine(TODAY, dt.time(12, 0), tzinfo=dt.UTC)


def service(session: AsyncSession) -> WellnessService:
    """The service under test, wired to the test session."""
    return WellnessService.from_session(session)


async def stored(session: AsyncSession) -> Sequence[WellnessPromptRow]:
    """Every prompt row, oldest date first, read fresh from the database."""
    session.expire_all()
    result = await session.execute(
        select(WellnessPromptRow).order_by(WellnessPromptRow.local_date.asc())
    )
    return list(result.scalars())


def snapshot(rows: Sequence[WellnessPromptRow]) -> list[tuple[Any, ...]]:
    """What a second sweep must not change: the row, its status, its stamp."""
    return [(row.local_date, row.status, row.resolved_at) for row in rows]


# --- AC-7: one row per date, and the constraint is what makes it true ---------------


async def test_raising_twice_yields_one_row(db_session: AsyncSession) -> None:
    """The decision: one prompt per date is a **unique constraint**.

    Not scheduler discipline. The sweep runs hourly and is expected to fire
    over the same date many times a day; the row that stops a second prompt
    from existing is the one the database refuses to duplicate.
    """
    first = await service(db_session).raise_prompt(
        TODAY, actor=Actor.system(), now=NOON
    )
    second = await service(db_session).raise_prompt(
        TODAY, actor=Actor.system(), now=NOON + dt.timedelta(hours=1)
    )

    rows = await stored(db_session)
    assert [row.local_date for row in rows] == [TODAY]
    assert first.id == second.id
    # The second raise does not move the deadline either: the day was asked at
    # noon, and the athlete's window runs from then.
    assert rows[0].expires_at == first.expires_at


async def test_raising_over_an_answered_prompt_leaves_it_answered(
    db_session: AsyncSession,
) -> None:
    await service(db_session).raise_prompt(TODAY, actor=Actor.system(), now=NOON)
    await service(db_session).record(
        TODAY,
        {"fatigue": 3},
        actor=Actor.athlete(),
        source=WellnessSource.ATHLETE,
    )

    await service(db_session).raise_prompt(
        TODAY, actor=Actor.system(), now=NOON + dt.timedelta(hours=2)
    )

    [row] = await stored(db_session)
    assert row.status is WellnessPromptStatus.ANSWERED
    assert row.resolved_at is not None


async def test_raising_over_an_expired_prompt_does_not_resurrect_it(
    db_session: AsyncSession,
) -> None:
    """No resurrection: a day that closed unanswered stays closed."""
    raised = await service(db_session).raise_prompt(
        YESTERDAY, actor=Actor.system(), now=NOON
    )
    await service(db_session).expire_prompts(
        actor=Actor.system(), now=raised.expires_at
    )
    [expired] = await stored(db_session)
    closed_at = expired.resolved_at

    await service(db_session).raise_prompt(
        YESTERDAY, actor=Actor.system(), now=raised.expires_at + dt.timedelta(hours=1)
    )

    [row] = await stored(db_session)
    assert row.status is WellnessPromptStatus.EXPIRED
    assert row.resolved_at == closed_at


async def test_two_raises_inside_one_transaction_yield_one_row(
    db_session: AsyncSession,
) -> None:
    """Both raises run before any commit — the second still finds the first."""
    one = service(db_session)
    await one.raise_prompt(TODAY, actor=Actor.system(), now=NOON)
    await one.raise_prompt(TODAY, actor=Actor.system(), now=NOON)
    await db_session.commit()

    assert [row.local_date for row in await stored(db_session)] == [TODAY]


# --- AC-8: expiry closes the day into "not provided", once ------------------------


async def test_an_unanswered_prompt_past_its_deadline_expires(
    db_session: AsyncSession,
) -> None:
    hours = get_settings().wellness.prompt_expiry_hours
    await service(db_session).raise_prompt(YESTERDAY, actor=Actor.system(), now=NOON)

    expired = await service(db_session).expire_prompts(
        actor=Actor.system(), now=NOON + dt.timedelta(hours=hours + 1)
    )

    assert [row.local_date for row in expired] == [YESTERDAY]
    [row] = await stored(db_session)
    assert row.status is WellnessPromptStatus.EXPIRED
    assert row.resolved_at == NOON + dt.timedelta(hours=hours + 1)


async def test_no_second_prompt(db_session: AsyncSession) -> None:
    """Expiry closes the day; it never raises a follow-up.

    The decision, and the reason it is asserted rather than assumed: a reminder
    cascade is what an application built for the compliant athlete does, and the
    athlete this exists for is the one who did not answer because the week is
    already going badly.
    """
    hours = get_settings().wellness.prompt_expiry_hours
    await service(db_session).raise_prompt(YESTERDAY, actor=Actor.system(), now=NOON)

    await service(db_session).expire_prompts(
        actor=Actor.system(), now=NOON + dt.timedelta(hours=hours + 1)
    )

    rows = await stored(db_session)
    assert [row.local_date for row in rows] == [YESTERDAY]
    assert [row.status for row in rows] == [WellnessPromptStatus.EXPIRED]


async def test_running_the_sweep_twice_changes_nothing(
    db_session: AsyncSession,
) -> None:
    hours = get_settings().wellness.prompt_expiry_hours
    await service(db_session).raise_prompt(YESTERDAY, actor=Actor.system(), now=NOON)
    later = NOON + dt.timedelta(hours=hours + 1)
    await service(db_session).expire_prompts(actor=Actor.system(), now=later)
    before = snapshot(await stored(db_session))

    again = await service(db_session).expire_prompts(
        actor=Actor.system(), now=later + dt.timedelta(hours=1)
    )

    assert again == []
    assert snapshot(await stored(db_session)) == before


async def test_a_prompt_exactly_at_its_deadline_expires(
    db_session: AsyncSession,
) -> None:
    """The half-open rule decides, and it decides *expired*.

    A prompt is answerable over ``[raised_at, expires_at)``, like every other
    range in this codebase, so the instant named by the deadline is already
    outside the window.
    """
    hours = get_settings().wellness.prompt_expiry_hours
    raised = await service(db_session).raise_prompt(
        YESTERDAY, actor=Actor.system(), now=NOON
    )
    assert raised.expires_at == NOON + dt.timedelta(hours=hours)

    expired = await service(db_session).expire_prompts(
        actor=Actor.system(), now=raised.expires_at
    )

    assert [row.local_date for row in expired] == [YESTERDAY]
    # And one second earlier it is still the athlete's to answer.
    assert (await stored(db_session))[0].status is WellnessPromptStatus.EXPIRED


async def test_a_prompt_one_second_short_of_its_deadline_is_left_alone(
    db_session: AsyncSession,
) -> None:
    raised = await service(db_session).raise_prompt(
        YESTERDAY, actor=Actor.system(), now=NOON
    )

    expired = await service(db_session).expire_prompts(
        actor=Actor.system(), now=raised.expires_at - dt.timedelta(seconds=1)
    )

    assert expired == []
    assert (await stored(db_session))[0].status is WellnessPromptStatus.PENDING


async def test_an_answered_prompt_past_the_deadline_is_untouched(
    db_session: AsyncSession,
) -> None:
    hours = get_settings().wellness.prompt_expiry_hours
    await service(db_session).raise_prompt(YESTERDAY, actor=Actor.system(), now=NOON)
    await service(db_session).record(
        YESTERDAY,
        {"fatigue": 2},
        actor=Actor.athlete(),
        source=WellnessSource.ATHLETE,
    )
    before = snapshot(await stored(db_session))

    expired = await service(db_session).expire_prompts(
        actor=Actor.system(), now=NOON + dt.timedelta(hours=hours + 5)
    )

    assert expired == []
    assert snapshot(await stored(db_session)) == before
    assert before[0][1] is WellnessPromptStatus.ANSWERED


async def test_the_sweep_logs_and_returns_when_the_database_fails(
    session_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scheduler job that raises stops running. This one may never raise."""

    async def boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("the database went away mid-sweep")

    monkeypatch.setattr(WellnessService, "expire_prompts", boom)

    await run_wellness_prompt_sweep()


async def test_the_scheduled_sweep_raises_the_day_and_closes_the_overdue(
    session_factory: Any, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one job does both halves — raise today, expire what has run out."""
    monkeypatch.setenv("WELLNESS__PROMPT_HOUR_LOCAL", "0")
    get_settings.cache_clear()
    # Raised long enough ago that its window has run out by the time the job
    # reads the wall clock, which is the one thing the job does read.
    hours = get_settings().wellness.prompt_expiry_hours
    await service(db_session).raise_prompt(
        YESTERDAY,
        actor=Actor.system(),
        now=dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours + 1),
    )
    await db_session.commit()

    await run_wellness_prompt_sweep()

    rows = {row.local_date: row.status for row in await stored(db_session)}
    assert rows[YESTERDAY] is WellnessPromptStatus.EXPIRED
    assert rows[TODAY] is WellnessPromptStatus.PENDING


async def test_the_sweep_raises_nothing_before_the_days_prompt_hour(
    session_factory: Any, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The day is asked once, in the evening — not at every tick since midnight."""
    monkeypatch.setenv("WELLNESS__PROMPT_HOUR_LOCAL", "23")
    get_settings.cache_clear()

    await run_wellness_prompt_sweep()

    assert await stored(db_session) == []


# --- AC-58: backfill writes days, and asks no questions ---------------------------


async def test_backfill_raises_no_prompts(db_session: AsyncSession) -> None:
    """The decision: a batch write raises no prompt for the days it writes.

    A migration of sixty days of history is not sixty mornings the athlete was
    asked about and did not answer — and a prompt row per imported day would
    make the record say exactly that.
    """
    await service(db_session).record_many(
        [
            DayInput(
                local_date=TODAY - dt.timedelta(days=offset),
                updates={"resting_hr_bpm": 50 + offset},
            )
            for offset in range(1, 6)
        ],
        actor=Actor.athlete(),
        source=WellnessSource.ATHLETE,
    )

    assert await stored(db_session) == []


async def test_a_backfill_covering_today_leaves_todays_prompt_pending(
    db_session: AsyncSession,
) -> None:
    """Today's standing prompt is not answered as a side effect of a migration."""
    raised = await service(db_session).raise_prompt(
        TODAY, actor=Actor.system(), now=NOON
    )
    before = snapshot(await stored(db_session))
    assert raised.status is WellnessPromptStatus.PENDING

    await service(db_session).record_many(
        [
            DayInput(local_date=YESTERDAY, updates={"resting_hr_bpm": 51}),
            DayInput(local_date=TODAY, updates={"resting_hr_bpm": 52}),
        ],
        actor=Actor.athlete(),
        source=WellnessSource.ATHLETE,
    )

    rows = await stored(db_session)
    assert snapshot(rows) == before
    assert rows[0].status is WellnessPromptStatus.PENDING


async def test_a_backfill_for_a_date_whose_prompt_exists_adds_no_duplicate(
    db_session: AsyncSession,
) -> None:
    await service(db_session).raise_prompt(YESTERDAY, actor=Actor.system(), now=NOON)
    before = snapshot(await stored(db_session))

    await service(db_session).record_many(
        [DayInput(local_date=YESTERDAY, updates={"motivation": 4})],
        actor=Actor.athlete(),
        source=WellnessSource.ATHLETE,
    )

    rows = await stored(db_session)
    assert len(rows) == 1
    assert snapshot(rows) == before


async def test_recording_one_day_answers_that_days_standing_prompt(
    db_session: AsyncSession,
) -> None:
    """The per-day write *is* the answer to the day's question.

    The counterpart of `test_backfill_raises_no_prompts`, and the reason the
    two paths differ: the athlete filling in this morning's form has answered
    what they were asked, and a prompt that then expired into "not provided"
    beside a recorded day would be a lie the coach reads as silence.
    """
    await service(db_session).raise_prompt(TODAY, actor=Actor.system(), now=NOON)

    await service(db_session).record(
        TODAY,
        {"fatigue": 3},
        actor=Actor.athlete(),
        source=WellnessSource.ATHLETE,
    )

    [row] = await stored(db_session)
    assert row.status is WellnessPromptStatus.ANSWERED
    assert row.resolved_at is not None


async def test_recording_a_day_whose_prompt_already_expired_does_not_reopen_it(
    db_session: AsyncSession,
) -> None:
    hours = get_settings().wellness.prompt_expiry_hours
    await service(db_session).raise_prompt(YESTERDAY, actor=Actor.system(), now=NOON)
    await service(db_session).expire_prompts(
        actor=Actor.system(), now=NOON + dt.timedelta(hours=hours + 1)
    )
    before = snapshot(await stored(db_session))

    await service(db_session).record(
        YESTERDAY,
        {"fatigue": 3},
        actor=Actor.athlete(),
        source=WellnessSource.ATHLETE,
    )

    assert snapshot(await stored(db_session)) == before


async def test_a_dry_run_answers_nothing(db_session: AsyncSession) -> None:
    await service(db_session).raise_prompt(TODAY, actor=Actor.system(), now=NOON)

    await service(db_session).record(
        TODAY,
        {"fatigue": 3},
        actor=Actor.athlete(),
        source=WellnessSource.ATHLETE,
        dry_run=True,
    )

    [row] = await stored(db_session)
    assert row.status is WellnessPromptStatus.PENDING


async def test_a_prompt_is_never_raised_as_a_side_effect_of_a_read(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Reading the surface asks the athlete nothing."""
    assert (await client.get("/api/v1/wellness/prompt")).status_code == 200
    assert (
        await client.get(
            "/api/v1/wellness/days",
            params={
                "start": YESTERDAY.isoformat(),
                "end": (TODAY + dt.timedelta(days=1)).isoformat(),
            },
        )
    ).status_code == 200

    assert await stored(db_session) == []


async def test_the_backfill_endpoint_raises_no_prompts_and_answers_none(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The same rule through the surface the migration actually calls.

    `POST /wellness/backfill` is the endpoint a file of sixty days arrives on,
    and the MCP tool `record_wellness_days` is the same service call. Asserted
    here as well as at the service, because this is the path where a stray
    prompt per imported day would first show up.
    """
    await service(db_session).raise_prompt(TODAY, actor=Actor.system(), now=NOON)
    before = snapshot(await stored(db_session))

    response = await client.post(
        "/api/v1/wellness/backfill",
        json={
            "days": [
                {"local_date": YESTERDAY.isoformat(), "resting_hr_bpm": 48},
                {"local_date": TODAY.isoformat(), "resting_hr_bpm": 47},
            ]
        },
    )

    assert response.status_code == 200, response.text
    assert snapshot(await stored(db_session)) == before

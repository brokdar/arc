"""The wellness tools, driven end to end through a real MCP client.

Standing rule 1 of the increment plan is that nothing ships to the UI in one PR
and to the agent in a later "registration" pass, so these are the agent's half
of `test_wellness_api.py` — the same capabilities, proved over the wire, plus
the three things only this surface has: the write cap, the explicit `clear`,
and the compact block on the one-call opener.
"""

import datetime as dt
import json
from typing import Any

import pytest
from fastmcp.exceptions import ToolError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import athlete_today
from app.domain.actor import Actor
from app.domain.wellness import MAX_BACKFILL_DAYS, Confounder
from app.persistence.audit import AuditLogEntry
from app.persistence.wellness import WellnessDayRow
from app.services.wellness import WellnessService
from tests.unit.mcp_harness import connected_as, server_for

_KEY = "a1b2c3d4" * 4
COACH = f"coach:write:{_KEY}"
READER = f"reader:read:{_KEY[::-1]}"

#: Today on the athlete's clock — the same one `WellnessService.local_today`
#: reads, because that is the day these tests are about. Not `dt.date.today()`,
#: which is the *container's* clock and a third answer to the question
#: (issue #62); the DTZ rules now refuse it.
TODAY = athlete_today()


async def call(entry: str, tool: str, arguments: dict[str, Any] | None = None) -> Any:
    """Call one tool as ``entry``, over the in-memory transport."""
    async with connected_as(server_for(COACH, READER), entry) as client:
        result = await client.call_tool(tool, arguments or {})
        return result.data


def past(offset: int) -> str:
    """An ISO date ``offset`` days ago."""
    return (TODAY - dt.timedelta(days=offset)).isoformat()


def field_count(value: Any) -> int:
    """Every key in ``value``, at every depth, lists walked into.

    ``len(block)`` counts the four or five top-level keys and misses the
    hundred beneath them, which is how a budget written against the outside of
    a nested block stops measuring anything.
    """
    if isinstance(value, dict):
        return len(value) + sum(field_count(item) for item in value.values())
    if isinstance(value, list):
        return sum(field_count(item) for item in value)
    return 0


# --- the self-describing vocabulary (AC-43) -----------------------------------


async def test_the_inputs_tool_answers_every_what_may_i_send_question(
    session_factory: Any,
) -> None:
    # The eval's "wasted purpose guesses" metric, for this surface: the agent
    # must never discover the confounder vocabulary by submitting one.
    inputs = (await call(READER, "get_wellness_inputs"))["inputs"]

    assert {entry["value"] for entry in inputs["confounders"]} == {
        member.value for member in Confounder
    }
    assert {scale["field"] for scale in inputs["scales"]} >= {"fatigue", "rpe"}
    assert inputs["max_backfill_days"] == MAX_BACKFILL_DAYS
    assert inputs["tiers"]


async def test_the_scales_carry_their_polarity_and_their_words(
    session_factory: Any,
) -> None:
    inputs = (await call(READER, "get_wellness_inputs"))["inputs"]
    by_field = {scale["field"]: scale for scale in inputs["scales"]}

    assert by_field["fatigue"]["polarity"] == "higher_is_worse"
    assert by_field["motivation"]["polarity"] == "higher_is_better"
    # RPE is a magnitude, not a valence — a 9 is a hard session, not a bad one.
    assert by_field["rpe"]["polarity"] == "higher_is_neither"
    assert by_field["fatigue"]["anchors"]["1"]


# --- one call records one day (AC-1, AC-2, AC-3) ------------------------------


async def test_one_day_is_one_call_and_reads_back_through_the_same_surface(
    session_factory: Any,
) -> None:
    await call(
        COACH,
        "record_wellness",
        {
            "sleep_duration_s": 27_000,
            "resting_hr_bpm": 47,
            "hrv_ms": 58.0,
            "hrv_metric": "rmssd",
            "hrv_context": "sleeping",
            "fatigue": 3,
            "motivation": 4,
            "note": "Slept through.",
        },
    )

    read = await call(
        READER,
        "get_wellness",
        {"start": past(1), "end": (TODAY + dt.timedelta(days=1)).isoformat()},
    )

    [day] = read["items"]
    assert day["local_date"] == TODAY.isoformat()
    assert day["resting_hr_bpm"] == 47
    assert day["note"] == "Slept through."


async def test_an_agent_write_is_athlete_reported_but_sourced_to_the_agent(
    session_factory: Any, db_session: AsyncSession
) -> None:
    # The invariant: the agent records what it was told and never signs as the
    # athlete. There is no argument by which it could.
    await call(COACH, "record_wellness", {"fatigue": 3})

    [row] = (await db_session.execute(select(WellnessDayRow))).scalars().all()
    assert row.provenance.value == "athlete_reported"
    assert row.source.value == "agent"


async def test_a_call_with_no_field_is_refused_and_costs_no_budget(
    session_factory: Any,
) -> None:
    before = (await call(COACH, "record_wellness", {"fatigue": 3, "dry_run": True}))[
        "budget_remaining"
    ]

    with pytest.raises(ToolError, match="at least one field"):
        await call(COACH, "record_wellness", {})

    after = (await call(COACH, "record_wellness", {"fatigue": 3, "dry_run": True}))[
        "budget_remaining"
    ]
    assert after == before


async def test_an_unknown_field_is_refused_with_the_vocabulary_named(
    session_factory: Any,
) -> None:
    with pytest.raises(ToolError, match="unknown field"):
        await call(COACH, "record_wellness", {"clear": ["hrv_rmssd_ms"]})


async def test_an_unknown_confounder_names_every_legal_one(
    session_factory: Any,
) -> None:
    with pytest.raises(ToolError) as refusal:
        await call(COACH, "record_wellness", {"confounders": ["hungover"]})

    for member in Confounder:
        assert member.value in str(refusal.value)


# --- the dry run, and the #17 invariant (AC-4) --------------------------------


async def test_a_dry_run_writes_nothing_and_returns_what_would_be_set(
    session_factory: Any, db_session: AsyncSession
) -> None:
    answer = await call(
        COACH, "record_wellness", {"resting_hr_bpm": 47, "dry_run": True}
    )

    assert answer["dry_run"] is True
    assert answer["day"]["changed"]["resting_hr_bpm"]["to"] == 47
    db_session.expire_all()
    assert (await db_session.execute(select(WellnessDayRow))).scalars().all() == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("spo2", 97),
        ("resting_hr_bpm", 5),
        ("hrv_ms", 9_000),
        ("weight_kg", 5),
        ("fatigue", 9),
    ],
)
async def test_a_dry_run_refuses_exactly_what_the_write_refuses(
    session_factory: Any, field: str, value: float
) -> None:
    # Bounds are domain rules on the shared path, so nothing a dry run accepts
    # can fail at the write (issue #17).
    with pytest.raises(ToolError):
        await call(COACH, "record_wellness", {field: value, "dry_run": True})
    with pytest.raises(ToolError):
        await call(COACH, "record_wellness", {field: value})


# --- clearing (AC-57) ---------------------------------------------------------


async def test_a_value_can_be_cleared_and_the_clear_is_audited(
    session_factory: Any, db_session: AsyncSession
) -> None:
    await call(COACH, "record_wellness", {"fatigue": 3, "resting_hr_bpm": 47})

    answer = await call(COACH, "record_wellness", {"clear": ["resting_hr_bpm"]})

    assert answer["wellness"]["resting_hr_bpm"] is None
    assert answer["wellness"]["fatigue"] == 3
    db_session.expire_all()
    actions = [
        row.action
        for row in (await db_session.execute(select(AuditLogEntry))).scalars()
    ]
    assert actions.count("wellness.updated") == 1


async def test_setting_and_clearing_the_same_field_is_refused(
    session_factory: Any,
) -> None:
    with pytest.raises(ToolError, match="both set and clear"):
        await call(COACH, "record_wellness", {"fatigue": 3, "clear": ["fatigue"]})


# --- backfill over MCP (AC-26, AC-29) -----------------------------------------


async def test_a_batch_costs_one_write_against_the_cap_whatever_its_size(
    session_factory: Any,
) -> None:
    # The decision this tool exists for: a 60-day migration is one decision the
    # athlete asked for, not sixty. Looping the per-day tool would strand it.
    before = (await call(COACH, "record_wellness", {"fatigue": 3, "dry_run": True}))[
        "budget_remaining"
    ]

    answer = await call(
        COACH,
        "record_wellness_days",
        {
            "days": [
                {"date": past(offset), "resting_hr_bpm": 46 + offset % 3}
                for offset in range(1, 61)
            ]
        },
    )

    assert answer["day_count"] == 60
    assert answer["outcomes"] == {"created": 60}
    assert answer["budget_remaining"] == before - 1


async def test_a_dry_run_batch_costs_nothing_and_writes_nothing(
    session_factory: Any, db_session: AsyncSession
) -> None:
    days = [{"date": past(offset), "resting_hr_bpm": 46} for offset in range(1, 11)]

    answer = await call(COACH, "record_wellness_days", {"days": days, "dry_run": True})

    assert answer["outcomes"] == {"created": 10}
    db_session.expire_all()
    assert (await db_session.execute(select(WellnessDayRow))).scalars().all() == []


async def test_a_batch_day_missing_its_date_is_refused_by_position(
    session_factory: Any,
) -> None:
    with pytest.raises(ToolError, match="day 2 needs a `date`"):
        await call(
            COACH,
            "record_wellness_days",
            {"days": [{"date": past(2), "fatigue": 3}, {"fatigue": 4}]},
        )


async def test_a_future_day_is_refused_on_both_write_paths(
    session_factory: Any,
) -> None:
    tomorrow = (TODAY + dt.timedelta(days=1)).isoformat()

    with pytest.raises(ToolError, match="has not happened yet"):
        await call(COACH, "record_wellness", {"date": tomorrow, "fatigue": 3})
    with pytest.raises(ToolError, match="has not happened yet"):
        await call(
            COACH,
            "record_wellness_days",
            {"days": [{"date": tomorrow, "fatigue": 3}]},
        )


async def test_a_backfilled_day_reads_back_on_the_date_it_describes(
    session_factory: Any,
) -> None:
    await call(
        COACH,
        "record_wellness_days",
        {"days": [{"date": past(90), "resting_hr_bpm": 48, "fatigue": 3}]},
    )

    read = await call(READER, "get_wellness", {"start": past(91), "end": past(89)})

    [day] = read["items"]
    assert day["local_date"] == past(90)
    # Entered today, describing a day three months ago: the subjective half is
    # recall and says so, and the device number is not discounted for it.
    assert day["subjective_recalled"] is True
    assert day["resting_hr_bpm"] == 48


# --- the confounder pre-check, on the agent surface (AC-36) -------------------


async def test_a_declared_confounder_reports_the_markers_not_actionable(
    session_factory: Any,
) -> None:
    await call(
        COACH,
        "record_wellness",
        {"resting_hr_bpm": 43, "confounders": ["alcohol"], "note": "two beers"},
    )

    read = await call(
        READER,
        "get_wellness",
        {"start": TODAY.isoformat(), "end": (TODAY + dt.timedelta(days=1)).isoformat()},
    )

    [day] = read["items"]
    assert day["resting_hr_bpm"] == 43, "the number is real and stays visible"
    assert day["markers"]["actionable"] is False
    assert day["markers"]["invalidated_by"] == ["alcohol"]


# --- gaps, weeks and the compact opener ---------------------------------------


async def test_a_range_read_names_the_days_nobody_answered(
    session_factory: Any,
) -> None:
    await call(COACH, "record_wellness", {"date": past(2), "fatigue": 3})

    read = await call(
        READER,
        "get_wellness",
        {"start": past(3), "end": (TODAY + dt.timedelta(days=1)).isoformat()},
    )

    assert [day["local_date"] for day in read["items"]] == [past(2)]
    assert read["missing"] == [past(3), past(1), TODAY.isoformat()]


async def test_the_weekly_fold_reports_the_n_behind_every_mean(
    session_factory: Any,
) -> None:
    for offset in (1, 2, 3):
        await call(
            COACH, "record_wellness", {"date": past(offset), "resting_hr_bpm": 48}
        )

    summary = (
        await call(
            READER,
            "get_wellness_weeks",
            {"start": past(20), "end": (TODAY + dt.timedelta(days=1)).isoformat()},
        )
    )["wellness"]

    means = [
        mean
        for week in summary["weeks"]
        for mean in week["metrics"]
        if mean["metric"] == "resting_hr_bpm"
    ]
    assert means, "a recorded metric must appear in the fold"
    assert sum(mean["n"] for mean in means) == 3


async def test_an_invalidated_day_is_left_out_of_the_objective_means(
    session_factory: Any,
) -> None:
    # A mean built partly out of artefacts is worse than a shorter honest one.
    await call(COACH, "record_wellness", {"date": past(1), "resting_hr_bpm": 48})
    await call(
        COACH,
        "record_wellness",
        {"date": past(2), "resting_hr_bpm": 62, "confounders": ["alcohol"]},
    )

    summary = (
        await call(
            READER,
            "get_wellness_weeks",
            {"start": past(20), "end": (TODAY + dt.timedelta(days=1)).isoformat()},
        )
    )["wellness"]

    means = {
        mean["metric"]: mean for week in summary["weeks"] for mean in week["metrics"]
    }
    assert means["resting_hr_bpm"] == {"metric": "resting_hr_bpm", "mean": 48.0, "n": 1}
    invalidated = sum(week["days_invalidated"] for week in summary["weeks"])
    assert invalidated == 1


async def test_hrv_is_never_pooled_across_statistic_or_context(
    session_factory: Any,
) -> None:
    # AC-46's fold half: a sleeping RMSSD mean and a daytime SDNN mean averaged
    # together belong to neither. This is the test that stops a later refactor
    # from "simplifying" the pooling back in.
    await call(
        COACH,
        "record_wellness",
        {
            "date": past(1),
            "hrv_ms": 60.0,
            "hrv_metric": "rmssd",
            "hrv_context": "sleeping",
        },
    )
    await call(
        COACH,
        "record_wellness",
        {
            "date": past(2),
            "hrv_ms": 120.0,
            "hrv_metric": "sdnn",
            "hrv_context": "waking_spot",
        },
    )

    summary = (
        await call(
            READER,
            "get_wellness_weeks",
            {"start": past(20), "end": (TODAY + dt.timedelta(days=1)).isoformat()},
        )
    )["wellness"]

    means = {
        mean["metric"]: mean["mean"]
        for week in summary["weeks"]
        for mean in week["metrics"]
        if mean["metric"].startswith("hrv_ms")
    }
    assert means == {"hrv_ms[rmssd,sleeping]": 60.0, "hrv_ms[sdnn,waking_spot]": 120.0}


async def test_the_coaching_context_carries_today_and_a_compact_recent_series(
    session_factory: Any,
) -> None:
    await call(COACH, "record_wellness", {"fatigue": 2, "resting_hr_bpm": 46})
    await call(
        COACH,
        "record_wellness",
        {"date": past(1), "resting_hr_bpm": 50, "confounders": ["alcohol"]},
    )

    block = (await call(READER, "get_coaching_context"))["wellness"]

    assert block["today"]["fatigue"] == 2
    compact = {day["local_date"]: day for day in block["recent"]}
    assert compact[past(1)]["not_actionable"] == ["alcohol"]
    # A field the athlete did not report is absent, not null: a null on the one
    # call every session begins with is a token spent saying nothing.
    assert "sleep_duration_s" not in compact[past(1)]


async def test_the_coaching_context_block_stays_small_with_a_long_history(
    session_factory: Any, db_session: AsyncSession
) -> None:
    # AC-55: a pinned budget, so a field added later that would bloat the one
    # call every session begins with fails here rather than quietly costing the
    # coach tokens forever.
    #
    # A prompt is standing for today, and the batch path deliberately does not
    # answer it, so the block is measured with every key it can carry populated.
    # A null `prompt` would leave that object's own fields outside the budget —
    # which is exactly how `readiness` and `prompt` came to be free.
    await WellnessService.from_session(db_session).raise_prompt(
        TODAY, actor=Actor.system()
    )
    await db_session.commit()
    await call(
        COACH,
        "record_wellness_days",
        {
            "days": [
                {
                    "date": past(offset),
                    "resting_hr_bpm": 46,
                    "hrv_ms": 58.0,
                    "hrv_metric": "rmssd",
                    "hrv_context": "sleeping",
                    "sleep_duration_s": 27_000,
                    "weight_kg": 78.0,
                    "fatigue": 3,
                    "motivation": 4,
                    "soreness": 2,
                    "stress": 2,
                    "sleep_quality": 4,
                    "note": "x" * 200,
                }
                for offset in range(90)
            ]
        },
    )

    block = (await call(READER, "get_coaching_context"))["wellness"]

    # The week, which is a different claim from the budget below: `recent` is
    # seven days whatever the history holds.
    assert len(block["recent"]) == 7, "the opener carries a week, not the history"
    fields = sum(len(day) for day in block["recent"])
    assert fields <= 70, (
        "the compact series has grown; whole days belong in get_wellness"
    )
    # The fixture must be at its fullest for the budget to mean anything: a
    # null `prompt` costs 4 fields, and if this setup ever stops taking effect
    # the ceilings would silently go back to covering 106 of the block's
    # fields — the same "the budget stopped covering what it claims" failure
    # this whole assertion exists to end, one level up.
    assert block["prompt"] is not None, "the fixture must raise a prompt"
    assert block["readiness"] is not None, "the fixture must produce a readiness block"
    assert block["today"] is not None, "the fixture must record today"

    # And the whole block, because budgeting `recent` alone left everything
    # else free: `readiness` and `prompt` were each added to this block without
    # touching the ceiling above, which is 50 fields nobody counted on the one
    # call every session begins with.
    #
    # Measured 2026-08-14 against this 90-day fixture: **110 fields, 2610
    # bytes**. The slack is deliberately SMALLER than the smallest key anyone
    # would plausibly add — a `prompt`-shaped key is 4 fields and ~130 bytes —
    # so that every addition forces a conscious edit to these numbers rather
    # than sliding underneath them. That ratchet is the point: an earlier draft
    # of this test left 10 fields of headroom "for a marker or two", and a
    # 4-field key measured through it untouched, which is precisely the
    # regression that produced this criterion.
    #
    # The reserved-for-markers rationale was wrong twice: a marker costs 4
    # fields, not one, and all 90 fixture days are identical, so the SD is zero
    # and `readiness.markers` can never be non-empty here. The count is fully
    # deterministic at 110; the only jitter in the byte figure is `expires_at`
    # dropping a zero microsecond field, 7 bytes at roughly one in a million.
    #
    # A key that blows this budget must either justify what it costs on every
    # session opener — this is the most expensive place in the surface to add a
    # field — or live behind its own tool, which is where `get_wellness`,
    # `get_wellness_trend` and `get_wellness_weeks` already are.
    assert field_count(block) <= 112, (
        "the wellness block has grown; a field here is paid for on every "
        "coaching session"
    )
    assert len(json.dumps(block).encode()) <= 2_640, (
        "the wellness block has grown in bytes; long strings cost the coach "
        "tokens even when the field count holds"
    )


async def test_recording_today_answers_the_day_s_standing_prompt(
    session_factory: Any, db_session: AsyncSession
) -> None:
    # The agent's half of `test_answering_the_prompt_writes_the_day_and_closes
    # _the_question`: there is no `answer_prompt` tool and there is not meant to
    # be one — filling in the day *is* the answer, so an agent that records for
    # the athlete closes the question the application put to them. Pinned on
    # this surface because the docstring now says so, and a promise made to an
    # agent in prose is a promise nothing else keeps.
    await WellnessService.from_session(db_session).raise_prompt(
        TODAY, actor=Actor.system()
    )
    await db_session.commit()
    before = (await call(READER, "get_coaching_context"))["wellness"]["prompt"]
    assert before["status"] == "pending"

    await call(COACH, "record_wellness", {"fatigue": 3})

    prompt = (await call(READER, "get_coaching_context"))["wellness"]["prompt"]
    assert prompt["status"] == "answered"
    assert prompt["resolved_at"]


async def test_the_context_says_nothing_recorded_rather_than_inventing_a_day(
    session_factory: Any,
) -> None:
    block = (await call(READER, "get_coaching_context"))["wellness"]

    assert block["today"] is None
    assert block["recent"] == []
    assert block["weight_in_force"] is None


# --- the red flag does not stop the athlete reporting in (AC-52) --------------


async def test_recording_wellness_works_while_the_red_flag_is_up(
    session_factory: Any, client: Any
) -> None:
    # A day's readings are testimony, not an intensification, and an ill
    # athlete is exactly who most needs them recorded.
    response = await client.patch(
        "/api/v1/athlete",
        json={
            "red_flag_active": True,
            "red_flag_severity": "moderate",
            "red_flag_note": "chest infection",
        },
    )
    assert response.status_code == 200, response.text

    answer = await call(
        COACH, "record_wellness", {"fatigue": 5, "confounders": ["illness_onset"]}
    )

    assert answer["wellness"]["fatigue"] == 5


# --- paging, on the surface AC-54 asks for it on ------------------------------


async def test_a_paged_range_reports_the_gaps_in_the_range_not_in_the_page(
    session_factory: Any,
) -> None:
    # The coach's first read after a migration is the migration read back. When
    # `missing` was derived from the page, 40 of 90 recorded days came back
    # named as silence — and the tool docstring tells the agent to trust it.
    span = 90
    await call(
        COACH,
        "record_wellness_days",
        {
            "days": [
                {"date": past(offset), "resting_hr_bpm": 46}
                for offset in range(1, span + 1)
            ]
        },
    )

    read = await call(
        READER,
        "get_wellness",
        {
            "start": past(span),
            "end": (TODAY + dt.timedelta(days=1)).isoformat(),
            "limit": 50,
        },
    )

    assert read["total"] == span
    assert read["returned"] == 50
    assert read["missing"] == [TODAY.isoformat()]
    recorded = {day["local_date"] for day in read["items"]}
    assert not recorded & set(read["missing"])


async def test_paging_reaches_the_rest_of_the_range(session_factory: Any) -> None:
    await call(
        COACH,
        "record_wellness_days",
        {"days": [{"date": past(offset), "fatigue": 3} for offset in range(1, 61)]},
    )

    first = await call(
        READER,
        "get_wellness",
        {"start": past(60), "end": TODAY.isoformat(), "limit": 50},
    )
    second = await call(
        READER,
        "get_wellness",
        {"start": past(60), "end": TODAY.isoformat(), "limit": 50, "offset": 50},
    )

    assert first["returned"] == 50
    assert second["returned"] == 10
    # Both pages agree about the range, and neither invents a gap in it.
    assert first["total"] == second["total"] == 60
    assert first["missing"] == second["missing"] == []


async def test_a_range_longer_than_a_year_is_refused(session_factory: Any) -> None:
    with pytest.raises(ToolError, match="at most 371 days"):
        await call(
            READER,
            "get_wellness",
            {"start": "2000-01-01", "end": TODAY.isoformat()},
        )


# --- who wrote it, on the one call every session begins with (AC-56) ----------


async def test_the_compact_series_marks_a_day_the_agent_wrote_down(
    session_factory: Any, client: Any
) -> None:
    # A coach that cannot tell the athlete's report from its own transcription
    # will eventually cite its own echo back as evidence. Spelling the ordinary
    # case on all seven days would cost fourteen fields saying "as usual", so
    # the departure is what is stated.
    await call(COACH, "record_wellness", {"date": past(1), "fatigue": 3})
    response = await client.patch(
        f"/api/v1/wellness/days/{past(2)}", json={"fatigue": 4}
    )
    assert response.status_code == 200, response.text

    block = (await call(READER, "get_coaching_context"))["wellness"]
    compact = {day["local_date"]: day for day in block["recent"]}

    assert compact[past(1)]["source"] == "agent"
    assert "source" not in compact[past(2)], (
        "the athlete's own report is the default and says nothing"
    )
    assert "provenance" not in compact[past(2)]


# --- the trend read on the agent surface (AC-32, AC-36) -----------------------


async def seed(days: list[dict[str, Any]]) -> None:
    """Write a batch of days as the coach."""
    await call(COACH, "record_wellness_days", {"days": days})


def sleeping(offset: int, value: float, **fields: Any) -> dict[str, Any]:
    """One backfill day carrying a sleeping RMSSD reading."""
    return {
        "date": past(offset),
        "hrv_ms": value,
        "hrv_metric": "rmssd",
        "hrv_context": "sleeping",
        **fields,
    }


async def test_the_trend_tool_answers_per_metric_with_its_own_maturity(
    session_factory: Any,
) -> None:
    # Forty complete days imported in one batch. The objective half matures at
    # full weight — the watch measured it on the day, whatever day it was typed
    # in — and the subjective half does not, because nobody accurately recalls
    # last month's Tuesday motivation. Two maturities, two objects, one call.
    await seed(
        [
            sleeping(offset, 55.0 + offset % 4, motivation=3 + offset % 3)
            for offset in range(40)
        ]
    )

    answer = (
        await call(
            READER,
            "get_wellness_trend",
            {
                "start": past(59),
                "end": (TODAY + dt.timedelta(days=1)).isoformat(),
                "metrics": ["motivation", "hrv_rmssd_ms"],
            },
        )
    )["trend"]

    hrv = answer["metrics"]["hrv_rmssd_ms"]["baseline"]
    assert hrv["kind"] == "banded"
    assert hrv["n"] == 40
    assert hrv["hrv_context"] == "sleeping"
    assert answer["metrics"]["hrv_rmssd_ms"]["rolling_mean_7d"]["n"] == 7

    motivation = answer["metrics"]["motivation"]["baseline"]
    assert motivation["kind"] == "abstention"
    # Thirty-seven of the forty were recalled and do not count; only the three
    # entered within the recall window do.
    assert motivation["readings"]["statement"] == "3 of 14"
    assert "mean" not in motivation
    # The three days close enough to be report rather than memory still fold
    # into the seven-day mean, and it says how many it had.
    assert answer["metrics"]["motivation"]["rolling_mean_7d"]["n"] == 3


async def test_the_trend_tool_reports_a_gap_as_a_gap(session_factory: Any) -> None:
    await seed(
        [
            {"date": past(4), "resting_hr_bpm": 50},
            {"date": past(2), "resting_hr_bpm": 52},
        ]
    )

    series = (
        await call(
            READER,
            "get_wellness_trend",
            {
                "start": past(4),
                "end": past(1),
                "metrics": ["resting_hr_bpm"],
            },
        )
    )["trend"]["metrics"]["resting_hr_bpm"]["series"]

    # Null, never zero: a line drawn to zero is a heart that stopped.
    assert [point["value"] for point in series] == [50, None, 52]


async def test_an_unknown_metric_is_refused_with_the_vocabulary(
    session_factory: Any,
) -> None:
    with pytest.raises(ToolError, match="hrv_rmssd_ms"):
        await call(
            READER,
            "get_wellness_trend",
            {"start": past(6), "end": TODAY.isoformat(), "metrics": ["hrv"]},
        )


async def test_a_voided_morning_says_so_beside_its_numbers_on_the_trend(
    session_factory: Any,
) -> None:
    await call(
        COACH,
        "record_wellness",
        {
            "date": past(1),
            "resting_hr_bpm": 43,
            "confounders": ["alcohol", "short_sleep", "travel"],
        },
    )

    series = (
        await call(
            READER,
            "get_wellness_trend",
            {
                "start": past(2),
                "end": TODAY.isoformat(),
                "metrics": ["resting_hr_bpm"],
            },
        )
    )["trend"]["metrics"]["resting_hr_bpm"]["series"]
    point = next(item for item in series if item["local_date"] == past(1))

    assert point["value"] == 43
    assert point["markers"]["statement"] == (
        "recorded, not actionable: alcohol, short_sleep"
    )
    assert point["markers"]["invalidated_by"] == ["alcohol", "short_sleep"]


async def test_the_coaching_context_names_the_confounder_beside_the_readings(
    session_factory: Any,
) -> None:
    # AC-36 on the one call every session begins with. The numbers are still
    # here — they are real and part of the history — and what is withheld is
    # their standing as evidence about today, stated on the same object.
    await call(
        COACH,
        "record_wellness",
        {"resting_hr_bpm": 43, "confounders": ["alcohol", "hot_room"]},
    )

    block = (await call(READER, "get_coaching_context"))["wellness"]

    assert block["today"]["resting_hr_bpm"] == 43
    assert block["today"]["markers"]["statement"] == (
        "recorded, not actionable: alcohol, hot_room"
    )
    compact = {day["local_date"]: day for day in block["recent"]}
    assert compact[TODAY.isoformat()]["not_actionable"] == ["alcohol", "hot_room"]
    assert compact[TODAY.isoformat()]["resting_hr_bpm"] == 43


async def test_a_non_invalidating_confounder_leaves_the_markers_actionable(
    session_factory: Any,
) -> None:
    await call(
        COACH, "record_wellness", {"resting_hr_bpm": 43, "confounders": ["travel"]}
    )

    block = (await call(READER, "get_coaching_context"))["wellness"]

    assert block["today"]["markers"]["actionable"] is True
    assert "not_actionable" not in {key for day in block["recent"] for key in day}


async def test_the_coaching_context_carries_the_readiness_projection(
    session_factory: Any,
) -> None:
    # The whole point of the feature on the surface that matters most: the
    # morning read arrives with the athlete's own normal already applied.
    await seed(
        [
            sleeping(offset, 55.0 + offset % 4, resting_hr_bpm=48 + offset % 4)
            for offset in range(60)
        ]
    )

    projection = (await call(READER, "get_coaching_context"))["wellness"]["readiness"]

    assert projection["markers_outside_band"]["statement"] == "0 of 2"
    assert projection["markers_outside_band"]["markers"] == []
    # A count and a label. No score, no recommendation, no verdict.
    assert set(projection) <= {"as_of", "markers_outside_band", "joint_state"}


async def test_the_weekly_fold_still_carries_its_n(session_factory: Any) -> None:
    # AC-39's edge on the surface the weekly fold actually has. Anchored to
    # last week's Monday, not this week's: the backfill guard rejects a
    # future day, and "this week's Monday + 2" is still ahead of today on a
    # Monday or a Tuesday. A completed week is always safe to seed.
    monday = TODAY - dt.timedelta(days=TODAY.weekday() + 7)
    await seed(
        [
            {
                "date": (monday + dt.timedelta(days=index)).isoformat(),
                "resting_hr_bpm": 48 + index,
            }
            for index in range(3)
        ]
    )

    answer = await call(
        READER,
        "get_wellness_weeks",
        {
            "start": monday.isoformat(),
            "end": (monday + dt.timedelta(days=7)).isoformat(),
        },
    )

    [week] = answer["wellness"]["weeks"]
    [resting] = [
        metric for metric in week["metrics"] if metric["metric"] == "resting_hr_bpm"
    ]
    assert resting == {"metric": "resting_hr_bpm", "mean": 49.0, "n": 3}

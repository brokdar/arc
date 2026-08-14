"""The wellness tools, driven end to end through a real MCP client.

Standing rule 1 of the increment plan is that nothing ships to the UI in one PR
and to the agent in a later "registration" pass, so these are the agent's half
of `test_wellness_api.py` — the same capabilities, proved over the wire, plus
the three things only this surface has: the write cap, the explicit `clear`,
and the compact block on the one-call opener.
"""

import datetime as dt
from typing import Any

import pytest
from fastmcp.exceptions import ToolError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.wellness import MAX_BACKFILL_DAYS, Confounder
from app.persistence.audit import AuditLogEntry
from app.persistence.wellness import WellnessDayRow
from tests.unit.mcp_harness import connected_as, server_for

_KEY = "a1b2c3d4" * 4
COACH = f"coach:write:{_KEY}"
READER = f"reader:read:{_KEY[::-1]}"

TODAY = dt.date.today()


async def call(entry: str, tool: str, arguments: dict[str, Any] | None = None) -> Any:
    """Call one tool as ``entry``, over the in-memory transport."""
    async with connected_as(server_for(COACH, READER), entry) as client:
        result = await client.call_tool(tool, arguments or {})
        return result.data


def past(offset: int) -> str:
    """An ISO date ``offset`` days ago."""
    return (TODAY - dt.timedelta(days=offset)).isoformat()


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
    session_factory: Any,
) -> None:
    # AC-55: a pinned budget, so a field added later that would bloat the one
    # call every session begins with fails here rather than quietly costing the
    # coach tokens forever.
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

    assert len(block["recent"]) == 7, "the opener carries a week, not the history"
    fields = sum(len(day) for day in block["recent"])
    assert fields <= 70, (
        "the compact series has grown; whole days belong in get_wellness"
    )


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

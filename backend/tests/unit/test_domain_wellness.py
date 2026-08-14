"""The pure rules of the daily wellness series.

Everything here runs without a database, because everything here is a
statement about what a day *is* — which day an overnight reading belongs to,
what a confounder does to the morning it describes, which weight governs a
date. The API and MCP surfaces re-test the ones that must survive an adapter;
these are the ones that must survive a refactor.
"""

import datetime as dt

import pytest

from app.domain.activity import session_date
from app.domain.wellness import (
    BOUNDS,
    INPUT_TIERS,
    INVALIDATES_MARKERS,
    MAX_NOTE_CHARS,
    OBJECTIVE_FIELDS,
    SUBJECTIVE_FIELDS,
    SUBJECTIVE_SCALES,
    VALUE_FIELDS,
    WELLNESS_LATE_ENTRY_DAYS,
    WRITABLE_FIELDS,
    BodyRegion,
    Confounder,
    HrvContext,
    HrvMetric,
    InputTier,
    Polarity,
    WellnessDay,
    entry_lag_days,
    is_late_entry,
    marker_standing,
    missing_dates,
    weight_in_force,
    wellness_day_date,
)
from app.services.activity import MAX_RPE, MIN_RPE

BERLIN = "Europe/Berlin"


def day(date: dt.date, **fields: object) -> WellnessDay:
    """A wellness day on ``date`` carrying ``fields``."""
    return WellnessDay(local_date=date, **fields)  # type: ignore[arg-type]


# --- the wake-day rule (AC-50) ------------------------------------------------


def test_an_overnight_reading_belongs_to_the_wake_day() -> None:
    # 23:30 on the 12th to 07:00 on the 13th, Berlin time. The athlete reads
    # these numbers over coffee on the 13th and calls them "this morning's".
    woke = dt.datetime(2026, 8, 13, 5, 0, tzinfo=dt.UTC)  # 07:00 Berlin

    assert wellness_day_date(woke, BERLIN) == dt.date(2026, 8, 13)


def test_the_wake_day_rule_is_deliberately_not_the_session_rule() -> None:
    # A session that starts at 23:30 on the 12th belongs to the 12th; a night
    # that starts at 23:30 on the 12th belongs to the 13th. The two answer
    # different questions and the difference is load-bearing: applying the
    # session rule to sleep would land every overnight reading on the day
    # before the one the athlete reads it on, and every other test would pass.
    began = dt.datetime(2026, 8, 12, 21, 30, tzinfo=dt.UTC)  # 23:30 Berlin
    woke = dt.datetime(2026, 8, 13, 5, 0, tzinfo=dt.UTC)  # 07:00 Berlin

    assert session_date(began, BERLIN) == dt.date(2026, 8, 12)
    assert wellness_day_date(woke, BERLIN) == dt.date(2026, 8, 13)


def test_the_wake_day_is_the_athletes_day_not_the_servers() -> None:
    # 00:30 Berlin on the 13th is still 22:30 UTC on the 12th. The athlete's
    # clock decides, because there is one athlete and therefore one clock.
    woke = dt.datetime(2026, 8, 12, 22, 30, tzinfo=dt.UTC)

    assert wellness_day_date(woke, BERLIN) == dt.date(2026, 8, 13)
    assert wellness_day_date(woke, "UTC") == dt.date(2026, 8, 12)


def test_a_naive_moment_is_refused_rather_than_assumed() -> None:
    with pytest.raises(ValueError, match="aware"):
        wellness_day_date(dt.datetime(2026, 8, 13, 7, 0), BERLIN)  # noqa: DTZ001


# --- late entry, and its asymmetry (AC-30) ------------------------------------


def test_a_same_day_entry_is_not_late() -> None:
    entered = dt.datetime(2026, 8, 14, 6, 30, tzinfo=dt.UTC)

    assert entry_lag_days(dt.date(2026, 8, 14), entered, "UTC") == 0
    assert not is_late_entry(dt.date(2026, 8, 14), entered, "UTC")


def test_lateness_starts_after_the_declared_window() -> None:
    described = dt.date(2026, 8, 1)
    at_the_bound = dt.datetime(2026, 8, 1, 9, 0, tzinfo=dt.UTC) + dt.timedelta(
        days=WELLNESS_LATE_ENTRY_DAYS
    )
    one_past = at_the_bound + dt.timedelta(days=1)

    assert not is_late_entry(described, at_the_bound, "UTC")
    assert is_late_entry(described, one_past, "UTC")


def test_the_lag_is_measured_on_the_athletes_calendar() -> None:
    # 22:30 UTC is 00:30 the next day in Berlin. A reading described by the
    # 12th and typed at that moment is one day late there, not zero — the
    # server's midnight is not the athlete's.
    entered = dt.datetime(2026, 8, 12, 22, 30, tzinfo=dt.UTC)

    assert entry_lag_days(dt.date(2026, 8, 12), entered, "UTC") == 0
    assert entry_lag_days(dt.date(2026, 8, 12), entered, BERLIN) == 1


# --- confounders and the marker pre-check (D5a) -------------------------------


def test_a_declared_alcohol_day_reports_its_markers_not_actionable() -> None:
    standing = marker_standing([Confounder.ALCOHOL])

    assert not standing.actionable
    assert standing.invalidated_by == (Confounder.ALCOHOL,)
    assert standing.statement == "recorded, not actionable: alcohol"


def test_a_context_only_confounder_leaves_the_markers_standing() -> None:
    # Travel and a hard session yesterday change how a reading should be read;
    # neither voids it. An illness onset least of all — it is the reading the
    # coach most wants.
    standing = marker_standing(
        [
            Confounder.TRAVEL,
            Confounder.HARD_SESSION_PREVIOUS_DAY,
            Confounder.ILLNESS_ONSET,
        ]
    )

    assert standing.actionable
    assert standing.invalidated_by == ()
    assert standing.statement == "recorded"


def test_the_invalidating_set_is_the_five_the_athlete_signed_off() -> None:
    assert {
        Confounder.ALCOHOL,
        Confounder.HOT_ROOM,
        Confounder.POOR_SLEEP_TIMING,
        Confounder.SHORT_SLEEP,
        Confounder.FIRST_SESSION_AFTER_LAYOFF,
    } == INVALIDATES_MARKERS


def test_the_standing_rides_on_the_day_itself() -> None:
    # The whole point of D5a: a coach reading the day sees the standing on the
    # same object as the numbers, and does not have to remember to look.
    recorded = day(
        dt.date(2026, 8, 14), resting_hr_bpm=44, confounders=(Confounder.ALCOHOL,)
    )

    assert recorded.resting_hr_bpm == 44
    assert not recorded.standing.actionable


# --- weight in force (AC-12, AC-13, AC-14) ------------------------------------


def test_the_weight_in_force_is_the_latest_on_or_before_the_date() -> None:
    series = [
        day(dt.date(2026, 8, 1), weight_kg=78.0),
        day(dt.date(2026, 8, 20), weight_kg=82.0),
    ]

    resolved = weight_in_force(series, dt.date(2026, 8, 20))

    assert resolved is not None
    assert resolved.weight_kg == 82.0
    assert resolved.effective_date == dt.date(2026, 8, 20)


def test_appending_a_later_weight_never_moves_an_earlier_date() -> None:
    # Issue #24's whole reason for keeping weight out of the anchor history:
    # the append-only promise has to hold for the *series*, and it does because
    # this looks backwards from the date asked about.
    before = [day(dt.date(2026, 8, 1), weight_kg=78.0)]
    after = [*before, day(dt.date(2026, 8, 20), weight_kg=82.0)]

    tenth_before = weight_in_force(before, dt.date(2026, 8, 10))
    tenth_after = weight_in_force(after, dt.date(2026, 8, 10))

    assert tenth_before == tenth_after
    assert tenth_after is not None
    assert tenth_after.weight_kg == 78.0


def test_a_date_before_the_first_weight_resolves_to_nothing() -> None:
    # Not a default, and not zero: watts per kilogram is then absent, because
    # a plausible number that is nobody's is worse than no number.
    series = [day(dt.date(2026, 8, 1), weight_kg=78.0)]

    assert weight_in_force(series, dt.date(2026, 7, 31)) is None
    assert weight_in_force([], dt.date(2026, 8, 1)) is None


def test_days_without_a_weight_do_not_shadow_the_one_in_force() -> None:
    series = [
        day(dt.date(2026, 8, 1), weight_kg=78.0),
        day(dt.date(2026, 8, 5), fatigue=3),
    ]

    resolved = weight_in_force(series, dt.date(2026, 8, 6))

    assert resolved is not None
    assert resolved.effective_date == dt.date(2026, 8, 1)


# --- what a day will and will not accept --------------------------------------


def test_a_day_with_nothing_on_it_is_refused() -> None:
    with pytest.raises(ValueError, match="must record something"):
        WellnessDay(local_date=dt.date(2026, 8, 14))


def test_a_day_carrying_only_a_confounder_is_a_real_day() -> None:
    # The eval's third case: the athlete reports a confounder and no numbers.
    # That is a report, not an empty write.
    recorded = day(dt.date(2026, 8, 14), confounders=(Confounder.TRAVEL,))

    assert recorded.confounders == (Confounder.TRAVEL,)


@pytest.mark.parametrize("field", sorted(BOUNDS))
def test_every_bound_refuses_just_outside_and_accepts_just_inside(field: str) -> None:
    low, high = BOUNDS[field]
    inside = {
        "hrv_ms": {"hrv_metric": HrvMetric.RMSSD, "hrv_context": HrvContext.SLEEPING}
    }

    day(dt.date(2026, 8, 14), **{field: low}, **inside.get(field, {}))
    day(dt.date(2026, 8, 14), **{field: high}, **inside.get(field, {}))
    with pytest.raises(ValueError, match=field):
        day(dt.date(2026, 8, 14), **{field: low - 1}, **inside.get(field, {}))
    with pytest.raises(ValueError, match=field):
        day(dt.date(2026, 8, 14), **{field: high + 1}, **inside.get(field, {}))


def test_spo2_is_a_fraction_not_a_percentage() -> None:
    # `.claude/rules/backend-domain-units.md` rule 1, and the one field where
    # the mistake produces a number that looks entirely reasonable.
    day(dt.date(2026, 8, 14), spo2=0.97)
    with pytest.raises(ValueError, match="spo2"):
        day(dt.date(2026, 8, 14), spo2=97)


@pytest.mark.parametrize("field", SUBJECTIVE_FIELDS)
def test_a_rating_off_its_scale_is_refused_by_name(field: str) -> None:
    scale = SUBJECTIVE_SCALES[field]

    day(dt.date(2026, 8, 14), **{field: scale.low})
    day(dt.date(2026, 8, 14), **{field: scale.high})
    with pytest.raises(ValueError, match=field):
        day(dt.date(2026, 8, 14), **{field: scale.high + 1})


def test_a_future_day_is_refused_naming_today() -> None:
    tomorrow = day(dt.date(2026, 8, 15), fatigue=2)

    with pytest.raises(ValueError, match="has not happened yet"):
        tomorrow.check_not_future(dt.date(2026, 8, 14))
    # Today itself is fine — the athlete reports their morning that morning.
    day(dt.date(2026, 8, 14), fatigue=2).check_not_future(dt.date(2026, 8, 14))


def test_an_hrv_reading_must_state_its_statistic_and_its_context() -> None:
    # AC-46's shape half: RMSSD and SDNN are not on one scale and a sleeping
    # mean and a daytime spot sample are not one distribution, so a reading
    # missing either cannot join a baseline honestly.
    with pytest.raises(ValueError, match="hrv_metric"):
        day(dt.date(2026, 8, 14), hrv_ms=52.0)
    with pytest.raises(ValueError, match="hrv_context"):
        day(dt.date(2026, 8, 14), hrv_ms=52.0, hrv_metric=HrvMetric.RMSSD)
    day(
        dt.date(2026, 8, 14),
        hrv_ms=52.0,
        hrv_metric=HrvMetric.SDNN,
        hrv_context=HrvContext.SLEEPING,
    )


def test_a_context_without_a_reading_is_a_claim_about_nothing() -> None:
    with pytest.raises(ValueError, match="hrv_ms"):
        day(dt.date(2026, 8, 14), hrv_context=HrvContext.SLEEPING, fatigue=2)


def test_a_soreness_region_outside_the_vocabulary_is_refused() -> None:
    day(dt.date(2026, 8, 14), soreness_by_region={BodyRegion.QUADS: 3})
    with pytest.raises(ValueError, match="body regions"):
        day(dt.date(2026, 8, 14), soreness_by_region={"left_eyebrow": 3})


def test_a_repeated_confounder_is_refused() -> None:
    with pytest.raises(ValueError, match="repeat"):
        day(
            dt.date(2026, 8, 14),
            confounders=(Confounder.ALCOHOL, Confounder.ALCOHOL),
        )


def test_the_note_is_bounded() -> None:
    day(dt.date(2026, 8, 14), note="x" * MAX_NOTE_CHARS)
    with pytest.raises(ValueError, match="note"):
        day(dt.date(2026, 8, 14), note="x" * (MAX_NOTE_CHARS + 1))


# --- the tables cannot silently fall behind (D4, D11) -------------------------


def test_every_writable_field_declares_an_input_tier() -> None:
    # The same shape as `test_domain_purity_contract`: a list that cannot fall
    # behind, because the graceful-degradation promise is only enforceable if
    # every field has a tier and every tier is served.
    assert set(INPUT_TIERS) == set(WRITABLE_FIELDS)


def test_nothing_is_a_required_input() -> None:
    # D11, confirmed: a required daily input turns a missed morning into a
    # failure state, which is how a capture surface stops being answered.
    assert InputTier.REQUIRED not in set(INPUT_TIERS.values())


def test_every_subjective_field_declares_a_scale() -> None:
    assert set(SUBJECTIVE_FIELDS) <= set(SUBJECTIVE_SCALES)


def test_every_scale_labels_every_point_it_admits() -> None:
    for scale in SUBJECTIVE_SCALES.values():
        assert set(scale.anchors) == set(range(scale.low, scale.high + 1)), scale.field
        assert all(label.strip() for label in scale.anchors.values()), scale.field


def test_the_scales_do_not_all_point_the_same_way() -> None:
    # The bug this table exists to prevent is a reader assuming one direction;
    # the table is only worth anything if the directions genuinely differ.
    assert SUBJECTIVE_SCALES["motivation"].polarity is Polarity.HIGHER_IS_BETTER
    assert SUBJECTIVE_SCALES["fatigue"].polarity is Polarity.HIGHER_IS_WORSE


def test_session_rpe_is_declared_here_and_matches_the_service_bounds() -> None:
    # D4: session RPE joins the scale table so the UI can show anchor words and
    # so RPE is visibly a different scale from RIR. The bounds are duplicated
    # because the domain may not import a service — this is the test that stops
    # the duplication from drifting.
    rpe = SUBJECTIVE_SCALES["rpe"]

    assert (rpe.low, rpe.high) == (int(MIN_RPE), int(MAX_RPE))
    # Intensity is not valence: a 9 is a hard session, not a bad one, and
    # forcing RPE into higher_is_worse would invent the direction this table
    # exists to stop being invented.
    assert rpe.polarity is Polarity.HIGHER_IS_NEITHER
    assert "RIR" in rpe.prompt


def test_the_objective_and_subjective_splits_cover_the_measured_fields() -> None:
    # The late-entry asymmetry turns on this split, so a field that is in
    # neither list would be silently exempt from both halves of the rule.
    scalars = set(VALUE_FIELDS) - {
        "sleep_start_local",
        "sleep_end_local",
        "hrv_metric",
        "hrv_context",
        "note",
    }
    assert set(OBJECTIVE_FIELDS) | set(SUBJECTIVE_FIELDS) == scalars


# --- gaps are reported, never synthesized -------------------------------------


def test_missing_dates_names_the_days_nobody_answered() -> None:
    gaps = missing_dates(
        [dt.date(2026, 8, 11)], start=dt.date(2026, 8, 10), end=dt.date(2026, 8, 13)
    )

    assert gaps == (dt.date(2026, 8, 10), dt.date(2026, 8, 12))


def test_the_range_is_half_open() -> None:
    gaps = missing_dates(
        [dt.date(2026, 8, 12)], start=dt.date(2026, 8, 10), end=dt.date(2026, 8, 12)
    )

    assert gaps == (dt.date(2026, 8, 10), dt.date(2026, 8, 11))


def test_missing_dates_takes_every_recorded_date_not_one_page_of_them() -> None:
    # The signature is a set of dates rather than a list of days so that the
    # difference is visible at the call site: handing it a *page* of a paged
    # read named every recorded day past the page as a day of silence.
    every = [dt.date(2026, 8, 10) + dt.timedelta(days=offset) for offset in range(3)]

    assert (
        missing_dates(every, start=dt.date(2026, 8, 10), end=dt.date(2026, 8, 13)) == ()
    )
    assert missing_dates(
        every[:1], start=dt.date(2026, 8, 10), end=dt.date(2026, 8, 13)
    ) == (dt.date(2026, 8, 11), dt.date(2026, 8, 12))

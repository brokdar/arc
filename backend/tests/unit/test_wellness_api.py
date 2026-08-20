"""The wellness HTTP surface: capture, correction, absence and the day read.

Through HTTP rather than through the service, per the testing strategy: this
is backend logic and the cheapest layer that catches a bug in it is the one the
athlete's browser actually talks to. The batch write has its own module
(`test_wellness_backfill.py`) because its invariant — whole or nothing — needs
its own negative cases.
"""

import datetime as dt
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import athlete_today
from app.core.exceptions import NotFoundError
from app.domain.actor import Actor
from app.domain.wellness import (
    BOUNDS,
    INPUT_TIERS,
    INVALIDATES_MARKERS,
    SUBJECTIVE_SCALES,
    WRITABLE_FIELDS,
    Confounder,
)
from app.persistence.wellness import WellnessDayRow
from app.persistence.wellness_prompt import WellnessPromptRow
from app.services.wellness import WellnessService

DAYS = "/api/v1/wellness/days"
INPUTS = "/api/v1/wellness/inputs"
WEIGHT = "/api/v1/wellness/weight"
PROMPT = "/api/v1/wellness/prompt"

#: Today on the athlete's clock — the same one `WellnessService.local_today`
#: reads, because that is the day these tests are about. Not `dt.date.today()`,
#: which is the *container's* clock and a third answer to the question
#: (issue #62); the DTZ rules now refuse it.
TODAY = athlete_today()
YESTERDAY = TODAY - dt.timedelta(days=1)

#: One value per writable field, all of them legal together. The field sweep
#: writes this whole object and reads it back, so a field added to the model
#: without an entry here fails `test_every_writable_field_round_trips`.
EVERY_FIELD: dict[str, Any] = {
    "sleep_duration_s": 27_000,
    "sleep_start_local": "23:15:00",
    "sleep_end_local": "06:45:00",
    "sleep_quality": 4,
    "resting_hr_bpm": 46,
    "hrv_ms": 61.4,
    "hrv_metric": "rmssd",
    "hrv_context": "sleeping",
    "respiratory_rate_brpm": 13.5,
    "spo2": 0.97,
    "wrist_temperature_delta_c": -0.2,
    "weight_kg": 78.4,
    "fatigue": 2,
    "soreness": 3,
    "stress": 2,
    "motivation": 4,
    "soreness_by_region": {"quads": 3, "lower_back": 2},
    "confounders": ["travel"],
    "note": "Long drive yesterday.",
}


async def patch(
    client: AsyncClient, date: dt.date, fields: dict[str, Any]
) -> dict[str, Any] | None:
    """PATCH one day, asserting it was accepted.

    ``None`` when the write retracted the day — see the PATCH docstring.
    """
    response = await client.patch(f"{DAYS}/{date.isoformat()}", json=fields)
    assert response.status_code == 200, response.text
    return response.json()


async def record(
    client: AsyncClient, date: dt.date = TODAY, **fields: Any
) -> dict[str, Any]:
    """PATCH one day and assert the day is still there afterwards."""
    body = await patch(client, date, fields)
    assert body is not None, "this write retracted the day"
    return body


# --- capture and read-back (AC-1, AC-2) ---------------------------------------


async def test_every_writable_field_round_trips(client: AsyncClient) -> None:
    assert set(EVERY_FIELD) == set(WRITABLE_FIELDS), (
        "a new wellness field needs a value in EVERY_FIELD, or nothing proves "
        "it can be written and read back"
    )

    body = await record(client, **EVERY_FIELD)

    for field, value in EVERY_FIELD.items():
        assert body[field] == value, field


async def test_a_write_records_athlete_reported_provenance_and_an_athlete_source(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Asserted on the stored row, not the response: the invariant is about what
    # is in the database when the agent later reads it.
    await record(client, fatigue=3)

    [row] = (await db_session.execute(select(WellnessDayRow))).scalars().all()
    assert row.provenance.value == "athlete_reported"
    assert row.source.value == "athlete"


async def test_omitting_a_field_leaves_it_alone_and_null_clears_it(
    client: AsyncClient,
) -> None:
    await record(client, resting_hr_bpm=46, fatigue=3)

    # A second write that mentions only fatigue must not wipe the heart rate:
    # a day is filled in over a morning, not in one shot.
    body = await record(client, fatigue=4)
    assert body["resting_hr_bpm"] == 46
    assert body["fatigue"] == 4

    # An explicit null is how a typo is retracted.
    body = await record(client, resting_hr_bpm=None)
    assert body["resting_hr_bpm"] is None
    assert body["fatigue"] == 4


async def test_clearing_an_hrv_reading_clears_what_described_it(
    client: AsyncClient,
) -> None:
    await record(
        client, fatigue=3, hrv_ms=61.0, hrv_metric="rmssd", hrv_context="sleeping"
    )

    body = await record(client, hrv_ms=None)

    assert body is not None
    assert body["hrv_ms"] is None
    assert body["hrv_metric"] is None
    assert body["hrv_context"] is None


async def test_clearing_the_last_value_retracts_the_day(client: AsyncClient) -> None:
    # A day that held one wrong reading would otherwise hold it forever, which
    # is the permanent lie in a baseline that clearing exists to prevent. The
    # day goes, the audit row keeps what it said, and an absent row is already
    # how this surface spells "the athlete reported nothing".
    await record(client, resting_hr_bpm=46)

    assert await patch(client, TODAY, {"resting_hr_bpm": None}) is None
    assert (await client.get(f"{DAYS}/{TODAY.isoformat()}")).status_code == 404


async def test_a_day_retracted_between_the_write_and_the_read_back_answers_null(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found by Schemathesis: a 404 on a PATCH that succeeded.

    The write commits, then the endpoint reads the day back to render it; a
    concurrent retraction in that gap left the read with nothing and answered
    404 — a status this operation does not document, on a request that worked.
    `null` is what it already says for "there is nothing here now".
    """
    await record(client, resting_hr_bpm=46)

    async def gone(self: WellnessService, local_date: dt.date) -> WellnessDayRow:
        raise NotFoundError(f"No wellness was recorded for {local_date.isoformat()}")

    monkeypatch.setattr(WellnessService, "get", gone)

    response = await client.patch(
        f"{DAYS}/{TODAY.isoformat()}", json={"resting_hr_bpm": 48}
    )

    assert response.status_code == 200, response.text
    assert response.json() is None


async def test_an_empty_write_to_a_day_that_never_existed_is_still_refused(
    client: AsyncClient,
) -> None:
    response = await client.patch(f"{DAYS}/{TODAY.isoformat()}", json={"fatigue": None})

    assert response.status_code == 422, response.text
    assert "no day here to retract" in response.json()["detail"]


async def test_a_write_with_no_field_at_all_is_refused_by_name(
    client: AsyncClient,
) -> None:
    response = await client.patch(f"{DAYS}/{TODAY.isoformat()}", json={})

    assert response.status_code == 422, response.text
    assert "must record something" in response.json()["detail"]


async def test_a_misspelled_field_is_a_422_rather_than_a_silent_no_op(
    client: AsyncClient,
) -> None:
    response = await client.patch(f"{DAYS}/{TODAY.isoformat()}", json={"fatigues": 3})

    assert response.status_code == 422, response.text


async def test_an_unknown_confounder_is_refused_with_the_vocabulary_enumerated(
    client: AsyncClient,
) -> None:
    # AC-15, the #19 lesson: an error that does not name the legal values costs
    # the caller a round trip.
    response = await client.patch(
        f"{DAYS}/{TODAY.isoformat()}", json={"confounders": ["hangover"]}
    )

    assert response.status_code == 422, response.text
    rendered = response.text
    for member in Confounder:
        assert member.value in rendered


# --- absence is not zero (AC-5, AC-6) -----------------------------------------


async def test_a_day_nobody_answered_is_a_404_not_a_day_of_nulls(
    client: AsyncClient,
) -> None:
    response = await client.get(f"{DAYS}/{TODAY.isoformat()}")

    assert response.status_code == 404, response.text


async def test_a_range_omits_the_unanswered_days_and_names_them(
    client: AsyncClient,
) -> None:
    await record(client, YESTERDAY, fatigue=3)

    response = await client.get(
        DAYS,
        params={
            "start": (TODAY - dt.timedelta(days=3)).isoformat(),
            "end": (TODAY + dt.timedelta(days=1)).isoformat(),
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["local_date"] for item in body["items"]] == [YESTERDAY.isoformat()]
    assert body["missing"] == [
        (TODAY - dt.timedelta(days=3)).isoformat(),
        (TODAY - dt.timedelta(days=2)).isoformat(),
        TODAY.isoformat(),
    ]


async def test_a_partial_day_reads_as_one_value_and_the_rest_absent(
    client: AsyncClient,
) -> None:
    body = await record(client, sleep_duration_s=25_200)

    assert body["sleep_duration_s"] == 25_200
    absent = [
        field
        for field in WRITABLE_FIELDS
        if field not in ("sleep_duration_s", "soreness_by_region", "confounders")
    ]
    assert all(body[field] is None for field in absent)
    # The two collections are empty rather than null — "no confounders" is a
    # statement, and a null list would make every consumer guard for it.
    assert body["soreness_by_region"] == {}
    assert body["confounders"] == []


# --- the range is half-open, and it pages (AC-16, AC-54) ----------------------


async def test_the_range_is_half_open_at_the_end(client: AsyncClient) -> None:
    await record(client, YESTERDAY, fatigue=3)
    await record(client, TODAY, fatigue=4)

    response = await client.get(
        DAYS, params={"start": YESTERDAY.isoformat(), "end": TODAY.isoformat()}
    )

    assert [item["local_date"] for item in response.json()["items"]] == [
        YESTERDAY.isoformat()
    ]


async def test_an_inverted_range_is_refused_rather_than_answered_empty(
    client: AsyncClient,
) -> None:
    response = await client.get(
        DAYS, params={"start": TODAY.isoformat(), "end": YESTERDAY.isoformat()}
    )

    assert response.status_code == 422, response.text


async def test_a_range_pages_and_reports_the_total(client: AsyncClient) -> None:
    for offset in range(4):
        await record(client, TODAY - dt.timedelta(days=offset), fatigue=3)

    response = await client.get(
        DAYS,
        params={
            "start": (TODAY - dt.timedelta(days=10)).isoformat(),
            "end": (TODAY + dt.timedelta(days=1)).isoformat(),
            "limit": 2,
            "offset": 1,
        },
    )

    body = response.json()
    assert body["total"] == 4
    assert len(body["items"]) == 2
    assert body["offset"] == 1


# --- backfilling one day through the ordinary path (AC-26, AC-30) -------------


async def test_any_past_date_is_writable_and_reads_back_on_that_date(
    client: AsyncClient,
) -> None:
    long_ago = TODAY - dt.timedelta(days=90)

    await record(client, long_ago, resting_hr_bpm=48)

    body = (await client.get(f"{DAYS}/{long_ago.isoformat()}")).json()
    assert body["local_date"] == long_ago.isoformat()
    assert body["resting_hr_bpm"] == 48


async def test_a_future_date_is_refused_by_name(client: AsyncClient) -> None:
    tomorrow = TODAY + dt.timedelta(days=1)

    response = await client.patch(f"{DAYS}/{tomorrow.isoformat()}", json={"fatigue": 3})

    assert response.status_code == 422, response.text
    assert "has not happened yet" in response.json()["detail"]


async def test_a_day_entered_late_is_marked_recalled_and_a_fresh_one_is_not(
    client: AsyncClient,
) -> None:
    # The flag is derived from `local_date` against `created_at`, so writing an
    # old day today *is* the late case — there is no boolean to set.
    stale = await record(client, TODAY - dt.timedelta(days=30), fatigue=3)
    fresh = await record(client, TODAY, fatigue=3)

    assert stale["subjective_recalled"] is True
    assert fresh["subjective_recalled"] is False


# --- the confounder pre-check on the wire (AC-36) -----------------------------


async def test_an_invalidating_confounder_reports_not_actionable_beside_the_values(
    client: AsyncClient,
) -> None:
    body = await record(client, resting_hr_bpm=44, confounders=["alcohol"])

    assert body["resting_hr_bpm"] == 44, "the numbers are real and stay visible"
    assert body["markers"]["actionable"] is False
    assert body["markers"]["invalidated_by"] == ["alcohol"]
    assert body["markers"]["statement"] == "recorded, not actionable: alcohol"


async def test_a_context_only_confounder_leaves_the_markers_actionable(
    client: AsyncClient,
) -> None:
    body = await record(client, resting_hr_bpm=44, confounders=["travel"])

    assert body["markers"]["actionable"] is True
    assert body["markers"]["statement"] == "recorded"


# --- weight in force (AC-12, AC-13, AC-14) ------------------------------------


async def test_the_weight_read_resolves_the_version_governing_a_date(
    client: AsyncClient,
) -> None:
    first = TODAY - dt.timedelta(days=20)
    second = TODAY - dt.timedelta(days=1)
    await record(client, first, weight_kg=78.0)
    await record(client, second, weight_kg=82.0)

    middle = (
        await client.get(
            WEIGHT, params={"on": (TODAY - dt.timedelta(days=10)).isoformat()}
        )
    ).json()
    latest = (await client.get(WEIGHT, params={"on": TODAY.isoformat()})).json()

    assert middle == {
        "weight_kg": 78.0,
        "effective_date": first.isoformat(),
        "on": (TODAY - dt.timedelta(days=10)).isoformat(),
    }
    assert latest["weight_kg"] == 82.0


async def test_a_date_before_the_first_weight_is_a_404(client: AsyncClient) -> None:
    await record(client, TODAY, weight_kg=78.0)

    response = await client.get(
        WEIGHT, params={"on": (TODAY - dt.timedelta(days=1)).isoformat()}
    )

    assert response.status_code == 404, response.text


# --- the self-describing vocabulary (AC-15, D4, D11) --------------------------


async def test_the_inputs_read_serves_every_tier_scale_and_vocabulary(
    client: AsyncClient,
) -> None:
    body = (await client.get(INPUTS)).json()

    assert {entry["field"] for entry in body["tiers"]} == set(INPUT_TIERS)
    assert {entry["field"] for entry in body["scales"]} == set(SUBJECTIVE_SCALES)
    assert {entry["value"] for entry in body["confounders"]} == {
        member.value for member in Confounder
    }
    assert body["body_regions"]


async def test_every_scale_carries_its_polarity_and_a_word_for_every_point(
    client: AsyncClient,
) -> None:
    body = (await client.get(INPUTS)).json()

    for scale in body["scales"]:
        points = {anchor["value"] for anchor in scale["anchors"]}
        assert points == set(range(scale["low"], scale["high"] + 1)), scale["field"]
        assert scale["polarity"] in {
            "higher_is_better",
            "higher_is_worse",
            "higher_is_neither",
        }


async def test_the_inputs_read_marks_which_confounders_void_a_morning(
    client: AsyncClient,
) -> None:
    body = (await client.get(INPUTS)).json()

    invalidating = {
        entry["value"] for entry in body["confounders"] if entry["invalidates_markers"]
    }
    assert invalidating == {member.value for member in INVALIDATES_MARKERS}


# --- bounds are the domain's, on every path (the #17 invariant) ---------------


@pytest.mark.parametrize("field", sorted(BOUNDS))
async def test_a_value_outside_its_bound_is_refused(
    client: AsyncClient, field: str
) -> None:
    low, _ = BOUNDS[field]

    response = await client.patch(f"{DAYS}/{TODAY.isoformat()}", json={field: low - 1})

    assert response.status_code == 422, response.text


# --- the guard ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", INPUTS),
        ("get", DAYS),
        ("get", f"{DAYS}/{TODAY.isoformat()}"),
        ("patch", f"{DAYS}/{TODAY.isoformat()}"),
        ("post", "/api/v1/wellness/backfill"),
        ("get", WEIGHT),
        ("get", PROMPT),
        ("post", PROMPT),
    ],
)
async def test_the_wellness_surface_is_behind_the_session(
    anon_client: AsyncClient, method: str, path: str
) -> None:
    response = await anon_client.request(method, path, json={})

    assert response.status_code == 401, response.text


# --- found by Schemathesis ----------------------------------------------------


async def test_clearing_a_tag_list_with_null_is_its_empty_value(
    client: AsyncClient,
) -> None:
    # `confounders: null` reached the domain as `None` and blew up in `len()`
    # as a 500. The two collections have an empty value rather than an absent
    # one — the same rule the profile's `sex` and `capabilities` follow.
    await record(client, fatigue=3, confounders=["travel"])

    body = await record(client, confounders=None, soreness_by_region=None)

    assert body["confounders"] == []
    assert body["soreness_by_region"] == {}
    assert body["fatigue"] == 3


async def test_an_empty_range_is_an_empty_page_and_an_inverted_one_is_refused(
    client: AsyncClient,
) -> None:
    # `[X, X)` is a legal range of length zero — half-open arithmetic makes it
    # one, and refusing it made a schema-valid request a 422.
    empty = await client.get(
        DAYS, params={"start": TODAY.isoformat(), "end": TODAY.isoformat()}
    )
    assert empty.status_code == 200, empty.text
    assert empty.json()["items"] == []
    assert empty.json()["missing"] == []

    inverted = await client.get(
        DAYS, params={"start": TODAY.isoformat(), "end": YESTERDAY.isoformat()}
    )
    assert inverted.status_code == 422, inverted.text


async def test_a_clock_time_carries_no_offset_and_round_trips_at_seconds(
    client: AsyncClient,
) -> None:
    # These readings are naive by design: the date is the day's and the zone is
    # the athlete's. Published as an OpenAPI `format: time` they were a value
    # the contract itself called invalid, because RFC 3339 `full-time` requires
    # an offset — so the contract says "clock time" and refuses one.
    body = await record(client, sleep_start_local="23:15")

    assert body["sleep_start_local"] == "23:15:00"

    for rejected in ("23:15:00+02:00", "23:15:00.123456", "25:00", "23:15Z"):
        response = await client.patch(
            f"{DAYS}/{TODAY.isoformat()}", json={"sleep_start_local": rejected}
        )
        assert response.status_code == 422, f"{rejected}: {response.text}"


async def test_a_paged_range_reports_the_gaps_in_the_range_not_in_the_page(
    client: AsyncClient,
) -> None:
    # The defect this pins: `missing` used to be computed from the rows on the
    # page, so a 90-day range read 50 at a time named the other 40 recorded
    # days as days the athlete said nothing on — while `total` on the same
    # object said 90. The first thing the athlete does after a migration is
    # read back what they migrated.
    span = 90
    await client.post(
        "/api/v1/wellness/backfill",
        json={
            "days": [
                {
                    "local_date": (TODAY - dt.timedelta(days=offset)).isoformat(),
                    "resting_hr_bpm": 46,
                }
                for offset in range(1, span + 1)
            ]
        },
    )

    body = (
        await client.get(
            DAYS,
            params={
                "start": (TODAY - dt.timedelta(days=span)).isoformat(),
                "end": (TODAY + dt.timedelta(days=1)).isoformat(),
                "limit": 50,
            },
        )
    ).json()

    assert body["total"] == span
    assert len(body["items"]) == 50
    # Only today, which genuinely has no row.
    assert body["missing"] == [TODAY.isoformat()]
    recorded = {item["local_date"] for item in body["items"]}
    assert not recorded & set(body["missing"]), (
        "a day on this page cannot also be a day nobody answered"
    )


async def test_a_range_longer_than_a_year_is_refused_with_the_bound_named(
    client: AsyncClient,
) -> None:
    # The answer names every unanswered date in the range, so an unbounded
    # range is an unbounded answer: `2000-01-01` to today was 9,722 date
    # strings on an empty database.
    response = await client.get(
        DAYS, params={"start": "2000-01-01", "end": TODAY.isoformat()}
    )

    assert response.status_code == 422, response.text
    assert "371" in response.json()["detail"]


# --- the trend read: baselines, gaps and the readiness projection -------------
#
# The maths itself is `test_domain_wellness_baseline.py`. What is asserted here
# is the *wire*: which keys the served objects carry, which they must not, and
# that an abstention reaches the client as a missing key rather than a null.

TREND = "/api/v1/wellness/trend"

#: Every key `readiness` may carry, and nothing else. The closed set is the
#: point: this projection counts and names, and a key called `readiness_score`
#: or `recommendation` appearing on it would be arc emitting the verdict it
#: exists not to emit — the deload week triggered by an alcohol artefact, with
#: a number attached.
READINESS_KEYS = {"as_of", "markers_outside_band", "joint_state"}

#: Names that must not appear anywhere in the readiness projection, at any
#: depth. A verdict smuggled in as a nested field is still a verdict.
FORBIDDEN_READINESS_KEYS = {"readiness_score", "recommendation", "verdict", "score"}


async def seed_days(client: AsyncClient, days: list[dict[str, Any]]) -> None:
    """Write a batch of dated days through the backfill endpoint."""
    response = await client.post("/api/v1/wellness/backfill", json={"days": days})
    assert response.status_code == 200, response.text


def day_at(offset: int, **fields: Any) -> dict[str, Any]:
    """One backfill entry ``offset`` days before today."""
    return {
        "local_date": (TODAY - dt.timedelta(days=offset)).isoformat(),
        **fields,
    }


async def read_trend(
    client: AsyncClient,
    *,
    metric: str | list[str] | None = None,
    start: dt.date | None = None,
    end: dt.date | None = None,
) -> dict[str, Any]:
    """Read the trend over the trailing 60 days unless told otherwise."""
    params: dict[str, Any] = {
        "start": (start or TODAY - dt.timedelta(days=59)).isoformat(),
        "end": (end or TODAY + dt.timedelta(days=1)).isoformat(),
    }
    if metric is not None:
        params["metric"] = metric
    response = await client.get(TREND, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def keys_anywhere(value: Any) -> set[str]:
    """Every key appearing anywhere in a nested JSON value."""
    if isinstance(value, dict):
        return set(value) | {
            name for item in value.values() for name in keys_anywhere(item)
        }
    if isinstance(value, list):
        return {name for item in value for name in keys_anywhere(item)}
    return set()


# --- AC-9: an immature baseline is an abstention, not a number with a caveat --


async def test_a_thin_hrv_series_serves_an_abstention_naming_both_counts(
    client: AsyncClient,
) -> None:
    # Eleven sleeping readings over twenty-two days: short on both bars.
    await seed_days(
        client,
        [
            day_at(offset, hrv_ms=55.0, hrv_metric="rmssd", hrv_context="sleeping")
            for offset in range(0, 22, 2)
        ][:11],
    )

    baseline = (await read_trend(client, metric="hrv_rmssd_ms"))["metrics"][
        "hrv_rmssd_ms"
    ]["baseline"]

    assert baseline["kind"] == "abstention"
    assert baseline["mature"] is False
    assert baseline["readings"] == {"have": 11, "need": 14, "statement": "11 of 14"}
    assert baseline["span_days"] == {"have": 21, "need": 28, "statement": "21 of 28"}
    # Absent from the object, not present-and-null.
    for absent in ("mean", "band", "deviation_sd"):
        assert absent not in baseline, f"an abstention must not carry {absent}"


async def test_crossing_both_bars_flips_the_same_call_to_a_baseline(
    client: AsyncClient,
) -> None:
    # Fourteen readings whose first and last are 27 days apart: a span of 28,
    # which is the inclusive boundary.
    offsets = [index * 2 for index in range(14)]
    offsets[-1] = 27
    await seed_days(
        client,
        [
            day_at(
                offset,
                hrv_ms=52.0 + (offset % 7),
                hrv_metric="rmssd",
                hrv_context="sleeping",
            )
            for offset in offsets
        ],
    )

    baseline = (await read_trend(client, metric="hrv_rmssd_ms"))["metrics"][
        "hrv_rmssd_ms"
    ]["baseline"]

    assert baseline["kind"] == "banded"
    assert baseline["mature"] is True
    assert baseline["n"] == 14
    assert baseline["span_days"] == 28
    for present in ("mean", "band", "deviation_sd"):
        assert present in baseline, f"a mature baseline must carry {present}"
    assert baseline["hrv_context"] == "sleeping"


async def test_an_empty_database_abstains_rather_than_raising(
    client: AsyncClient,
) -> None:
    baseline = (await read_trend(client, metric="hrv_rmssd_ms"))["metrics"][
        "hrv_rmssd_ms"
    ]["baseline"]

    assert baseline["kind"] == "abstention"
    assert baseline["readings"]["have"] == 0


# --- AC-39: every rolling statistic carries the n behind it -------------------


async def test_the_seven_day_mean_carries_the_n_it_was_computed_over(
    client: AsyncClient,
) -> None:
    await seed_days(
        client, [day_at(offset, resting_hr_bpm=48 + offset % 3) for offset in range(3)]
    )

    rolling = (await read_trend(client, metric="resting_hr_bpm"))["metrics"][
        "resting_hr_bpm"
    ]["rolling_mean_7d"]

    assert rolling["n"] == 3
    assert rolling["mean"] == pytest.approx((48 + 49 + 50) / 3)


async def test_a_seven_day_mean_over_one_reading_says_so(
    client: AsyncClient,
) -> None:
    await seed_days(client, [day_at(2, resting_hr_bpm=51)])

    rolling = (await read_trend(client, metric="resting_hr_bpm"))["metrics"][
        "resting_hr_bpm"
    ]["rolling_mean_7d"]

    assert rolling == {"mean": 51.0, "mean_native": 51.0, "n": 1}


async def test_the_weekly_fold_carries_its_n_too(
    client: AsyncClient, session_factory: Any
) -> None:
    # The weekly fold has no HTTP adapter — its only surface is the MCP tool
    # `get_wellness_weeks`, whose wire shape `test_mcp_wellness.py` asserts. The
    # rule is the same one, so it is pinned here against the service that
    # produces it: a mean without its `n` is arithmetic that looks identical
    # whether it came from three readings or seven.
    from app.services.wellness import WellnessService

    # Anchored to last week's Monday, not this week's: the backfill guard
    # rejects a future day, and "this week's Monday + 2" is still ahead of
    # today on a Monday or a Tuesday. A completed week is always safe to seed.
    monday = TODAY - dt.timedelta(days=TODAY.weekday() + 7)
    await seed_days(
        client,
        [
            {
                "local_date": (monday + dt.timedelta(days=index)).isoformat(),
                "resting_hr_bpm": 48 + index,
            }
            for index in range(3)
        ],
    )

    async with session_factory() as session:
        summary = await WellnessService.from_session(session).weeks(
            start=monday, end=monday + dt.timedelta(days=7)
        )

    [week] = summary.weeks
    [resting] = [mean for mean in week.metrics if mean.metric == "resting_hr_bpm"]
    assert resting.n == 3
    assert resting.mean == pytest.approx(49.0)


# --- AC-32: a gap is a gap, never a zero and never an interpolation -----------


async def test_a_date_with_no_reading_is_an_explicit_gap(
    client: AsyncClient,
) -> None:
    await seed_days(
        client,
        [day_at(4, resting_hr_bpm=50), day_at(2, resting_hr_bpm=52)],
    )

    series = (
        await read_trend(
            client,
            metric="resting_hr_bpm",
            start=TODAY - dt.timedelta(days=4),
            end=TODAY - dt.timedelta(days=1),
        )
    )["metrics"]["resting_hr_bpm"]["series"]

    assert [point["local_date"] for point in series] == [
        (TODAY - dt.timedelta(days=offset)).isoformat() for offset in (4, 3, 2)
    ]
    assert [point["value"] for point in series] == [50, None, 52]
    # The failure this forbids: a gap rendered as a value, which draws a line
    # to zero and reads as a heart that stopped.
    assert 0 not in [point["value"] for point in series]


async def test_a_gap_at_the_first_date_of_the_range(client: AsyncClient) -> None:
    await seed_days(client, [day_at(2, resting_hr_bpm=52)])

    series = (
        await read_trend(
            client,
            metric="resting_hr_bpm",
            start=TODAY - dt.timedelta(days=4),
            end=TODAY - dt.timedelta(days=1),
        )
    )["metrics"]["resting_hr_bpm"]["series"]

    assert series[0]["value"] is None
    assert series[0]["markers"] is None
    assert series[-1]["value"] == 52


async def test_a_gap_at_the_last_date_of_the_range(client: AsyncClient) -> None:
    await seed_days(client, [day_at(4, resting_hr_bpm=50)])

    series = (
        await read_trend(
            client,
            metric="resting_hr_bpm",
            start=TODAY - dt.timedelta(days=4),
            end=TODAY - dt.timedelta(days=1),
        )
    )["metrics"]["resting_hr_bpm"]["series"]

    assert series[0]["value"] == 50
    assert series[-1]["value"] is None


async def test_an_entirely_empty_range_is_an_empty_series_not_a_404(
    client: AsyncClient,
) -> None:
    body = await read_trend(
        client,
        metric="resting_hr_bpm",
        start=TODAY - dt.timedelta(days=3),
        end=TODAY,
    )

    series = body["metrics"]["resting_hr_bpm"]["series"]
    assert [point["value"] for point in series] == [None, None, None]
    assert body["metrics"]["resting_hr_bpm"]["today"] is None


async def test_a_single_day_range_returns_a_single_point(
    client: AsyncClient,
) -> None:
    await seed_days(client, [day_at(0, resting_hr_bpm=47)])

    body = await read_trend(
        client,
        metric="resting_hr_bpm",
        start=TODAY,
        end=TODAY + dt.timedelta(days=1),
    )

    series = body["metrics"]["resting_hr_bpm"]["series"]
    assert len(series) == 1
    assert series[0]["value"] == 47
    assert body["as_of"] == TODAY.isoformat()


async def test_the_read_answers_per_requested_metric_and_refuses_an_unknown_one(
    client: AsyncClient,
) -> None:
    await seed_days(client, [day_at(0, resting_hr_bpm=47, weight_kg=78.2)])

    body = await read_trend(client, metric=["resting_hr_bpm", "weight_kg"])
    assert set(body["metrics"]) == {"resting_hr_bpm", "weight_kg"}

    response = await client.get(
        TREND,
        params={
            "start": TODAY.isoformat(),
            "end": (TODAY + dt.timedelta(days=1)).isoformat(),
            "metric": "hrv",
        },
    )
    assert response.status_code == 422, response.text


# --- AC-36: a voided morning says so beside its own numbers ------------------


async def test_an_invalidated_day_reports_its_standing_beside_the_readings(
    client: AsyncClient,
) -> None:
    await seed_days(client, [day_at(1, resting_hr_bpm=43, confounders=["alcohol"])])

    series = (await read_trend(client, metric="resting_hr_bpm"))["metrics"][
        "resting_hr_bpm"
    ]["series"]
    point = next(
        item
        for item in series
        if item["local_date"] == (TODAY - dt.timedelta(days=1)).isoformat()
    )

    # The number is still here: it is real and it is part of the history. What
    # is withheld is its standing as evidence about today — on the same object,
    # because a coach that has to look elsewhere will one day not look.
    assert point["value"] == 43
    assert point["markers"]["actionable"] is False
    assert point["markers"]["invalidated_by"] == ["alcohol"]
    assert point["markers"]["statement"] == "recorded, not actionable: alcohol"


async def test_a_confounder_that_does_not_invalidate_leaves_markers_actionable(
    client: AsyncClient,
) -> None:
    await seed_days(client, [day_at(1, resting_hr_bpm=43, confounders=["travel"])])

    series = (await read_trend(client, metric="resting_hr_bpm"))["metrics"][
        "resting_hr_bpm"
    ]["series"]
    point = next(item for item in series if item["value"] == 43)

    assert point["markers"]["actionable"] is True
    assert point["markers"]["statement"] == "recorded"


async def test_multiple_invalidating_confounders_are_all_named(
    client: AsyncClient,
) -> None:
    await seed_days(
        client,
        [
            day_at(
                1,
                resting_hr_bpm=43,
                confounders=["alcohol", "short_sleep", "travel"],
            )
        ],
    )

    series = (await read_trend(client, metric="resting_hr_bpm"))["metrics"][
        "resting_hr_bpm"
    ]["series"]
    point = next(item for item in series if item["value"] == 43)

    assert point["markers"]["invalidated_by"] == ["alcohol", "short_sleep"]
    assert "travel" not in point["markers"]["statement"]


# --- AC-40: readiness counts, and the field inventory that keeps it a count ---


#: Per-marker jitter, so a settled series has a non-zero SD to deviate from.
#: Every marker moves through four values on a four-day cycle, which is a
#: baseline with a real spread rather than a flat line whose band is zero-wide.
_STEP = {
    "resting_hr_bpm": 1,
    "hrv_ms": 1.0,
    "respiratory_rate_brpm": 0.2,
    "wrist_temperature_delta_c": 0.05,
    "spo2": 0.002,
}

#: The base value each marker jitters around.
SETTLED: dict[str, float] = {
    "resting_hr_bpm": 48,
    "hrv_ms": 55.0,
    "respiratory_rate_brpm": 13.0,
    "wrist_temperature_delta_c": -0.2,
    "spo2": 0.97,
}


def _hrv(fields: dict[str, Any]) -> dict[str, Any]:
    """Attach the HRV discriminators when the day carries an HRV reading."""
    if "hrv_ms" in fields:
        fields |= {"hrv_metric": "rmssd", "hrv_context": "sleeping"}
    return fields


def settled_series(
    *,
    shifted: dict[str, float] | None = None,
    markers: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Sixty days of a settled athlete, with an optional shifted last week.

    Without ``shifted`` every day follows the same four-day jitter, so the
    trailing seven-day mean sits inside the band the whole series defines —
    which is what "nothing is outside" has to look like to be a real fixture
    rather than a flat line.
    """
    base = SETTLED if markers is None else markers
    days: list[dict[str, Any]] = []
    for offset in range(60):
        fields: dict[str, Any] = {
            name: round(value + (offset % 4) * _STEP[name], 4)
            for name, value in base.items()
        }
        if shifted is not None and offset < 7:
            fields |= shifted
        days.append(day_at(offset, **_hrv(fields)))
    return days


async def test_readiness_names_every_marker_outside_its_band_with_a_direction(
    client: AsyncClient,
) -> None:
    await seed_days(
        client,
        settled_series(
            shifted={
                "resting_hr_bpm": 60,
                "hrv_ms": 38.0,
                "respiratory_rate_brpm": 14.5,
                "wrist_temperature_delta_c": 0.5,
                "spo2": 0.99,
            }
        ),
    )

    outside = (await read_trend(client))["readiness"]["markers_outside_band"]

    assert outside["of"] == 5
    assert outside["count"] == 5
    assert outside["statement"] == "5 of 5"
    directions = {item["metric"]: item["direction"] for item in outside["markers"]}
    assert directions["resting_hr_bpm"] == "above"
    assert directions["hrv_rmssd_ms"] == "below"
    # A count and five names. No verdict travels with it.
    assert set(outside) == {"count", "of", "statement", "markers"}


async def test_readiness_field_inventory(client: AsyncClient) -> None:
    await seed_days(client, settled_series())

    projection = (await read_trend(client))["readiness"]

    assert set(projection) <= READINESS_KEYS
    assert "markers_outside_band" in projection
    # At any depth: a verdict smuggled in as a nested field is still a verdict,
    # and the whole point of this surface is that arc counts and abstains while
    # the coach decides out loud.
    assert not keys_anywhere(projection) & FORBIDDEN_READINESS_KEYS


async def test_zero_markers_outside_still_returns_the_projection(
    client: AsyncClient,
) -> None:
    await seed_days(client, settled_series())

    outside = (await read_trend(client))["readiness"]["markers_outside_band"]

    assert outside["count"] == 0
    assert outside["of"] == 5
    assert outside["markers"] == []


async def test_an_immature_marker_leaves_the_denominator(
    client: AsyncClient,
) -> None:
    days = settled_series(
        markers={name: value for name, value in SETTLED.items() if name != "spo2"},
        shifted={"resting_hr_bpm": 60, "hrv_ms": 38.0},
    )
    # SpO2 on four days only: present, and nowhere near a baseline.
    days += [day_at(offset, spo2=0.97) for offset in (61, 62, 63, 64)]
    await seed_days(client, days)

    body = await read_trend(client, start=TODAY - dt.timedelta(days=64))
    outside = body["readiness"]["markers_outside_band"]

    # Four of five markers can speak, and the denominator says four — not five,
    # which would make two outside look calmer than it is.
    assert outside["of"] == 4
    assert outside["count"] == 2
    assert body["metrics"]["spo2"]["baseline"]["kind"] == "abstention"


async def test_the_joint_state_is_absent_rather_than_guessed(
    client: AsyncClient,
) -> None:
    # Resting HR present and mature, HRV absent entirely.
    await seed_days(
        client,
        [day_at(offset, resting_hr_bpm=48 + offset % 3) for offset in range(60)],
    )

    projection = (await read_trend(client))["readiness"]

    assert "joint_state" not in projection


async def test_the_joint_state_names_the_quadrant_when_both_are_mature(
    client: AsyncClient,
) -> None:
    await seed_days(
        client,
        settled_series(shifted={"resting_hr_bpm": 60, "hrv_ms": 38.0}),
    )

    state = (await read_trend(client))["readiness"]["joint_state"]

    assert state["key"] == "hrv_low_rhr_high"
    assert state["label"] == "HRV below baseline, resting HR above baseline"


async def test_the_trend_read_needs_a_session(anon_client: AsyncClient) -> None:
    response = await anon_client.get(
        TREND,
        params={
            "start": TODAY.isoformat(),
            "end": (TODAY + dt.timedelta(days=1)).isoformat(),
        },
    )

    assert response.status_code == 401


# --- the standing prompt (AC-60) ----------------------------------------------


async def raise_todays_prompt(db_session: AsyncSession) -> None:
    """Raise today's prompt the way the scheduled sweep does."""
    await WellnessService.from_session(db_session).raise_prompt(
        TODAY, actor=Actor.system()
    )
    await db_session.commit()


async def stored_prompt(db_session: AsyncSession) -> Any:
    """The one prompt row, read fresh."""
    db_session.expire_all()
    rows = (await db_session.execute(select(WellnessPromptRow))).scalars().all()
    assert len(rows) == 1, f"expected one prompt row, found {len(rows)}"
    return rows[0]


async def test_the_standing_prompt_is_read_with_its_status_and_deadline(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await raise_todays_prompt(db_session)

    body = (await client.get(PROMPT)).json()

    assert body["local_date"] == TODAY.isoformat()
    assert body["status"] == "pending"
    # The deadline is a fact about *this* prompt, stored when it was raised, so
    # nothing downstream has to agree with a constant.
    assert body["expires_at"] is not None
    assert body["resolved_at"] is None


async def test_no_prompt_today_reads_as_absence_rather_than_an_error(
    client: AsyncClient,
) -> None:
    response = await client.get(PROMPT)

    # 200 with a null body: "nobody has been asked yet" is an answer, and a 500
    # or a 404 would make the Today view treat it as a failure.
    assert response.status_code == 200, response.text
    assert response.json() is None


async def test_answering_the_prompt_writes_the_day_and_closes_the_question(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await raise_todays_prompt(db_session)

    body = (await client.post(PROMPT, json={"fatigue": 3, "motivation": 4})).json()

    assert body["prompt"]["status"] == "answered"
    assert body["prompt"]["resolved_at"] is not None
    assert body["day"]["fatigue"] == 3
    # On the stored row, not just in the response: the coach reads the database.
    row = await stored_prompt(db_session)
    assert row.status.value == "answered"
    assert row.resolved_at is not None
    assert (await client.get(f"{DAYS}/{TODAY.isoformat()}")).json()["motivation"] == 4


async def test_a_write_that_fails_validation_leaves_the_prompt_pending(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """One transaction: the day and the prompt move together or not at all."""
    await raise_todays_prompt(db_session)

    # An HRV reading with no statistic and no context is a domain refusal.
    response = await client.post(PROMPT, json={"hrv_ms": 61.0})

    assert response.status_code == 422, response.text
    assert (await stored_prompt(db_session)).status.value == "pending"
    assert (await client.get(f"{DAYS}/{TODAY.isoformat()}")).status_code == 404


async def test_answering_an_expired_prompt_is_refused_by_name(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await raise_todays_prompt(db_session)
    await WellnessService.from_session(db_session).expire_prompts(
        actor=Actor.system(),
        now=dt.datetime.now(dt.UTC) + dt.timedelta(days=30),
    )
    await db_session.commit()

    response = await client.post(PROMPT, json={"fatigue": 3})

    assert response.status_code == 409, response.text
    assert "expired" in response.json()["detail"].lower()
    # And the day write does not land: the closed question is not a back door
    # into today, and the backfill path is where a late entry belongs.
    assert (await client.get(f"{DAYS}/{TODAY.isoformat()}")).status_code == 404
    assert (await stored_prompt(db_session)).status.value == "expired"


async def test_answering_when_nothing_was_asked_is_refused_by_name(
    client: AsyncClient,
) -> None:
    response = await client.post(PROMPT, json={"fatigue": 3})

    assert response.status_code == 404, response.text
    assert (await client.get(f"{DAYS}/{TODAY.isoformat()}")).status_code == 404

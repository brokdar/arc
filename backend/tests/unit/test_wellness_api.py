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

from app.domain.wellness import (
    BOUNDS,
    INPUT_TIERS,
    INVALIDATES_MARKERS,
    SUBJECTIVE_SCALES,
    WRITABLE_FIELDS,
    Confounder,
)
from app.persistence.wellness import WellnessDayRow

DAYS = "/api/v1/wellness/days"
INPUTS = "/api/v1/wellness/inputs"
WEIGHT = "/api/v1/wellness/weight"

TODAY = dt.date.today()
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

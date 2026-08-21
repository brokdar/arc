"""HTTP endpoints for the daily wellness series. Thin over the service.

**Why the dated resource is `/wellness/days/{date}` and not `/wellness/{date}`.**
Five facets of this collection — `inputs`, `backfill`, `weight`, `trend` and
`prompt` — would otherwise share a path shape with the id route, and
every method they do not themselves declare would fall through to it and answer
**422 about the path parameter** where 405 is the truth. Schemathesis'
``unsupported_method`` check fails on exactly that
(`.claude/rules/api-collection-facets.md`). For the same reason the batch write
is `POST /wellness/backfill` and not `/wellness/days/batch`, which would collide
with `{date}`.

This router carries no auth dependency of its own: `app.main` mounts it on the
protected `/api/v1` router.
"""

import datetime as dt
from collections import Counter
from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic.json_schema import SkipJsonSchema

from app.api.deps import ActorDep
from app.api.pagination import PageParamsDep
from app.api.schemas.wellness import (
    BackfillDayResult,
    BandedBaselineRead,
    BandRead,
    BaselineAbstentionRead,
    ConfounderRead,
    CountRead,
    InputTierRead,
    JointStateRead,
    MarkersOutsideBandRead,
    MarkerStandingRead,
    MetricTrendRead,
    OutsideMarkerRead,
    ReadinessRead,
    RollingMeanRead,
    ScaleAnchorRead,
    SlopeRead,
    SubjectiveScaleRead,
    TrendBaselineRead,
    TrendPointRead,
    WeightInForceRead,
    WellnessBackfill,
    WellnessBackfillResult,
    WellnessDayRead,
    WellnessDaysPage,
    WellnessDayWrite,
    WellnessInputsRead,
    WellnessPromptAnswer,
    WellnessPromptRead,
    WellnessTrendRead,
)
from app.core.exceptions import ErrorDetail, NotFoundError, ValidationErrorDetail
from app.domain.wellness import (
    INPUT_TIERS,
    INVALIDATES_MARKERS,
    SUBJECTIVE_SCALES,
    BodyRegion,
    Confounder,
    MarkerStanding,
    WellnessSource,
)
from app.domain.wellness_baseline import (
    Abstention,
    Baseline,
    Count,
    MetricTrend,
    Readiness,
    WellnessMetric,
)
from app.persistence.db import SessionDep
from app.persistence.wellness import WellnessDayRow
from app.persistence.wellness_prompt import WellnessPromptRow
from app.services.wellness import (
    DayInput,
    DayResult,
    WellnessService,
    WellnessTrend,
)

router = APIRouter(prefix="/wellness", tags=["wellness"])

type Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {
    404: {"model": ErrorDetail, "description": "Nothing recorded for that date"}
}
BAD_BODY: Responses = {400: {"model": ErrorDetail, "description": "Malformed body"}}
INVALID: Responses = {
    422: {
        "model": ValidationErrorDetail,
        "description": "The day violates a schema or domain rule",
    }
}
CONFLICT: Responses = {
    409: {"model": ErrorDetail, "description": "The prompt already expired"}
}


def _times(fields: dict[str, Any]) -> dict[str, Any]:
    """Parse the two clock-time strings a write may carry into `dt.time`.

    At the edge, because the domain models a wall-clock reading as a `time`
    and the contract publishes it as a string — see :data:`ClockTime` for why
    the two differ.
    """
    for name in ("sleep_start_local", "sleep_end_local"):
        value = fields.get(name)
        if isinstance(value, str):
            fields[name] = dt.time.fromisoformat(value)
    return fields


def get_service(session: SessionDep) -> WellnessService:
    """Bind the service to a request-scoped session."""
    return WellnessService.from_session(session)


ServiceDep = Annotated[WellnessService, Depends(get_service)]

RangeStart = Annotated[dt.date, Query(description="First day of the range, inclusive.")]

#: Which metrics a trend read answers for. Optional **by omission** and never
#: nullable — `?metric=null` is the four-letter string to a query parser, and a
#: contract that advertised `null` here would fail Schemathesis on a request it
#: said was legal (`.claude/rules/api-nullability.md`). Omitting it asks for
#: every metric.
TrendMetrics = Annotated[
    list[WellnessMetric] | SkipJsonSchema[None],
    Query(
        description=(
            "Metrics to answer for; repeat the parameter for several. Omit for "
            "all of them."
        )
    ),
]
RangeEnd = Annotated[
    dt.date,
    Query(
        description=(
            "First day *after* the range: it is half-open [start, end), like "
            "every range in this application."
        )
    ),
]


def _clock(value: dt.time | None) -> str | None:
    """Render a stored clock time as `HH:MM:SS`, with no offset and no micros.

    Seconds precision on the way out because that is the precision the contract
    admits on the way in (:data:`app.api.schemas.wellness.ClockTime`) — a
    round trip has to produce a value the same schema would accept.
    """
    return None if value is None else value.isoformat(timespec="seconds")


def to_read(row: WellnessDayRow, *, subjective_recalled: bool) -> WellnessDayRead:
    """Project a stored day onto its read shape.

    ``markers`` and ``subjective_recalled`` are computed here from the domain
    rather than stored, and they ride on the **same object as the readings** —
    a confounder standing the caller has to fetch separately is a confounder
    standing the caller will one day not fetch.
    """
    day = row.to_domain()
    standing = day.standing
    return WellnessDayRead(
        id=row.id,
        local_date=row.local_date,
        sleep_duration_s=day.sleep_duration_s,
        sleep_start_local=_clock(day.sleep_start_local),
        sleep_end_local=_clock(day.sleep_end_local),
        sleep_quality=day.sleep_quality,
        resting_hr_bpm=day.resting_hr_bpm,
        hrv_ms=day.hrv_ms,
        hrv_metric=day.hrv_metric,
        hrv_context=day.hrv_context,
        respiratory_rate_brpm=day.respiratory_rate_brpm,
        spo2=day.spo2,
        wrist_temperature_delta_c=day.wrist_temperature_delta_c,
        weight_kg=day.weight_kg,
        fatigue=day.fatigue,
        soreness=day.soreness,
        stress=day.stress,
        motivation=day.motivation,
        soreness_by_region=dict(day.soreness_by_region),
        confounders=list(day.confounders),
        note=day.note,
        markers=MarkerStandingRead(
            actionable=standing.actionable,
            invalidated_by=list(standing.invalidated_by),
            statement=standing.statement,
        ),
        subjective_recalled=subjective_recalled,
        provenance=day.provenance,
        source=day.source,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/inputs")
async def get_wellness_inputs() -> WellnessInputsRead:
    """Read the whole vocabulary of the wellness surface.

    The tiers, the subjective scales with their polarity and anchor
    descriptors, the confounder vocabulary with its invalidating half marked,
    and the body regions. Served so no consumer has to carry a private copy of
    what a 3 means — or discover the confounder list by submitting guesses.

    Bundled reference data, unpaged and identical on every deployment.
    """
    return WellnessInputsRead(
        tiers=[
            InputTierRead(field=name, tier=tier) for name, tier in INPUT_TIERS.items()
        ],
        scales=[
            SubjectiveScaleRead(
                field=scale.field,
                low=scale.low,
                high=scale.high,
                polarity=scale.polarity,
                prompt=scale.prompt,
                anchors=[
                    ScaleAnchorRead(value=point, label=label)
                    for point, label in sorted(scale.anchors.items())
                ],
            )
            for scale in SUBJECTIVE_SCALES.values()
        ],
        confounders=[
            ConfounderRead(
                value=member, invalidates_markers=member in INVALIDATES_MARKERS
            )
            for member in Confounder
        ],
        body_regions=list(BodyRegion),
    )


@router.post("/backfill", responses=BAD_BODY | INVALID)
async def backfill_wellness(
    service: ServiceDep, actor: ActorDep, payload: WellnessBackfill
) -> WellnessBackfillResult:
    """Record many days in **one transaction** — the migration path.

    Either the whole batch lands or none of it does: a partial migration leaves
    the athlete unable to tell which days made it, and the retry then has to
    reason about overlap. A day that breaks a rule is refused **by date and
    field**, because "validation failed" over three hundred days is
    unactionable.

    Days that already exist are updated, not replaced — a field the batch does
    not mention is left alone — and the answer says per day whether it was
    created or updated. `dry_run` reports exactly those outcomes without
    writing, which is what a migration wants before committing to itself.
    """
    results = await service.record_many(
        [
            DayInput(
                local_date=entry.local_date,
                updates=_times(
                    entry.model_dump(exclude_unset=True, exclude={"local_date"})
                ),
            )
            for entry in payload.days
        ],
        actor=actor,
        source=WellnessSource.ATHLETE,
        dry_run=payload.dry_run,
    )
    return _backfill_result(results, dry_run=payload.dry_run)


@router.get("/prompt")
async def get_wellness_prompt(service: ServiceDep) -> WellnessPromptRead | None:
    """The question standing for today: its status and its deadline.

    **`null` when nobody has been asked yet** — a fresh instance, or an hour
    before the day's prompt is raised. That is an answer and not a failure, so
    it is a 200 with a null body rather than a 404: the Today view has to be
    able to tell "not asked" from "asked and unanswered" from "answered", and
    an error status collapses the first into a broken page.

    Reading this **never raises a prompt**. A question the athlete only gets
    because they happened to open the app is exactly the intermittent capture
    the prompt exists to replace, and it would make "was the athlete asked?"
    depend on who read the page. The scheduled sweep raises it, once, at
    `WELLNESS__PROMPT_HOUR_LOCAL`.
    """
    return _prompt(await service.prompt())


@router.post("/prompt", responses=NOT_FOUND | BAD_BODY | CONFLICT | INVALID)
async def answer_wellness_prompt(
    service: ServiceDep, actor: ActorDep, payload: WellnessDayWrite
) -> WellnessPromptAnswer:
    """Answer today's standing prompt: record the day and close the question.

    The body is the day, exactly as `PATCH /wellness/days/{date}` takes it. The
    two writes are **one transaction**: a payload that breaks a domain rule
    leaves the prompt `pending`, because the athlete was asked, has not
    answered yet, and a rejected payload must not spend the day's one question.

    **404** when no prompt is standing — the day is still writable through the
    dated PATCH; what does not exist is a question to answer. **409** when the
    prompt already expired: the day closed unanswered and that is a recorded
    fact, so a late reading goes through the dated write, where it is marked as
    entered from memory rather than backdated into an answer that never came.
    """
    prompt, result = await service.answer_prompt(
        _times(payload.model_dump(exclude_unset=True)),
        actor=actor,
        # The athlete is the only writer with an HTTP session; the agent writes
        # through MCP, which supplies `agent` here.
        source=WellnessSource.ATHLETE,
    )
    day = None
    if result.day is not None:
        row = await service.get(prompt.local_date)
        day = to_read(row, subjective_recalled=service.is_recalled(row))
    return WellnessPromptAnswer(prompt=_prompt_read(prompt), day=day)


def _prompt(row: WellnessPromptRow | None) -> WellnessPromptRead | None:
    """Render the standing prompt, or the absence of one."""
    return None if row is None else _prompt_read(row)


def _prompt_read(row: WellnessPromptRow) -> WellnessPromptRead:
    """Project one prompt row onto its read shape."""
    return WellnessPromptRead(
        local_date=row.local_date,
        status=row.status,
        expires_at=row.expires_at,
        resolved_at=row.resolved_at,
        raised_at=row.created_at,
    )


@router.get("/weight", responses=NOT_FOUND)
async def get_weight_in_force(
    service: ServiceDep,
    on: Annotated[dt.date, Query(description="The date to resolve the weight for.")],
) -> WeightInForceRead:
    """The body weight governing ``on``, with the day it was recorded.

    The most recent weight on or before that date — which is why appending a
    later one never changes what an earlier date resolves to.

    A date before the first recorded weight is a **404**, and watts per
    kilogram is then absent rather than computed against a default nobody has.
    """
    resolved = await service.weight_in_force(on)
    if resolved is None:
        raise NotFoundError(f"No weight was recorded on or before {on.isoformat()}")
    return WeightInForceRead(
        weight_kg=resolved.weight_kg, effective_date=resolved.effective_date, on=on
    )


@router.get("/trend", responses=INVALID)
async def get_wellness_trend(
    service: ServiceDep,
    start: RangeStart,
    end: RangeEnd,
    metric: TrendMetrics = None,
) -> WellnessTrendRead:
    """Per metric: the dated readings, the seven-day mean and the baseline.

    What makes a stored reading interpretable. `54` is alarming for one athlete
    and a Tuesday for another, so every metric is answered against **this**
    athlete's own trailing-60-day baseline, with a normal band and today's
    distance from it in standard deviations.

    Four things this read is careful about, each of them a way the same numbers
    could be read as more than they are:

    * **An immature baseline abstains.** Under 14 readings spanning 28 days,
      `baseline` carries no `mean`, no `band` and no `deviation_sd` — it names
      both counts instead, so "not enough data" comes with its own unlock
      condition rather than a caveat somebody drops.
    * **`deviation_sd` compares the seven-day mean to the baseline**, never
      today to yesterday. A single bad night can move it by three sevenths of
      an SD at most, which is the whole point.
    * **A date with no reading is a gap**: `value` is null, never zero and
      never interpolated. A line drawn through it would be a week the athlete
      did not have.
    * **A voided morning still returns its numbers**, with `markers` on the
      same object saying they are not evidence about today.

    `readiness` counts how many markers sit outside their own band and names
    them with a direction. It is a count, not a score: there is no verdict
    here and there is not meant to be.

    The baseline window reaches back sixty days from `end` whatever range you
    ask for, so a fortnight's chart still carries a mature baseline behind it.
    """
    return to_trend_read(await service.trend(start=start, end=end, metrics=metric))


@router.get("/days", responses=INVALID)
async def list_wellness_days(
    service: ServiceDep, page: PageParamsDep, start: RangeStart, end: RangeEnd
) -> WellnessDaysPage:
    """Read the series over the half-open range ``[start, end)``, oldest first.

    Oldest first because a wellness series is read the way a chart is drawn —
    the opposite of the session log, which reads backwards from the most recent
    thing that happened.

    Days with nothing recorded are **absent from `items` and listed in
    `missing`**. They are not synthesized as null-filled days: "the athlete
    reported nothing" and "the athlete was not asked" must not render as the
    same object. `missing` is over the **whole range**, not the page — a
    recorded day on page two is not a day the athlete was silent on.

    `end` equal to `start` is a legal empty range; `end` before it is a 422, as
    is a range longer than a year and a week — the answer names every
    unanswered date in it, so an unbounded range is an unbounded answer.
    """
    resolved = await service.range(
        start=start, end=end, offset=page.offset, limit=page.limit
    )
    return WellnessDaysPage(
        items=[
            to_read(row, subjective_recalled=service.is_recalled(row))
            for row in resolved.days
        ],
        total=resolved.total,
        offset=page.offset,
        limit=page.limit,
        # From the service, over the whole range — never derived from `items`,
        # which is one page of it.
        missing=list(resolved.missing),
    )


@router.get("/days/{local_date}", responses=NOT_FOUND)
async def get_wellness_day(service: ServiceDep, local_date: dt.date) -> WellnessDayRead:
    """Read one day.

    404 when nothing was recorded that day — see `GET /wellness/days` for why
    a day of nulls would be the wrong answer.
    """
    row = await service.get(local_date)
    return to_read(row, subjective_recalled=service.is_recalled(row))


@router.patch("/days/{local_date}", responses=BAD_BODY | INVALID | CONFLICT)
async def record_wellness_day(
    service: ServiceDep,
    actor: ActorDep,
    local_date: dt.date,
    payload: WellnessDayWrite,
) -> WellnessDayRead | None:
    """Record or correct one day. Creates it if it does not exist.

    An omitted field is left unchanged; an explicit `null` **clears** it. A day
    is corrigible — the athlete who typed 6.5 h of sleep as 65 can fix it — and
    every write appends an audit row carrying what it used to say.

    **Any past or present date** is a legal target, which is what makes
    backfilling a single day need nothing added. A future date is refused by
    name: a reading for tomorrow is not a late entry, it is a typo, and storing
    it would put a value in the baseline window before the morning it claims to
    report has happened.

    The response is **`null`** when the write cleared the day's last value: the
    day was retracted, and `null` says the same thing the dated read's 404 says
    — there is nothing here now. It is a 200 rather than a 204 so the contract
    stays one status and one model, and a client can tell "retracted" from
    "failed" without inspecting a header.
    """
    result = await service.record(
        local_date,
        _times(payload.model_dump(exclude_unset=True)),
        actor=actor,
        # The athlete is the only writer with an HTTP session; the agent writes
        # through MCP, which supplies `agent` here.
        source=WellnessSource.ATHLETE,
    )
    if result.day is None:
        return None
    try:
        row = await service.get(local_date)
    except NotFoundError:
        # The write landed, and a concurrent retraction removed the day before
        # this read. `null` is what this endpoint already says for "there is
        # nothing here now", so answering it keeps one contract for one state
        # rather than a 404 on a PATCH that succeeded.
        return None
    return to_read(row, subjective_recalled=service.is_recalled(row))


def _backfill_result(
    results: Sequence[DayResult], *, dry_run: bool
) -> WellnessBackfillResult:
    """Render a batch's per-day outcomes."""
    return WellnessBackfillResult(
        dry_run=dry_run,
        outcomes=dict(Counter(day.outcome.value for day in results)),
        days=[
            BackfillDayResult(
                local_date=day.local_date,
                outcome=day.outcome.value,
                changed=dict(day.changed),
            )
            for day in results
        ],
    )


def _standing(standing: MarkerStanding) -> MarkerStandingRead:
    """Render a day's marker standing for the wire."""
    return MarkerStandingRead(
        actionable=standing.actionable,
        invalidated_by=list(standing.invalidated_by),
        statement=standing.statement,
    )


def _baseline(
    value: Baseline | Abstention,
) -> BandedBaselineRead | TrendBaselineRead | BaselineAbstentionRead:
    """Render a baseline as whichever of the three shapes it is.

    The shape is the abstention: an immature baseline becomes a model with no
    ``mean``, ``band`` or ``deviation_sd`` **field**, so those keys are absent
    from the JSON rather than null. Serving one model with optional fields
    would put a null where a number goes, and a null in a number's slot is a
    zero to the next reader.
    """
    if isinstance(value, Abstention):
        return BaselineAbstentionRead(
            metric=value.metric,
            hrv_context=value.hrv_context,
            readings=_count(value.readings),
            span_days=_count(value.span_days),
            reason=value.reason,
        )
    common: dict[str, Any] = {
        "metric": value.metric,
        "hrv_context": value.hrv_context,
        "space": value.space,
        "unit": value.unit,
        "n": value.n,
        "span_days": value.span_days,
        "mean": value.mean,
        "mean_native": value.mean_native,
        "sd": value.sd,
        "cv": value.cv,
        "trend": SlopeRead(
            per_day=value.trend.per_day, per_week=value.trend.per_week, n=value.trend.n
        ),
    }
    if value.band is None:
        return TrendBaselineRead(**common)
    return BandedBaselineRead(
        **common,
        band=BandRead(
            low=value.band.low,
            high=value.band.high,
            half_width=value.band.half_width,
            low_native=value.band.low_native,
            high_native=value.band.high_native,
        ),
        deviation_sd=value.deviation_sd,
        direction=value.direction,
    )


def _count(count: Count) -> CountRead:
    """Render a count against its bar, with the `have of need` line."""
    return CountRead(have=count.have, need=count.need, statement=str(count))


def to_trend_read(resolved: WellnessTrend) -> WellnessTrendRead:
    """Project a computed trend onto its read shape.

    Public, and called by `backend/scripts/emit_wellness_trend_fixture.py` as
    well as by the endpoint: the frontend's trend fixture is generated by
    running the real domain through *this* function, so a mock cannot describe
    a payload the API could not produce.
    """
    return WellnessTrendRead(
        start=resolved.start,
        end=resolved.end,
        as_of=resolved.as_of,
        metrics={
            name: _metric_trend(found) for name, found in resolved.metrics.items()
        },
        readiness=_readiness(resolved.readiness),
    )


def _metric_trend(found: MetricTrend) -> MetricTrendRead:
    """Render one metric's series, rolling mean and baseline(s)."""
    return MetricTrendRead(
        metric=found.metric,
        unit=found.unit,
        space=found.space,
        series=[
            TrendPointRead(
                local_date=point.local_date,
                value=point.value,
                markers=None if point.standing is None else _standing(point.standing),
            )
            for point in found.series
        ],
        today=found.today,
        rolling_mean_7d=RollingMeanRead(
            mean=found.rolling_mean_7d.mean,
            mean_native=found.rolling_mean_7d.mean_native,
            n=found.rolling_mean_7d.n,
        ),
        baseline=_baseline(found.baseline),
        by_context={
            context: _baseline(value) for context, value in found.by_context.items()
        },
    )


def _readiness(projection: Readiness) -> ReadinessRead:
    """Render the readiness projection: a count, names and a quadrant label."""
    outside = projection.markers_outside_band
    return ReadinessRead(
        as_of=projection.as_of,
        markers_outside_band=MarkersOutsideBandRead(
            count=outside.count,
            of=outside.of,
            statement=str(outside),
            markers=[
                OutsideMarkerRead(
                    metric=marker.metric,
                    direction=marker.direction,
                    deviation_sd=marker.deviation_sd,
                )
                for marker in outside.markers
            ],
        ),
        joint_state=(
            None
            if projection.joint_state is None
            else JointStateRead(
                key=projection.joint_state.key,
                label=projection.joint_state.label,
                hrv_deviation_sd=projection.joint_state.hrv_deviation_sd,
                resting_hr_deviation_sd=projection.joint_state.resting_hr_deviation_sd,
            )
        ),
    )

"""`GET /fitness` — fitness, fatigue and form. Thin over the service.

A top-level path rather than `/sessions/fitness`: it is a facet of the session
collection as a whole, and under `/sessions` it would share a shape with
`/sessions/{session_id}`, where every method it does not declare falls through
to the id route and answers 422 about uuid syntax instead of 405
(`.claude/rules/api-collection-facets.md`).

This router carries no auth dependency of its own: `app.main` mounts it on the
protected `/api/v1` router.
"""

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic.json_schema import SkipJsonSchema

from app.api.schemas.fitness import (
    DisplayOnlyValueRead,
    FitnessExplanationsRead,
    FitnessMaturityRead,
    FitnessPointRead,
    FitnessTrendRead,
    FormStandingRead,
    SeriesExplanationRead,
    SeriesMaturityRead,
)
from app.core.exceptions import InvalidRangeError, ValidationErrorDetail
from app.domain.metrics import MetricExplanation
from app.domain.pmc import (
    FitnessTrend,
    SeriesMaturity,
    atl_explanation,
    ctl_explanation,
    tsb_explanation,
)
from app.persistence.db import SessionDep
from app.services.fitness import FitnessService

router = APIRouter(prefix="/fitness", tags=["fitness"])

INVALID: dict[int | str, dict[str, Any]] = {
    422: {"model": ValidationErrorDetail, "description": "`from` is after `to`"}
}

#: Optional by omission, never nullable: `?from=null` is a four-letter string
#: to a query parser (`.claude/rules/api-nullability.md`).
From = Annotated[
    dt.date | SkipJsonSchema[None],
    Query(
        alias="from",
        description="First day to return, inclusive. Omit to start at the first session.",
    ),
]
To = Annotated[
    dt.date | SkipJsonSchema[None],
    Query(
        alias="to",
        description=(
            "First day *not* to return — the range is half-open [from, to), like "
            "every range in this application. Omit for through today; a later "
            "date is clipped to today."
        ),
    ),
]


def get_service(session: SessionDep) -> FitnessService:
    """Bind the service to a request-scoped session."""
    return FitnessService.from_session(session)


ServiceDep = Annotated[FitnessService, Depends(get_service)]


@router.get("", responses=INVALID)
async def get_fitness(
    service: ServiceDep, start: From = None, end: To = None
) -> FitnessTrendRead:
    """Fitness (CTL), fatigue (ATL) and form (TSB), one point per day.

    **Display-only.** These describe training; arc never changes a plan
    because of them, and every value is stamped `display_only: true`.

    * CTL is the 42-day EWMA of **cycling** load, ATL the 7-day EWMA of every
      discipline's load, `α = 1 − e^(−1/τ)`; form is `CTL − ATL` at
      `form_lag_days`.
    * The series starts on the first session's `local_date` and runs every day
      to today — rest days included. Before the first session there are no
      points, never a run of zeros.
    * A session neither load model could price adds nothing and marks its day
      `load_incomplete`; `n` counts the sessions that were counted.
    * Each series says from which day it has load behind it:
      `maturity.ctl.first_priced_session` (the first priced ride),
      `maturity.atl.first_priced_session` (the first priced session of any
      discipline) and `maturity.tsb.first_assessed` (both, at the form lag).
      Before that day — or on every day, when it is null — that series' values
      are no data rather than zero.
    * `maturity` says whether each average still mostly describes its starting
      seed of zero: CTL until day 97, ATL until day 17, counted from the first
      session whatever window was asked for.

    `from` after `to` is a 422; equal bounds are a legal empty window.
    """
    try:
        trend = await service.trend(start=start, end=end)
    except InvalidRangeError as exc:
        raise exc.naming("from", "to") from exc
    return to_fitness_read(trend)


def _explanation(explanation: MetricExplanation) -> SeriesExplanationRead:
    """Render one series' explanation, stamped."""
    return SeriesExplanationRead(
        formula=explanation.formula,
        inputs=dict(explanation.inputs),
        assumptions=list(explanation.assumptions),
        citation=explanation.citation,
        display_only=True,
    )


def _maturity(series: SeriesMaturity) -> SeriesMaturityRead:
    """Render one series' maturity, stamped."""
    return SeriesMaturityRead(
        display_only=True,
        mature=series.mature,
        seeded=series.seeded,
        matures_on_day=series.matures_on_day,
        matures_on=series.matures_on,
        first_priced_session=series.first_priced,
    )


def _value(value: float) -> DisplayOnlyValueRead:
    """Render one day's value, stamped."""
    return DisplayOnlyValueRead(value=value, display_only=True)


def to_fitness_read(trend: FitnessTrend) -> FitnessTrendRead:
    """Project the domain's trend onto the response.

    Public because `scripts/emit_pmc_fixture.py` projects through it, so the
    frontend's fixture is byte for byte a response this endpoint serves.
    """
    return FitnessTrendRead(
        points=[
            FitnessPointRead(
                local_date=point.local_date,
                day=point.day,
                ctl=_value(point.ctl),
                atl=_value(point.atl),
                tsb=_value(point.tsb),
                load=point.load.load,
                fitness_load=point.load.fitness_load,
                n=point.load.n,
                uncounted=point.load.uncounted,
                load_incomplete=point.load.load_incomplete,
            )
            for point in trend.points
        ],
        maturity=FitnessMaturityRead(
            first_session=trend.maturity.first_session,
            as_of=trend.maturity.as_of,
            day=trend.maturity.day,
            ctl=_maturity(trend.maturity.ctl),
            atl=_maturity(trend.maturity.atl),
            tsb=FormStandingRead(
                display_only=True,
                first_assessed=trend.maturity.tsb.first_assessed,
            ),
        ),
        form_lag_days=trend.form_lag_days,
        explanations=FitnessExplanationsRead(
            ctl=_explanation(ctl_explanation()),
            atl=_explanation(atl_explanation()),
            tsb=_explanation(tsb_explanation(trend.form_lag_days)),
        ),
    )

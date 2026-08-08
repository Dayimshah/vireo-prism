"""Cohort analysis: retention matrices, cumulative revenue, and LTV by channel.

Four endpoints, all keyed on signup cohort rather than on calendar time. The distinction
is the point: a dip in overall retention can mean every cohort got worse, or it can mean
one large cohort of low-intent signups arrived. Only a cohort view separates the two.

Triangular by construction
--------------------------
A cohort matrix is triangular, not rectangular. The cohort that signed up last month has
no month-6 cell, so that cell is ``None`` — never zero. Zero on a heatmap reads as total
churn; the cell simply has not happened yet.

``max_months`` and ``max_weeks`` bound the width of the matrix. They cap how far each
cohort is followed, which keeps the response a readable size rather than one column per
period in the dataset's whole history.
"""

# No `from __future__ import annotations` here — see the note in `app/routers/kpi.py`.
from typing import Annotated

from fastapi import APIRouter, Query

from app.db.deps import CatalogDep, SessionDep
from app.repositories.cohort import (
    DEFAULT_MAX_MONTHS,
    DEFAULT_MAX_WEEKS,
    DEFAULT_MIN_COHORT_SIZE,
)
from app.routers.base import respond
from app.schemas import cohort as schema
from app.schemas.base import DataResponse, with_rate_limit
from app.schemas.params import FilterDep, ObservationDep, WindowDep
from app.services import cohort as service

router = APIRouter(prefix="/cohort", tags=["Cohorts"], responses=with_rate_limit())

MinCohortSize = Annotated[
    int,
    Query(
        ge=1,
        le=10_000,
        description=(
            "Cohorts smaller than this are omitted. A three-user cohort retains at 0%, "
            "33%, 67% or 100%, and whichever it lands on dominates the heatmap."
        ),
    ),
]

MaxPeriods = Annotated[
    int,
    Query(
        ge=1,
        le=60,
        description="How many periods to follow each cohort for. Bounds the matrix width.",
    ),
]


@router.get(
    "/monthly-matrix",
    response_model=DataResponse[schema.MonthlyMatrixRow],
    summary="Monthly cohort retention matrix",
)
async def get_monthly_matrix(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
    observation_end: ObservationDep = None,
    max_months: MaxPeriods = DEFAULT_MAX_MONTHS,
    min_cohort_size: MinCohortSize = DEFAULT_MIN_COHORT_SIZE,
) -> DataResponse[schema.MonthlyMatrixRow]:
    """Return retention by signup month and months elapsed.

    The standard cohort heatmap. Read *down* a column to compare cohorts at the same age —
    that is the comparison that controls for observation time. Reading across a row shows
    one cohort's decay, which is also useful but tells you nothing about whether the
    product is improving.
    """
    rows = await service.get_monthly_matrix(
        session,
        catalog,
        window.date_from,
        window.date_to,
        observation_end,
        max_months,
        min_cohort_size,
        filters,
    )
    return respond(schema.MonthlyMatrixRow, rows, window=window, filters=filters)


@router.get(
    "/weekly-matrix",
    response_model=DataResponse[schema.WeeklyMatrixRow],
    summary="Weekly cohort retention matrix",
)
async def get_weekly_matrix(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
    observation_end: ObservationDep = None,
    max_weeks: MaxPeriods = DEFAULT_MAX_WEEKS,
    min_cohort_size: MinCohortSize = DEFAULT_MIN_COHORT_SIZE,
) -> DataResponse[schema.WeeklyMatrixRow]:
    """Return retention by signup week and weeks elapsed.

    The finer grain shows the first-week collapse that a monthly matrix averages away —
    most churn in a subscription product happens in days, not months. The cost is smaller
    cohorts and noisier cells, which is what ``min_cohort_size`` is for.
    """
    rows = await service.get_weekly_matrix(
        session,
        catalog,
        window.date_from,
        window.date_to,
        observation_end,
        max_weeks,
        min_cohort_size,
        filters,
    )
    return respond(schema.WeeklyMatrixRow, rows, window=window, filters=filters)


@router.get(
    "/revenue-cumulative",
    response_model=DataResponse[schema.RevenueCumulativeRow],
    summary="Cumulative revenue per cohort",
)
async def get_revenue_cumulative(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
    observation_end: ObservationDep = None,
    min_cohort_size: MinCohortSize = DEFAULT_MIN_COHORT_SIZE,
) -> DataResponse[schema.RevenueCumulativeRow]:
    """Return cumulative revenue per cohort by months since signup.

    Cumulative, so the series only rises — which makes it a poor way to spot a cohort
    going bad and a good way to see when a cohort pays back its acquisition cost. Pair it
    with ``/marketing/cac-payback`` for the crossing point.
    """
    rows = await service.get_revenue_cumulative(
        session,
        catalog,
        window.date_from,
        window.date_to,
        observation_end,
        min_cohort_size,
        filters,
    )
    return respond(schema.RevenueCumulativeRow, rows, window=window, filters=filters)


@router.get(
    "/ltv-by-channel",
    response_model=DataResponse[schema.LtvByChannelRow],
    summary="Lifetime value by acquisition channel",
)
async def get_ltv_by_channel(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
    observation_end: ObservationDep = None,
    min_cohort_size: MinCohortSize = DEFAULT_MIN_COHORT_SIZE,
) -> DataResponse[schema.LtvByChannelRow]:
    """Return realised lifetime value per acquisition channel.

    Realised, not projected: this is revenue actually collected to date, with no curve
    fitted beyond the observation window. A channel acquiring users recently will
    therefore look worse than one that has had two years to accumulate — the figure is
    honest about what happened rather than optimistic about what might.
    """
    rows = await service.get_ltv_by_channel(
        session,
        catalog,
        window.date_from,
        window.date_to,
        observation_end,
        min_cohort_size,
        filters,
    )
    return respond(schema.LtvByChannelRow, rows, window=window, filters=filters)


__all__ = ["router"]

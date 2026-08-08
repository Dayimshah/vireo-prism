"""Monetisation: ARPU, MRR movement, and two views of what converts.

Four endpoints.

MRR is a stock, not a flow
-------------------------
``/mrr-movement`` decomposes the change in monthly recurring revenue into new, expansion,
contraction, churn and reactivation. Those components sum to the net change, which is the
only reason a waterfall chart of them is readable.

Because MRR is a recurring *stock* rather than a flow, it cannot be summed across months —
twelve months of a £10 subscription is £120 of revenue but £10 of MRR. The overview tile
reports the latest month for this reason, and anything that adds MRR across periods is
wrong.
"""

# No `from __future__ import annotations` here — see the note in `app/routers/kpi.py`.
from typing import Annotated

from fastapi import APIRouter, Query

from app.db.deps import CatalogDep, SessionDep
from app.repositories.monetization import DEFAULT_MIN_COHORT_SIZE
from app.routers.base import respond
from app.schemas import monetization as schema
from app.schemas.base import DataResponse, with_rate_limit
from app.schemas.params import FilterDep, WindowDep
from app.services import monetization as service

router = APIRouter(prefix="/monetization", tags=["Monetization"], responses=with_rate_limit())

MinCohortSize = Annotated[
    int,
    Query(ge=1, le=10_000, description="Cohorts smaller than this are omitted."),
]


@router.get(
    "/arpu-trend",
    response_model=DataResponse[schema.ArpuTrendRow],
    summary="ARPU and ARPPU over time",
)
async def get_arpu_trend(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.ArpuTrendRow]:
    """Return average revenue per user and per *paying* user, by month.

    Both, because they answer different questions and move independently. ARPU divides by
    every active user, so it falls when a free-tier campaign succeeds — which is not a
    monetisation problem. ARPPU divides by payers only and isolates pricing and plan mix.
    A dashboard showing one without the other invites exactly the wrong conclusion.

    Either is ``None`` for a month with no users in its denominator: undefined, not zero.
    """
    rows = await service.get_arpu_trend(session, catalog, window.date_from, window.date_to, filters)
    return respond(schema.ArpuTrendRow, rows, window=window, filters=filters)


@router.get(
    "/mrr-movement",
    response_model=DataResponse[schema.MrrMovementRow],
    summary="MRR movement waterfall",
)
async def get_mrr_movement(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.MrrMovementRow]:
    """Return monthly MRR decomposed into its movement components.

    New, expansion, contraction, churned and reactivation MRR, which sum to the net
    change. Contraction and churn are reported as positive magnitudes with their direction
    carried by the column name, so a waterfall renders without a client having to guess at
    signs.

    ``reactivation_mrr`` is often ``None`` in a young dataset: nobody has yet cancelled
    and returned. That is an absence of the event, not a zero measurement.
    """
    rows = await service.get_mrr_movement(
        session, catalog, window.date_from, window.date_to, filters
    )
    return respond(schema.MrrMovementRow, rows, window=window, filters=filters)


@router.get(
    "/conversion-by-watch-decile",
    response_model=DataResponse[schema.WatchDecileConversionRow],
    summary="Conversion rate by watch-time decile",
)
async def get_conversion_by_watch_decile(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.WatchDecileConversionRow]:
    """Return subscription conversion by how much a user watched.

    The clearest evidence in this dataset that engagement precedes payment. Deciles are
    computed over the population in the window, so they are relative bands rather than
    fixed hour thresholds.

    Correlation, stated plainly: heavy watchers convert more, and this does not establish
    that driving watch time causes conversion. High-intent users do both.
    """
    rows = await service.get_conversion_by_watch_decile(
        session, catalog, window.date_from, window.date_to, filters
    )
    return respond(schema.WatchDecileConversionRow, rows, window=window, filters=filters)


@router.get(
    "/trial-conversion",
    response_model=DataResponse[schema.TrialConversionRow],
    summary="Trial-to-paid conversion by plan",
)
async def get_trial_conversion_by_plan(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
    min_cohort_size: MinCohortSize = DEFAULT_MIN_COHORT_SIZE,
) -> DataResponse[schema.TrialConversionRow]:
    """Return the share of trials that became paid subscriptions, per plan.

    A trial started near the end of the window may not have finished, so recent windows
    understate conversion. The plan with the highest rate is frequently the most expensive
    one — a selection effect, since users who pick it have already decided.
    """
    rows = await service.get_trial_conversion_by_plan(
        session, catalog, window.date_from, window.date_to, min_cohort_size, filters
    )
    return respond(schema.TrialConversionRow, rows, window=window, filters=filters)


__all__ = ["router"]

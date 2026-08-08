"""Marketing efficiency: attribution, LTV:CAC, and payback period.

Three endpoints, and together they are the closest this API comes to a verdict on where
acquisition spend should go.

Last-touch attribution, and what that hides
-------------------------------------------
Each user is credited to a single channel — the one recorded at signup. That is
last-touch attribution, and it systematically over-credits the channel a user happened to
arrive through and under-credits whatever made them aware in the first place. Paid search
looks excellent under last-touch partly because it captures demand created elsewhere.

Multi-touch would need an event stream of impressions this dataset does not model. Stated
rather than papered over, because a ratio of 4.2:1 reads as precise and the attribution
underneath it is not.
"""

# No `from __future__ import annotations` here — see the note in `app/routers/kpi.py`.
from typing import Annotated

from fastapi import APIRouter, Query

from app.db.deps import CatalogDep, SessionDep
from app.repositories.marketing import DEFAULT_MIN_COHORT_SIZE
from app.routers.base import respond
from app.schemas import marketing as schema
from app.schemas.base import DataResponse, with_rate_limit
from app.schemas.params import FilterDep, ObservationDep, WindowDep
from app.services import marketing as service

router = APIRouter(prefix="/marketing", tags=["Marketing"], responses=with_rate_limit())

MinCohortSize = Annotated[
    int,
    Query(ge=1, le=10_000, description="Channels with fewer users than this are omitted."),
]


@router.get(
    "/channel-attribution",
    response_model=DataResponse[schema.ChannelAttributionRow],
    summary="Users, revenue and spend by acquisition channel",
)
async def get_channel_attribution(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
    min_cohort_size: MinCohortSize = DEFAULT_MIN_COHORT_SIZE,
) -> DataResponse[schema.ChannelAttributionRow]:
    """Return per-channel acquisition volume, realised revenue and spend.

    The base table the other two endpoints derive from. Organic channels carry no spend,
    so their cost-based columns are ``None`` rather than zero — a channel with no cost is
    not a channel with infinite efficiency, and the distinction survives to the wire.
    """
    rows = await service.get_channel_attribution(
        session, catalog, window.date_from, window.date_to, min_cohort_size, filters
    )
    return respond(schema.ChannelAttributionRow, rows, window=window, filters=filters)


@router.get(
    "/ltv-to-cac",
    response_model=DataResponse[schema.LtvToCacRow],
    summary="LTV:CAC ratio by channel",
)
async def get_ltv_to_cac(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
    min_cohort_size: MinCohortSize = DEFAULT_MIN_COHORT_SIZE,
) -> DataResponse[schema.LtvToCacRow]:
    """Return realised lifetime value against acquisition cost, per channel.

    The ratio uses *realised* LTV — revenue collected to date, with no curve projected
    forward — so it is a floor rather than a forecast, and it penalises recently acquired
    cohorts that have had less time to pay. A channel improving on this measure over
    successive windows is the signal worth acting on.

    The ratio is ``None`` where spend is zero, since dividing by it is undefined rather
    than infinitely good.
    """
    rows = await service.get_ltv_to_cac(
        session, catalog, window.date_from, window.date_to, min_cohort_size, filters
    )
    return respond(schema.LtvToCacRow, rows, window=window, filters=filters)


@router.get(
    "/cac-payback",
    response_model=DataResponse[schema.CacPaybackRow],
    summary="Months to recover acquisition cost",
)
async def get_cac_payback(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
    observation_end: ObservationDep = None,
    min_cohort_size: MinCohortSize = DEFAULT_MIN_COHORT_SIZE,
) -> DataResponse[schema.CacPaybackRow]:
    """Return how many months each channel takes to recover its acquisition cost.

    ``payback_months`` is ``None`` when a channel has not paid back *within the observed
    window*, which covers two very different cases: a channel that never will, and one
    that simply has not had long enough yet. Both are honestly unknown here, and reporting
    a projected figure would let a reader mistake extrapolation for measurement.
    """
    rows = await service.get_cac_payback(
        session,
        catalog,
        window.date_from,
        window.date_to,
        observation_end,
        min_cohort_size,
        filters,
    )
    return respond(schema.CacPaybackRow, rows, window=window, filters=filters)


__all__ = ["router"]

"""Geography and devices: where the audience is, and what it watches on.

Two endpoints with deliberately different parameter shapes, because they answer different
kinds of question.

``/country-ranking`` takes only ``date_to``. It ranks countries on their *cumulative*
state as at that date — total users, lifetime revenue — so a start date would be
meaningless: you cannot accumulate a lifetime from a window.

``/device-breakdown`` takes a full window, because device mix is a property of activity
within a period and genuinely changes over time.
"""

# No `from __future__ import annotations` here — see the note in `app/routers/kpi.py`.
from typing import Annotated

from fastapi import APIRouter, Query

from app.db.deps import CatalogDep, SessionDep
from app.repositories.geo import DEFAULT_MIN_COHORT_SIZE
from app.routers.base import respond
from app.schemas import geo as schema
from app.schemas.base import DataResponse, with_rate_limit
from app.schemas.params import EndDateDep, FilterDep, WindowDep
from app.services import geo as service

router = APIRouter(prefix="/geo", tags=["Geography"], responses=with_rate_limit())


@router.get(
    "/country-ranking",
    response_model=DataResponse[schema.CountryRankingRow],
    summary="Countries ranked by engagement and revenue",
)
async def get_country_ranking(
    session: SessionDep,
    catalog: CatalogDep,
    date_to: EndDateDep,
    filters: FilterDep,
    min_cohort_size: Annotated[
        int,
        Query(ge=1, le=10_000, description="Countries with fewer users than this are omitted."),
    ] = DEFAULT_MIN_COHORT_SIZE,
) -> DataResponse[schema.CountryRankingRow]:
    """Return countries ranked by users, engagement and revenue as at a date.

    Cumulative as at ``date_to``, not windowed — see the module docstring for why there is
    no ``date_from`` here.

    Revenue is in USD with no purchasing-power adjustment, so a ranking by revenue per
    user largely reproduces a ranking of national income. The engagement columns are the
    ones that compare across markets on equal terms.
    """
    rows = await service.get_country_ranking(session, catalog, date_to, min_cohort_size, filters)
    return respond(schema.CountryRankingRow, rows, filters=filters)


@router.get(
    "/device-breakdown",
    response_model=DataResponse[schema.DeviceBreakdownRow],
    summary="Activity by device platform and form factor",
)
async def get_device_breakdown(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.DeviceBreakdownRow]:
    """Return sessions, watch time and users by device platform and form factor.

    Counted per session, so a user who watches on a phone and a television appears under
    both. The columns therefore sum to more than the distinct user count, which is correct
    and is why cross-device behaviour has its own endpoint at ``/sessions/device-switching``.
    """
    rows = await service.get_device_breakdown(
        session, catalog, window.date_from, window.date_to, filters
    )
    return respond(schema.DeviceBreakdownRow, rows, window=window, filters=filters)


__all__ = ["router"]

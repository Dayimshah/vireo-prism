"""Headline engagement metrics: DAU, WAU, MAU, stickiness, daily composition.

Six endpoints, all daily series over a window, all filterable.

Every route in this package follows the same four lines: take the parameter
dependencies, call the service, wrap the rows with
:func:`~app.routers.base.respond`. There is no logic here — validation lives in the
parameter dependencies, filtering and caching in the services layer, SQL in the
repositories. A router that starts computing something is a sign the calculation belongs
one layer down.

Reading these together
----------------------
``/stickiness`` is DAU/MAU, and it is the one number here that is a *ratio of two other
endpoints*. It is served separately rather than derived client-side because the
denominator is a 30-day rolling distinct count, which cannot be reconstructed from the
daily DAU series — summing or averaging daily actives counts a returning user once per
day. That is the single most common way this metric gets computed wrongly.
"""

# No `from __future__ import annotations` in this package, deliberately, and it is the one
# rule a new router must not break. FastAPI resolves every endpoint annotation at import
# time to decide what each parameter *is*. Under postponed evaluation `SessionDep` arrives
# as the string "SessionDep", which FastAPI cannot resolve to a dependency, so it falls
# back to treating it as a query parameter — and the app dies building its OpenAPI schema
# with `TypeAdapter[Annotated[ForwardRef('SessionDep'), Query(...)]] is not fully defined`.
# Fourth appearance of this trap: see `app/middleware.py`, `app/schemas/params.py` and
# `app/services/experiments.py`.
from fastapi import APIRouter

from app.db.deps import CatalogDep, SessionDep
from app.routers.base import respond
from app.schemas import kpi as schema
from app.schemas.base import DataResponse, with_rate_limit
from app.schemas.params import FilterDep, WindowDep
from app.services import kpi as service

router = APIRouter(prefix="/kpi", tags=["KPI"], responses=with_rate_limit())


@router.get(
    "/dau",
    response_model=DataResponse[schema.DauRow],
    summary="Daily active users",
)
async def get_dau(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.DauRow]:
    """Return daily active users, sessions and watch time.

    One row per day in the window, including days with no activity: the query builds its
    own date spine, so a gap reads as an explicit zero rather than a missing point that a
    chart would silently interpolate across.
    """
    rows = await service.get_dau(session, catalog, window.date_from, window.date_to, filters)
    return respond(schema.DauRow, rows, window=window, filters=filters)


@router.get(
    "/wau",
    response_model=DataResponse[schema.WauRow],
    summary="Weekly active users (7-day rolling)",
)
async def get_wau(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.WauRow]:
    """Return the 7-day rolling distinct active user count.

    Rolling, not calendar-week: each row counts distinct users over that day and the six
    before it. A user active on several of those days is counted once, which is why this
    cannot be derived from the DAU series.

    The first six rows of a window are therefore computed over a partial lookback. The
    query reaches behind ``date_from`` for the data it needs, so the values are correct
    rather than truncated.
    """
    rows = await service.get_wau(session, catalog, window.date_from, window.date_to, filters)
    return respond(schema.WauRow, rows, window=window, filters=filters)


@router.get(
    "/mau",
    response_model=DataResponse[schema.MauRow],
    summary="Monthly active users (30-day rolling)",
)
async def get_mau(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.MauRow]:
    """Return the 30-day rolling distinct active user count.

    As with WAU, distinct over the whole lookback rather than a sum of daily figures.
    """
    rows = await service.get_mau(session, catalog, window.date_from, window.date_to, filters)
    return respond(schema.MauRow, rows, window=window, filters=filters)


@router.get(
    "/stickiness",
    response_model=DataResponse[schema.StickinessRow],
    summary="DAU/MAU stickiness ratio",
)
async def get_stickiness(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.StickinessRow]:
    """Return DAU as a percentage of MAU, per day.

    Read as "on an average day, what share of the monthly base shows up". Served rather
    than derived because the denominator is a rolling distinct count — see the module
    docstring.

    ``stickiness_pct`` is ``None`` on a day with no monthly actives at all: a ratio with
    an empty denominator is undefined, and zero would be plotted as a measurement.
    """
    rows = await service.get_stickiness(session, catalog, window.date_from, window.date_to, filters)
    return respond(schema.StickinessRow, rows, window=window, filters=filters)


@router.get(
    "/new-vs-returning",
    response_model=DataResponse[schema.NewVsReturningRow],
    summary="Daily split of new against returning users",
)
async def get_new_vs_returning(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.NewVsReturningRow]:
    """Return each day's active users split into new and returning.

    "New" means the user's signup date is that day, so the two categories are mutually
    exclusive and sum to DAU.
    """
    rows = await service.get_new_vs_returning(
        session, catalog, window.date_from, window.date_to, filters
    )
    return respond(schema.NewVsReturningRow, rows, window=window, filters=filters)


@router.get(
    "/sessions-per-user",
    response_model=DataResponse[schema.SessionsPerUserRow],
    summary="Session frequency distribution per day",
)
async def get_sessions_per_user(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.SessionsPerUserRow]:
    """Return per-day session counts per active user, with median and p90.

    The mean is reported alongside the percentiles because session counts are
    right-skewed — a handful of heavy users pull the mean above the median, and the gap
    between the two is the interesting part rather than noise.
    """
    rows = await service.get_sessions_per_user(
        session, catalog, window.date_from, window.date_to, filters
    )
    return respond(schema.SessionsPerUserRow, rows, window=window, filters=filters)


__all__ = ["router"]

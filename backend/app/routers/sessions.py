"""Session behaviour: how long, how deep, on what, when, and across which devices.

Six endpoints describing the shape of a session rather than counting them.

Percentiles, not averages
-------------------------
Session duration is heavily right-skewed: most sessions are a few minutes of browsing,
a few are a film. The mean sits between the two and describes neither, so this API
reports p50/p75/p90/p95 and leaves the mean as one column among several rather than the
headline.
"""

# No `from __future__ import annotations` here — see the note in `app/routers/kpi.py`.
from fastapi import APIRouter

from app.db.deps import CatalogDep, SessionDep
from app.routers.base import respond
from app.schemas import sessions as schema
from app.schemas.base import DataResponse, with_rate_limit
from app.schemas.params import FilterDep, WindowDep
from app.services import sessions as service

router = APIRouter(prefix="/sessions", tags=["Sessions"], responses=with_rate_limit())


@router.get(
    "/duration-percentiles",
    response_model=DataResponse[schema.SessionDurationPercentileRow],
    summary="Session duration percentiles by dimension",
)
async def get_session_duration_percentiles(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.SessionDurationPercentileRow]:
    """Return session duration percentiles, broken down several ways at once.

    The result mixes breakdown types in one series: rows carry a ``dimension_type`` of
    ``overall``, ``platform``, ``form_factor`` and so on. Read ``dimension_type``
    explicitly and never index by row position — the ``overall`` row is not guaranteed to
    be first, and treating it as a platform would put a total on a per-platform chart.
    """
    rows = await service.get_session_duration_percentiles(
        session, catalog, window.date_from, window.date_to, filters
    )
    return respond(schema.SessionDurationPercentileRow, rows, window=window, filters=filters)


@router.get(
    "/depth",
    response_model=DataResponse[schema.SessionDepthRow],
    summary="Distribution of session depth",
)
async def get_session_depth(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.SessionDepthRow]:
    """Return how many screens deep sessions go, as a distribution across buckets.

    Bucketed rather than averaged: the interesting signal is the share of sessions that
    never get past the first screen, and a mean depth of 3 hides whether that is everyone
    viewing three screens or half bouncing immediately.
    """
    rows = await service.get_session_depth(
        session, catalog, window.date_from, window.date_to, filters
    )
    return respond(schema.SessionDepthRow, rows, window=window, filters=filters)


@router.get(
    "/events-per-session",
    response_model=DataResponse[schema.EventsPerSessionRow],
    summary="Distribution of events per session",
)
async def get_events_per_session(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.EventsPerSessionRow]:
    """Return the distribution of event counts per session.

    A rough proxy for interaction density. Read against ``/depth``: many events across
    few screens is engagement, many events across many screens can be someone unable to
    find anything.
    """
    rows = await service.get_events_per_session(
        session, catalog, window.date_from, window.date_to, filters
    )
    return respond(schema.EventsPerSessionRow, rows, window=window, filters=filters)


@router.get(
    "/entry-exit-screens",
    response_model=DataResponse[schema.EntryExitScreenRow],
    summary="Where sessions start and where they end",
)
async def get_entry_exit_screens(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.EntryExitScreenRow]:
    """Return the screens sessions begin and end on.

    A screen with a high exit share is not automatically a problem — the player is
    supposed to be where sessions end. It matters on the screens that exist to move a
    user onward.
    """
    rows = await service.get_entry_exit_screens(
        session, catalog, window.date_from, window.date_to, filters
    )
    return respond(schema.EntryExitScreenRow, rows, window=window, filters=filters)


@router.get(
    "/device-switching",
    response_model=DataResponse[schema.DeviceSwitchingRow],
    summary="Users moving between devices",
)
async def get_device_switching(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.DeviceSwitchingRow]:
    """Return how users move between devices across sessions.

    Cross-device users are usually the most valuable segment, and they are also the ones
    a per-device analysis double-counts. This is the endpoint that shows how much of the
    base that is.
    """
    rows = await service.get_device_switching(
        session, catalog, window.date_from, window.date_to, filters
    )
    return respond(schema.DeviceSwitchingRow, rows, window=window, filters=filters)


@router.get(
    "/activity-heatmap",
    response_model=DataResponse[schema.ActivityHeatmapRow],
    summary="Activity by hour of day and day of week",
)
async def get_activity_heatmap(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.ActivityHeatmapRow]:
    """Return activity by weekday and hour, for a heatmap.

    Hours are UTC, not local time. A global audience therefore smears across the axis,
    and the peaks visible here are a mix of timezones rather than an evening. Stated
    because a heatmap invites reading a daily rhythm into it that only holds per-region.
    """
    rows = await service.get_activity_heatmap(
        session, catalog, window.date_from, window.date_to, filters
    )
    return respond(schema.ActivityHeatmapRow, rows, window=window, filters=filters)


__all__ = ["router"]

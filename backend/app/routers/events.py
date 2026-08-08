"""Event-stream composition: what the telemetry is actually made of.

One endpoint. Less a business metric than a way to see the raw material every other
number is derived from — if ``START_VIDEO`` volume moves and watch hours do not, the
problem is in the pipeline rather than in the audience.
"""

# No `from __future__ import annotations` here — see the note in `app/routers/kpi.py`.
from fastapi import APIRouter

from app.db.deps import CatalogDep, SessionDep
from app.routers.base import respond
from app.schemas import events as schema
from app.schemas.base import DataResponse, with_rate_limit
from app.schemas.params import FilterDep, WindowDep
from app.services import events as service

router = APIRouter(prefix="/events", tags=["Events"], responses=with_rate_limit())


@router.get(
    "/distribution",
    response_model=DataResponse[schema.EventDistributionRow],
    summary="Event volume and screen mix by event type",
)
async def get_event_distribution(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.EventDistributionRow]:
    """Return event counts by type, with the screens each type fires on.

    ``screen_mix`` is the one JSONB column in this API — a mapping of screen name to count,
    variable in shape by event type, which a fixed set of columns could not carry.

    This is the heaviest query in the service: it scans the partitioned event table rather
    than a materialized view, which is why it runs closer to two seconds than to fifty
    milliseconds and why its cache lifetime is the longest available.
    """
    rows = await service.get_event_distribution(
        session, catalog, window.date_from, window.date_to, filters
    )
    return respond(schema.EventDistributionRow, rows, window=window, filters=filters)


__all__ = ["router"]

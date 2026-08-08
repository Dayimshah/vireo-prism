"""Funnels: discovery to watch, signup to subscribe, drop-off, timing, segments.

Five endpoints across two funnels.

Steps are counted per user, not per event
-----------------------------------------
A user who browses the catalogue four times counts once at the browse step. Counting
events instead would let one indecisive user inflate the top of the funnel and produce a
conversion rate below the true one — the most common way a funnel chart misleads.

Because of that, step counts are monotonically non-increasing by construction: reaching
step three requires having reached step two. A funnel that widens is a bug in the query,
not a finding.
"""

# No `from __future__ import annotations` here — see the note in `app/routers/kpi.py`.
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.db.deps import CatalogDep, SessionDep
from app.repositories.funnel import DEFAULT_MIN_COHORT_SIZE
from app.routers.base import respond
from app.schemas import funnel as schema
from app.schemas.base import DataResponse, with_rate_limit
from app.schemas.params import FilterDep, WindowDep
from app.services import funnel as service

router = APIRouter(prefix="/funnel", tags=["Funnel"], responses=with_rate_limit())

#: Dimensions ``/by-segment`` accepts, mirroring
#: :data:`app.repositories.funnel.FUNNEL_SEGMENTS`. Note this differs from the retention
#: set: a funnel splits by ``platform`` and ``form_factor``, where retention splits by the
#: user's signup ``device``. The two are not interchangeable and the API does not pretend
#: they are.
FunnelSegment = Literal["country", "channel", "persona", "form_factor", "platform", "premium"]

MinCohortSize = Annotated[
    int,
    Query(
        ge=1,
        le=10_000,
        description="Segments with fewer sessions than this are omitted.",
    ),
]


@router.get(
    "/discovery-to-watch",
    response_model=DataResponse[schema.DiscoveryFunnelRow],
    summary="Discovery funnel: browse to watching",
)
async def get_discovery_to_watch(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.DiscoveryFunnelRow]:
    """Return the discovery funnel from browsing to watching.

    Both conversion figures are reported per step: the share of the previous step, which
    localises where users leave, and the share of the first step, which says how much of
    the original audience is left. Charting only one of them is how a funnel gets read
    wrongly.
    """
    rows = await service.get_discovery_to_watch(
        session, catalog, window.date_from, window.date_to, filters
    )
    return respond(schema.DiscoveryFunnelRow, rows, window=window, filters=filters)


@router.get(
    "/signup-to-subscribe",
    response_model=DataResponse[schema.SubscribeFunnelRow],
    summary="Monetisation funnel: signup to paid subscription",
)
async def get_signup_to_subscribe(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.SubscribeFunnelRow]:
    """Return the funnel from signup through to a paid subscription.

    Longer-running than the discovery funnel: a user who signs up on the last day of the
    window has had no opportunity to convert, so the final steps of a recent window are
    understated. Compare windows of equal length that end well before today.
    """
    rows = await service.get_signup_to_subscribe(
        session, catalog, window.date_from, window.date_to, filters
    )
    return respond(schema.SubscribeFunnelRow, rows, window=window, filters=filters)


@router.get(
    "/step-dropoff",
    response_model=DataResponse[schema.StepDropoffRow],
    summary="Drop-off between consecutive steps",
)
async def get_step_dropoff(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.StepDropoffRow]:
    """Return where users leave, as counts and shares between consecutive steps.

    The same data as the funnel endpoints, arranged so the largest single leak is
    directly comparable across steps rather than inferred from two adjacent bar heights.
    """
    rows = await service.get_step_dropoff(
        session, catalog, window.date_from, window.date_to, filters
    )
    return respond(schema.StepDropoffRow, rows, window=window, filters=filters)


@router.get(
    "/time-between-steps",
    response_model=DataResponse[schema.TimeBetweenStepsRow],
    summary="Elapsed time between funnel steps",
)
async def get_time_between_steps(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.TimeBetweenStepsRow]:
    """Return how long users take to move between steps.

    Median and p90 rather than a mean: elapsed times have a long tail — someone signs up
    and subscribes three months later — and a mean pulled by that tail describes nobody's
    experience.

    Only users who *completed* the transition are timed, so this says how long the journey
    takes for those who make it, not how long the rest waited before giving up.
    """
    rows = await service.get_time_between_steps(
        session, catalog, window.date_from, window.date_to, filters
    )
    return respond(schema.TimeBetweenStepsRow, rows, window=window, filters=filters)


@router.get(
    "/by-segment",
    response_model=DataResponse[schema.FunnelBySegmentRow],
    summary="Funnel conversion split by one dimension",
)
async def get_funnel_by_segment(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
    segment_by: FunnelSegment = "country",
    min_cohort_size: MinCohortSize = DEFAULT_MIN_COHORT_SIZE,
) -> DataResponse[schema.FunnelBySegmentRow]:
    """Return funnel conversion split by one dimension, small segments suppressed.

    ``segment_by`` reaches SQL as a bound parameter, never interpolated, and is validated
    against an allowlist in the repository regardless of what the schema advertises.
    """
    rows = await service.get_funnel_by_segment(
        session,
        catalog,
        window.date_from,
        window.date_to,
        segment_by,
        min_cohort_size,
        filters,
    )
    return respond(schema.FunnelBySegmentRow, rows, window=window, filters=filters)


__all__ = ["router"]

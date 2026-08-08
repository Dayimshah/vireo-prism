"""Retention: three definitions of it, plus segments, persona curves and resurrection.

Six endpoints. The first three answer the same question three different ways, and which
one a reader picks changes the number materially — so they are separate endpoints rather
than a mode flag, and each one's docstring says what it counts.

Three definitions, three answers
--------------------------------
* **N-day** — active on *exactly* day N. The strictest, and the one most products quote.
* **Rolling** — active on day N *or any day after*. Always the highest of the three.
* **Unbounded** — active on day N or any *later* day, without an upper bound on the
  window. Highest again for recent cohorts, and the least comparable across cohorts.

A retention chart with no definition attached is unreadable, which is why this API will
not serve one without saying which it used.

Right-censoring
---------------
A cohort that signed up eight days ago has no day-30 retention. That cell is ``None``,
never zero. Zero would be plotted as total churn, and on a chart the difference between
"nobody came back" and "we cannot know yet" is the difference between a crisis and a
young cohort. ``observation_end`` moves the cut-off explicitly for anyone reproducing a
figure from an earlier date.
"""

# No `from __future__ import annotations` here — see the note in `app/routers/kpi.py`.
# FastAPI resolves endpoint annotations at import time; postponed evaluation turns
# `SessionDep` into an unresolvable string and the app dies building its OpenAPI schema.
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.db.deps import CatalogDep, SessionDep
from app.repositories.retention import DEFAULT_MIN_COHORT_SIZE
from app.routers.base import respond
from app.schemas import retention as schema
from app.schemas.base import DataResponse, with_rate_limit
from app.schemas.params import FilterDep, ObservationDep, WindowDep
from app.services import retention as service

router = APIRouter(prefix="/retention", tags=["Retention"], responses=with_rate_limit())

#: Dimensions ``/by-segment`` accepts, mirroring
#: :data:`app.repositories.retention.RETENTION_SEGMENTS`. Declared as a ``Literal`` so
#: OpenAPI documents the choices and Swagger renders a dropdown; the repository validates
#: independently, so the allowlist is enforced whether or not a caller reads the docs.
RetentionSegment = Literal["country", "channel", "persona", "device", "premium"]

MinCohortSize = Annotated[
    int,
    Query(
        ge=1,
        le=10_000,
        description=(
            "Cohorts smaller than this are omitted. Small cohorts produce retention "
            "percentages that swing wildly on one user and read as signal."
        ),
    ),
]


@router.get(
    "/nday",
    response_model=DataResponse[schema.RetentionNdayRow],
    summary="N-day retention (active on exactly day N)",
)
async def get_retention_nday(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
    observation_end: ObservationDep = None,
) -> DataResponse[schema.RetentionNdayRow]:
    """Return retention by day offset, counting activity on exactly that day.

    The strictest of the three definitions. A user active on day 6 and day 8 but not day
    7 is retained on days 6 and 8 only.
    """
    rows = await service.get_retention_nday(
        session, catalog, window.date_from, window.date_to, observation_end, filters
    )
    return respond(schema.RetentionNdayRow, rows, window=window, filters=filters)


@router.get(
    "/rolling",
    response_model=DataResponse[schema.RetentionRollingRow],
    summary="Rolling retention (active on day N or later)",
)
async def get_retention_rolling(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
    observation_end: ObservationDep = None,
) -> DataResponse[schema.RetentionRollingRow]:
    """Return retention counting activity on day N or any day after it.

    Always greater than or equal to the N-day figure for the same offset. Useful for
    "have we lost them for good", where a quiet week is not churn.
    """
    rows = await service.get_retention_rolling(
        session, catalog, window.date_from, window.date_to, observation_end, filters
    )
    return respond(schema.RetentionRollingRow, rows, window=window, filters=filters)


@router.get(
    "/unbounded",
    response_model=DataResponse[schema.RetentionUnboundedRow],
    summary="Unbounded retention (ever active from day N onward)",
)
async def get_retention_unbounded(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
    observation_end: ObservationDep = None,
) -> DataResponse[schema.RetentionUnboundedRow]:
    """Return retention counting any activity from day N onward, without an upper bound.

    The most generous definition, and the least comparable across cohorts: an older
    cohort has had longer to satisfy it, so a downward slope across cohorts can be
    entirely an artefact of observation time. Compare cohorts of similar age only.
    """
    rows = await service.get_retention_unbounded(
        session, catalog, window.date_from, window.date_to, observation_end, filters
    )
    return respond(schema.RetentionUnboundedRow, rows, window=window, filters=filters)


@router.get(
    "/by-segment",
    response_model=DataResponse[schema.RetentionBySegmentRow],
    summary="Retention split by one dimension",
)
async def get_retention_by_segment(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
    segment_by: RetentionSegment = "country",
    observation_end: ObservationDep = None,
    min_cohort_size: MinCohortSize = DEFAULT_MIN_COHORT_SIZE,
) -> DataResponse[schema.RetentionBySegmentRow]:
    """Return retention split by one dimension, with small cohorts suppressed.

    ``segment_by`` reaches SQL as a bound parameter, never interpolated. The accepted set
    differs from the funnel's: retention segments by signup ``device``, which a funnel
    splits by ``platform`` and ``form_factor`` instead.
    """
    rows = await service.get_retention_by_segment(
        session,
        catalog,
        window.date_from,
        window.date_to,
        segment_by,
        observation_end,
        min_cohort_size,
        filters,
    )
    return respond(schema.RetentionBySegmentRow, rows, window=window, filters=filters)


@router.get(
    "/curve-by-persona",
    response_model=DataResponse[schema.RetentionCurveByPersonaRow],
    summary="Retention curve per persona",
)
async def get_retention_curve_by_persona(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
    observation_end: ObservationDep = None,
    min_cohort_size: MinCohortSize = DEFAULT_MIN_COHORT_SIZE,
) -> DataResponse[schema.RetentionCurveByPersonaRow]:
    """Return a full retention curve for each persona.

    One row per persona and day offset, so the curves can be drawn on shared axes. This
    is the chart that shows *shape* differences — two personas can reach the same day-30
    figure by very different routes, and only one of those is worth copying.
    """
    rows = await service.get_retention_curve_by_persona(
        session,
        catalog,
        window.date_from,
        window.date_to,
        observation_end,
        min_cohort_size,
        filters,
    )
    return respond(schema.RetentionCurveByPersonaRow, rows, window=window, filters=filters)


@router.get(
    "/resurrection",
    response_model=DataResponse[schema.ResurrectionRateRow],
    summary="Users returning after a dormant spell",
)
async def get_resurrection_rate(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.ResurrectionRateRow]:
    """Return users who came back after going dormant.

    The counterweight to a retention curve, which by construction only ever falls.
    Resurrections are why a monthly active count can grow while every cohort's retention
    declines, and a dashboard showing only the curve makes that look impossible.
    """
    rows = await service.get_resurrection_rate(
        session, catalog, window.date_from, window.date_to, filters
    )
    return respond(schema.ResurrectionRateRow, rows, window=window, filters=filters)


__all__ = ["router"]

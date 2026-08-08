"""RFM segmentation of the user base.

One endpoint, and like ``/churn/risk-scorecard`` it takes **no date window**. RFM
describes the present state of the user base — recency, frequency, monetary value —
anchored to the dataset's latest activity date. A window parameter would imply a
time-slicing the metric does not support.

Deciles are computed over the filtered population
-------------------------------------------------
``?country=India`` re-ranks users *within* India rather than showing where Indian users
sit in the global ranking. So ``champions`` always means "top decile among the users you
asked about", and the segment mix shifts under filtering by design. This is the more
useful default for a filtered dashboard and the more surprising one, which is why it is
stated here as well as in :mod:`app.schemas.users`.
"""

# No `from __future__ import annotations` here — see the note in `app/routers/kpi.py`.
from fastapi import APIRouter

from app.db.deps import CatalogDep, SessionDep
from app.routers.base import respond
from app.schemas import users as schema
from app.schemas.base import DataResponse, with_rate_limit
from app.schemas.params import FilterDep
from app.services import users as service

router = APIRouter(prefix="/users", tags=["Users"], responses=with_rate_limit())


@router.get(
    "/rfm-segments",
    response_model=DataResponse[schema.RfmSegmentRow],
    summary="RFM segments with size, engagement and revenue share",
)
async def get_rfm_segments(
    session: SessionDep,
    catalog: CatalogDep,
    filters: FilterDep,
) -> DataResponse[schema.RfmSegmentRow]:
    """Return one row per RFM segment.

    Each row carries the segment's size, its average recency/frequency/monetary decile,
    and its share of users and of revenue. Reading the two shares against each other is
    the point of the endpoint: a segment holding a small share of users and a large share
    of revenue is where the business actually lives.

    ``pct_of_revenue`` is ``None`` when total revenue is zero — a share of nothing is
    undefined, and reporting ``0`` would read as a measurement.
    """
    rows = await service.get_rfm_segments(session, catalog, filters)
    return respond(schema.RfmSegmentRow, rows, filters=filters)


__all__ = ["router"]

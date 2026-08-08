"""The dashboard landing page: six headline tiles with period-over-period deltas.

One endpoint, and the only one that composes several queries into a single answer. Each
tile carries its current value, the value from the immediately preceding window of equal
length, and the delta between them — so the caller makes one request instead of twelve and
cannot accidentally compare windows of different lengths.

Each tile states its own reduction
----------------------------------
The six tiles are not reduced the same way, and ``Tile.grain`` says which applies:

* ``avg_dau`` is a **mean** of the daily figures. Summing 90 daily DAU counts a daily
  visitor 90 times; a true window-level distinct count is a different query.
* ``sessions`` and ``watch_hours`` are **totals** — event counts add.
* ``stickiness_pct`` is averaged over the days where it is *defined*, skipping days with
  no monthly actives rather than treating them as zero.
* ``mrr_usd`` and ``arpu_usd`` are the **latest month**, because MRR is a recurring stock
  and adding twelve months of it produces a number that means nothing. A window narrower
  than a month therefore yields a partial month, which is why ``revenue_month`` is
  returned alongside.

Zeros here are real
-------------------
A window outside the dataset returns tiles reading ``0`` with real deltas, not blanks:
every query behind this endpoint builds its own date spine and LEFT JOINs onto it, so an
empty window produces explicit zeros. Only genuinely undefined figures — ratios with an
empty denominator, like ``stickiness_pct`` and ``arpu_usd`` — come back ``None``.

Caching
-------
This endpoint does not cache its own result; its six inputs each cache individually. So
``X-Cache`` reads ``PARTIAL`` here in the steady state, which is the expected value rather
than a symptom of anything.
"""

# No `from __future__ import annotations` here — see the note in `app/routers/kpi.py`.
from fastapi import APIRouter

from app.db.deps import CatalogDep, SessionDep
from app.routers.base import respond_value
from app.schemas import overview as schema
from app.schemas.base import ValueResponse, with_rate_limit
from app.schemas.params import FilterDep, WindowDep
from app.services import overview as service

router = APIRouter(prefix="/overview", tags=["Overview"], responses=with_rate_limit())


@router.get(
    "",
    response_model=ValueResponse[schema.OverviewSchema],
    summary="Headline tiles with period-over-period deltas",
)
async def get_overview(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> ValueResponse[schema.OverviewSchema]:
    """Return the six headline tiles for a window.

    The comparison window is the equal-length period ending the day before
    ``date_from``, and it is returned as ``comparison_window`` so a reader can see exactly
    what the deltas are against rather than inferring it.

    ``direction`` and ``sentiment`` are separate fields on each tile because they are
    separate questions: churn moving up is ``direction=up`` and ``sentiment=negative``.
    Colouring a chart from the direction alone is how a rising bad number ends up green.
    """
    computed = await service.get_overview(
        session, catalog, window.date_from, window.date_to, filters
    )
    return respond_value(
        schema.OverviewSchema.from_overview(computed),
        rows=len(computed.tiles),
        window=window,
        filters=filters,
    )


__all__ = ["router"]

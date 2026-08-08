"""Content performance: leaderboards, completion, decay, and genre economics.

Six endpoints. The three leaderboards take ``limit``; the two rate endpoints take
``min_starts``.

Why ``min_starts`` exists
-------------------------
A completion rate over four starts is either 0%, 25%, 50%, 75% or 100%, and whichever it
lands on will top or bottom the leaderboard. Ranking on small denominators produces a
chart of sampling noise dressed as a ranking, so titles below the threshold are omitted
rather than shown with a caveat nobody reads.
"""

# No `from __future__ import annotations` here — see the note in `app/routers/kpi.py`.
from typing import Annotated

from fastapi import APIRouter, Query

from app.db.deps import CatalogDep, SessionDep
from app.repositories.content import DEFAULT_MIN_STARTS
from app.routers.base import respond
from app.schemas import content as schema
from app.schemas.base import DataResponse, with_rate_limit
from app.schemas.params import FilterDep, LimitDep, WindowDep
from app.services import content as service

router = APIRouter(prefix="/content", tags=["Content"], responses=with_rate_limit())

MinStarts = Annotated[
    int,
    Query(
        ge=1,
        le=100_000,
        description=(
            "Titles with fewer starts than this are omitted. Ranking on a small "
            "denominator produces sampling noise that looks like a ranking."
        ),
    ),
]


@router.get(
    "/top-watch-time",
    response_model=DataResponse[schema.TopWatchTimeRow],
    summary="Titles ranked by total watch time",
)
async def get_top_watch_time(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
    limit: LimitDep = None,
) -> DataResponse[schema.TopWatchTimeRow]:
    """Return the most-watched titles by total watch hours.

    Total watch time, not starts: a short title with many starts and a long one with few
    can trade places depending on which you rank by, and watch time is the one that
    tracks delivery cost and licensing value.
    """
    rows = await service.get_top_watch_time(
        session, catalog, window.date_from, window.date_to, limit, filters
    )
    return respond(schema.TopWatchTimeRow, rows, window=window, filters=filters)


@router.get(
    "/completion-rate",
    response_model=DataResponse[schema.CompletionRateRow],
    summary="Completion rate per title",
)
async def get_completion_rate(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
    limit: LimitDep = None,
    min_starts: MinStarts = DEFAULT_MIN_STARTS,
) -> DataResponse[schema.CompletionRateRow]:
    """Return the share of starts that reached completion, per title.

    Completion is defined against each title's own runtime, so a 22-minute episode and a
    two-hour film are comparable. Read alongside ``/top-watch-time``: a title can lead on
    watch hours and still be abandoned by most of the people who start it.
    """
    rows = await service.get_completion_rate(
        session, catalog, window.date_from, window.date_to, limit, min_starts, filters
    )
    return respond(schema.CompletionRateRow, rows, window=window, filters=filters)


@router.get(
    "/trailer-to-start",
    response_model=DataResponse[schema.TrailerToStartRow],
    summary="Trailer-to-start conversion per title",
)
async def get_trailer_to_start_cvr(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
    limit: LimitDep = None,
    min_starts: MinStarts = DEFAULT_MIN_STARTS,
) -> DataResponse[schema.TrailerToStartRow]:
    """Return how often watching a trailer leads to starting the title.

    Measures the promise the trailer makes rather than the title itself. A low rate with
    high completion among those who do start suggests the trailer is selling the wrong
    thing — which is a marketing fix, not a content one.
    """
    rows = await service.get_trailer_to_start_cvr(
        session, catalog, window.date_from, window.date_to, limit, min_starts, filters
    )
    return respond(schema.TrailerToStartRow, rows, window=window, filters=filters)


@router.get(
    "/shelf-life-decay",
    response_model=DataResponse[schema.ShelfLifeDecayRow],
    summary="How watch time decays after release",
)
async def get_shelf_life_decay(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.ShelfLifeDecayRow]:
    """Return watch time by weeks since release.

    Shows how fast a title's audience falls away, which is what separates a catalogue
    asset from a launch spike. Titles released near the end of the window contribute only
    their early weeks, so the tail is built from older releases — a genuine survivorship
    effect rather than a decay curve flattening out.
    """
    rows = await service.get_shelf_life_decay(
        session, catalog, window.date_from, window.date_to, filters
    )
    return respond(schema.ShelfLifeDecayRow, rows, window=window, filters=filters)


@router.get(
    "/genre-performance",
    response_model=DataResponse[schema.GenrePerformanceRow],
    summary="Genre performance matrix",
)
async def get_genre_performance_matrix(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.GenrePerformanceRow]:
    """Return per-genre watch time, completion and audience size together.

    Assembled as one matrix so genres can be compared on several axes at once: the genre
    with the most watch time is rarely the one with the best completion, and a catalogue
    decision needs both.
    """
    rows = await service.get_genre_performance_matrix(
        session, catalog, window.date_from, window.date_to, filters
    )
    return respond(schema.GenrePerformanceRow, rows, window=window, filters=filters)


@router.get(
    "/genre-affinity",
    response_model=DataResponse[schema.GenreAffinityRow],
    summary="Genre affinity by persona",
)
async def get_genre_affinity_by_persona(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.GenreAffinityRow]:
    """Return which genres each persona over- and under-indexes on.

    Affinity is relative to the base rate, not an absolute share. Every persona watches a
    lot of the most popular genre; the useful signal is where a persona departs from the
    average, which is what a recommendation or a merchandising slot can act on.
    """
    rows = await service.get_genre_affinity_by_persona(
        session, catalog, window.date_from, window.date_to, filters
    )
    return respond(schema.GenreAffinityRow, rows, window=window, filters=filters)


__all__ = ["router"]

"""Response models for the six content and catalogue queries.

Leaderboards, completion rates, trailer conversion, shelf-life decay and two genre
matrices. Semantics live in :mod:`app.repositories.content`.

How nullability was decided, here and in every sibling module
-------------------------------------------------------------
Field types were read off live query results rather than transcribed from the ``.sql``
files, and nullability follows one rule with a stated bias.

Observed nulls make a field optional — that is evidence. But a scan proves a column
*is* nullable and can never prove one is not: a ratio that happens to have a non-empty
denominator across this dataset looks required and would reject the first request that
narrowed it to nothing. So ratios, averages and indices whose denominator can be empty
are declared optional even where none was observed, while counts, sums, identifiers and
labels stay required — every one of these queries builds its own spine and LEFT JOINs
onto it, so an absent group returns an explicit zero rather than a null.

The bias is deliberate and cheap in one direction only. An over-strict field is a 500 in
front of a reader on some filter nobody tried; an over-loose one is a ``| null`` in the
OpenAPI schema and a null check a client would want anyway. Phase 12 asserts each
model's fields against the columns its query actually returns, which is where the
remaining slack gets tightened with evidence rather than by guessing.
"""

from __future__ import annotations

from pydantic import Field

from app.schemas.base import Number, RowModel


class TopWatchTimeRow(RowModel):
    """One title on the watch-time leaderboard.

    Attributes:
        content_id: Surrogate key for the title.
        title: Display name.
        genre: Primary genre.
        content_type: ``movie``, ``series`` or another ``core.content_type`` label.
        release_year: Year of release.
        language: Catalogue language.
        is_original: Whether Vireo commissioned it.
        runtime_minutes: Nominal runtime; for a series, per episode.
        watch_hours: Total playback hours in the window.
        starts: Playback starts.
        completions: Playbacks reaching the completion threshold.
        detail_views: Detail-page views.
        watchlist_adds: Watchlist additions.
        unique_viewers: Distinct users who started it.
        watch_hours_per_viewer: ``watch_hours / unique_viewers``, or ``None`` if
            nobody watched.
        completion_rate_pct: ``completions / starts`` as a percentage, or ``None``
            with no starts.
        watch_rank: Position on this leaderboard, ``1`` being the most watched.
    """

    content_id: int
    title: str
    genre: str
    content_type: str
    release_year: int
    language: str
    is_original: bool
    runtime_minutes: int
    watch_hours: Number
    starts: int
    completions: int
    detail_views: int
    watchlist_adds: int
    unique_viewers: int
    watch_hours_per_viewer: Number | None = None
    completion_rate_pct: Number | None = None
    watch_rank: int


class CompletionRateRow(RowModel):
    """One title's completion behaviour.

    Attributes:
        content_id: Surrogate key.
        title: Display name.
        genre: Primary genre.
        content_type: Content type label.
        runtime_minutes: Nominal runtime.
        is_original: Whether Vireo commissioned it.
        popularity_score: Catalogue popularity, independent of this window's watching.
        starts: Playback starts.
        completions: Playbacks completed.
        abandons: Playbacks abandoned partway.
        viewer_days: Distinct viewer-days, so one user watching on three days counts
            three times.
        completion_rate_pct: ``completions / starts`` as a percentage.
        avg_abandon_pct: Mean progress at which abandoned playbacks stopped.
        watch_hours: Total playback hours.
        avg_rating: Mean user rating, or ``None`` where nobody rated it.
    """

    content_id: int
    title: str
    genre: str
    content_type: str
    runtime_minutes: int
    is_original: bool
    popularity_score: Number
    starts: int
    completions: int
    abandons: int
    viewer_days: int
    completion_rate_pct: Number | None = None
    avg_abandon_pct: Number | None = None
    watch_hours: Number
    avg_rating: Number | None = None


class TrailerToStartRow(RowModel):
    """Whether watching a trailer preceded starting a title.

    Attributes:
        title: Display name.
        genre: Primary genre.
        content_type: Content type label.
        is_original: Whether Vireo commissioned it.
        detail_views: Detail-page views.
        trailer_views: Trailer plays.
        starts: Playback starts.
        trailer_view_rate_pct: Share of detail views that played the trailer.
        trailer_to_start_pct: Share of trailer viewers who then started the title.
        start_without_trailer_pct: Share of starts with no preceding trailer.
        lift_vs_no_trailer: Ratio of the two start rates. Above ``1`` means the
            trailer is associated with more starts — association, not causation; this
            is observational data, and the experiments endpoints are where a causal
            claim can be made.
        completion_rate_pct: Completion rate among those starts.
    """

    title: str
    genre: str
    content_type: str
    is_original: bool
    detail_views: int
    trailer_views: int
    starts: int
    trailer_view_rate_pct: Number | None = None
    trailer_to_start_pct: Number | None = None
    start_without_trailer_pct: Number | None = None
    lift_vs_no_trailer: Number | None = Field(
        default=None,
        description="Association, not causation. Null when the comparison base is empty.",
    )
    completion_rate_pct: Number | None = None


class ShelfLifeDecayRow(RowModel):
    """How watching decays in the weeks after a title is added.

    Attributes:
        week_since_added: Weeks since the title joined the catalogue; ``0`` is its
            first week.
        titles: Titles contributing to this week.
        watch_hours: Total playback hours at this age.
        starts: Playback starts at this age.
        pct_of_week0_mean: Mean per-title watching as a share of week 0.
        pct_of_week0_median: The same on the median, which is the more robust of the
            two when a single hit dominates the mean.
        pct_of_week0_originals: The mean share, originals only.
        pct_of_week0_licensed: The mean share, licensed titles only.
    """

    week_since_added: int
    titles: int
    watch_hours: Number
    starts: int
    pct_of_week0_mean: Number | None = None
    pct_of_week0_median: Number | None = None
    pct_of_week0_originals: Number | None = None
    pct_of_week0_licensed: Number | None = None


class GenrePerformanceRow(RowModel):
    """One genre's catalogue economics.

    Attributes:
        genre: Genre name.
        titles: Titles in the genre.
        originals: How many are originals.
        series_count: How many are series rather than films.
        avg_runtime_minutes: Mean runtime.
        avg_popularity: Mean catalogue popularity score.
        unique_viewers: Distinct users who watched anything in the genre.
        starts: Playback starts.
        completions: Playbacks completed.
        watch_hours: Total playback hours.
        completion_rate_pct: ``completions / starts`` as a percentage.
        view_to_start_pct: Starts as a share of detail views. Can exceed 100: a user
            may start a title repeatedly without revisiting its detail page, so this
            is not a funnel conversion and must not be plotted as one.
        avg_rating: Mean rating across the genre.
        catalogue_share_pct: Share of all titles.
        watch_share_pct: Share of all watch hours.
        watch_per_title_index: ``watch_share_pct / catalogue_share_pct``. Above ``1``
            means the genre earns more attention than its shelf space.
        watch_hours_per_title: Mean hours per title in the genre.
    """

    genre: str
    titles: int
    originals: int
    series_count: int
    avg_runtime_minutes: Number
    avg_popularity: Number
    unique_viewers: int
    starts: int
    completions: int
    watch_hours: Number
    completion_rate_pct: Number | None = None
    view_to_start_pct: Number | None = Field(
        default=None,
        description="Can exceed 100 — repeat starts without a new detail view. Not a funnel step.",
    )
    avg_rating: Number | None = None
    catalogue_share_pct: Number
    watch_share_pct: Number
    watch_per_title_index: Number | None = None
    watch_hours_per_title: Number | None = None


class GenreAffinityRow(RowModel):
    """One persona's affinity for one genre.

    Attributes:
        persona: Persona name.
        genre: Genre name.
        watch_seconds: Total playback seconds for this pairing.
        watch_hours: The same in hours.
        playback_events: Playback events contributing.
        pct_of_persona_watch: Share of this persona's watching that went to the genre.
        pct_of_all_watch: Share of all watching that went to the genre.
        affinity_lift: The ratio of the two. Above ``1`` means the persona
            over-indexes on the genre relative to the base rate; this is the column
            that carries the signal, since a large ``pct_of_persona_watch`` may only
            reflect a genre everyone watches.
        rank_within_persona: The genre's rank for this persona, ``1`` being highest.
    """

    persona: str
    genre: str
    watch_seconds: int
    watch_hours: Number
    playback_events: int
    pct_of_persona_watch: Number | None = None
    pct_of_all_watch: Number | None = None
    affinity_lift: Number | None = Field(
        default=None,
        description="Over-indexing versus the base rate. The column carrying the signal.",
    )
    rank_within_persona: int


__all__ = [
    "CompletionRateRow",
    "GenreAffinityRow",
    "GenrePerformanceRow",
    "ShelfLifeDecayRow",
    "TopWatchTimeRow",
    "TrailerToStartRow",
]

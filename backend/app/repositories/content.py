"""Catalogue performance: leaderboards, completion, decay and genre economics.

The six queries behind the Content page. Four describe individual titles, two
describe genres, and one — :func:`get_genre_affinity_by_persona` — is a
demonstration that the dataset's planted structure is recoverable from behaviour
alone.

Ratios are computed as ``SUM(numerator) / SUM(denominator)`` over the whole
window, never as an average of per-day rates. That is not a stylistic choice:
averaging stored ratios invites Simpson's paradox. A title with one start and one
completion on Monday and two hundred starts with forty completions on Tuesday
averages to 60% while its true rate is 20%. Every rate returned here is a true
window rate.

Small denominators are suppressed by ``min_starts``. A title with four starts
reports 0% or 100% completion, and either number is noise presented as a finding.
The suppression threshold is a parameter rather than a constant in SQL so a caller
looking at a niche catalogue can lower it deliberately.

Filter scope differs across these queries and is worth noting: most take only the
catalogue filters (genre, content type, language), while
:func:`get_trailer_to_start_cvr` and :func:`get_genre_affinity_by_persona` take
user-scope filters as well, because both relate a viewer property to a title
property. Passing a full :class:`~app.repositories.base.FilterSet` is always
safe — each query receives only the parameters it declares.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from app.repositories.base import FilterSet, fetch_all

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

#: Default leaderboard length. Matches the API's default page size, so an
#: unparameterised request returns one screen of results.
DEFAULT_LIMIT: Final[int] = 50

#: Default minimum starts (or trailer views) for a title to be ranked. Chosen so a
#: single viewer's behaviour cannot move a percentage by more than a few points.
DEFAULT_MIN_STARTS: Final[int] = 25


async def get_top_watch_time(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    limit: int = DEFAULT_LIMIT,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return the content leaderboard ranked by total watch time.

    Watch time rather than start count, because starts reward promotion while
    watch time rewards what people actually sat through: a heavily merchandised
    title can top a starts leaderboard while contributing little viewing.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        limit: Maximum titles to return.
        filters: Optional catalogue filters. Unfiltered when omitted.

    Returns:
        One row per title, ordered by watch time descending, with keys
        ``content_id``, ``title``, ``genre``, ``content_type``, ``release_year``,
        ``language``, ``is_original``, ``runtime_minutes``, ``watch_hours``,
        ``starts``, ``completions``, ``detail_views``, ``watchlist_adds``,
        ``unique_viewers``, ``watch_hours_per_viewer``, ``completion_rate_pct`` and
        ``watch_rank``.
    """
    return await fetch_all(
        session,
        "content/content_top_watch_time",
        {
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_completion_rate(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    limit: int = DEFAULT_LIMIT,
    min_starts: int = DEFAULT_MIN_STARTS,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return completion rate per title, with the average abandonment point.

    ``avg_abandon_pct`` is weighted by abandonment count, so a title with one early
    quitter and fifty late ones reports the late figure rather than the midpoint of
    the two.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        limit: Maximum titles to return.
        min_starts: Titles with fewer starts than this are excluded.
        filters: Optional catalogue filters. Unfiltered when omitted.

    Returns:
        One row per title, ordered by completion rate descending, with keys
        ``content_id``, ``title``, ``genre``, ``content_type``,
        ``runtime_minutes``, ``is_original``, ``popularity_score``, ``starts``,
        ``completions``, ``abandons``, ``viewer_days``, ``completion_rate_pct``,
        ``avg_abandon_pct``, ``watch_hours`` and ``avg_rating``.

        ``viewer_days`` is a sum of daily unique viewers, so a viewer returning on
        three days counts three times. It is not a distinct viewer count.
    """
    return await fetch_all(
        session,
        "content/content_completion_rate",
        {
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit,
            "min_starts": min_starts,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_trailer_to_start_cvr(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    limit: int = DEFAULT_LIMIT,
    min_starts: int = DEFAULT_MIN_STARTS,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return trailer-to-start conversion per title, against the no-trailer rate.

    Measures whether a trailer sells a title or talks people out of it. Both
    outcomes are legitimate — a trailer that filters out the wrong audience lowers
    starts and raises completion rate — so the pair is reported rather than
    ranking on conversion alone. ``lift_vs_no_trailer`` above 1.0 means the trailer
    earns its placement.

    Accepts both catalogue and user-scope filters.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        limit: Maximum titles to return.
        min_starts: Titles with fewer *trailer views* than this are excluded. The
            denominator here is trailer views, not starts, because that is the
            figure the headline rate divides by.
        filters: Optional catalogue and user-scope filters.

    Returns:
        One row per title, ordered by trailer-to-start rate descending, with keys
        ``title``, ``genre``, ``content_type``, ``is_original``, ``detail_views``,
        ``trailer_views``, ``starts``, ``trailer_view_rate_pct``,
        ``trailer_to_start_pct``, ``start_without_trailer_pct``,
        ``lift_vs_no_trailer`` and ``completion_rate_pct``.
    """
    return await fetch_all(
        session,
        "content/content_trailer_to_start_cvr",
        {
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit,
            "min_starts": min_starts,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_shelf_life_decay(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return how engagement decays in the weeks after a title is added.

    Answers the acquisition question: does a title keep earning, or spike on
    release and go quiet? A steep curve means the catalogue needs constant
    replenishment; a flat one means library titles carry real weight. Originals and
    licensed titles are reported separately because they typically decay at
    different rates.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional catalogue filters. Unfiltered when omitted.

    Returns:
        One row per week since a title was added, ordered ascending, with keys
        ``week_since_added``, ``titles``, ``watch_hours``, ``starts``,
        ``pct_of_week0_mean``, ``pct_of_week0_median``, ``pct_of_week0_originals``
        and ``pct_of_week0_licensed``.

        The ``pct_of_week0_*`` columns are means of per-title indices, giving every
        title equal weight, so the curve describes a typical title rather than the
        largest one.
    """
    return await fetch_all(
        session,
        "content/content_shelf_life_decay",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_genre_performance_matrix(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return one row per genre across every commissioning dimension.

    The columns are deliberately in tension: ``catalogue_share_pct`` against
    ``watch_share_pct`` exposes over- and under-invested genres, and
    ``watch_per_title_index`` is the ratio between them — above 1.0 a genre earns
    more attention than its shelf space, below 1.0 it is over-invested relative to
    what it returns. ``view_to_start_pct`` separates "hard to find" from "not
    appealing".

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional catalogue filters. Unfiltered when omitted.

    Returns:
        One row per genre, ordered by watch hours descending, with keys ``genre``,
        ``titles``, ``originals``, ``series_count``, ``avg_runtime_minutes``,
        ``avg_popularity``, ``unique_viewers``, ``starts``, ``completions``,
        ``watch_hours``, ``completion_rate_pct``, ``view_to_start_pct``,
        ``avg_rating``, ``catalogue_share_pct``, ``watch_share_pct``,
        ``watch_per_title_index`` and ``watch_hours_per_title``.

        Genres with a catalogue but no viewing are present with zero engagement
        rather than absent, so an unwatched genre is visible instead of missing.
    """
    return await fetch_all(
        session,
        "content/genre_performance_matrix",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_genre_affinity_by_persona(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return which personas watch which genres, as affinity lift.

    The most visually convincing of the "planted signal recovered" queries.
    ``seeder/personas.py`` declares that Anime Fans over-index heavily on Anime and
    Sports Fans on Sports; the underlying SQL reads neither that file nor the
    coefficients in ``core.personas``. It counts watch time in the event stream and
    groups by a foreign key, so the affinities it reports were recovered from
    behaviour rather than asserted.

    ``affinity_lift`` is this persona's share of watch time in a genre divided by
    the genre's share across the whole population: above 1.0 is over-indexed.

    Accepts both catalogue and user-scope filters.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional catalogue and user-scope filters.

    Returns:
        One row per persona and genre, ordered by persona then watch time
        descending, with keys ``persona``, ``genre``, ``watch_seconds``,
        ``watch_hours``, ``playback_events``, ``pct_of_persona_watch``,
        ``pct_of_all_watch``, ``affinity_lift`` and ``rank_within_persona``.
    """
    return await fetch_all(
        session,
        "content/genre_affinity_by_persona",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_MIN_STARTS",
    "get_completion_rate",
    "get_genre_affinity_by_persona",
    "get_genre_performance_matrix",
    "get_shelf_life_decay",
    "get_top_watch_time",
    "get_trailer_to_start_cvr",
]

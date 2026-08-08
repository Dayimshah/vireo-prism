"""Funnel analysis: conversion, drop-off, elapsed time and segment comparison.

Five views of two different funnels, and the distinction between them is the thing
to get right before reading any number here.

**Grain.** :func:`get_discovery_to_watch` is *session-scoped* and answers "what
happened in this visit". :func:`get_signup_to_subscribe` is *user-scoped* and
answers "how far did this person ever get". Mixing the two grains is the most
common error in funnel analysis: a user who browsed on Monday and subscribed on
Friday converted, but did so in neither session alone. Two functions, two grains,
never blended.

**Orientation.** :func:`get_discovery_to_watch` and :func:`get_step_dropoff` run on
the same underlying counts, reoriented. The first reports conversion at each step;
the second reports the loss *between* steps and ranks it, which is the form that
answers "where should we spend next quarter" rather than "what is our conversion".
Both rank the same data — ``loss_rank`` by absolute sessions lost,
``rate_rank`` by proportional leak — because the biggest absolute loss and the
leakiest step are usually different steps, and each implies different work.

**Cause.** :func:`get_time_between_steps` is what separates friction from
indifference. A long median from view to start suggests the detail page is not
persuading; a short median with heavy drop-off suggests the title itself is the
problem. Conversion percentages alone cannot distinguish those.

The session-scoped queries read ``analytics.mv_funnel_steps``, one row per session
with a boolean per step. That reshape is what turns an eight-step funnel into eight
filtered counts over a single scan instead of eight self-joins against the event
table.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from app.core.exceptions import ValidationError
from app.repositories.base import FilterSet, fetch_all

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

#: Dimensions ``funnel/funnel_by_segment`` can group by.
#:
#: Exactly the ``CASE`` arms in that file. This set is **not** the same as
#: :data:`app.repositories.retention.RETENTION_SEGMENTS`: the funnel query splits
#: device into ``form_factor`` and ``platform``, while the retention query offers a
#: single ``device`` meaning form factor. The two allowlists are deliberately kept
#: next to the queries they describe rather than merged into one shared constant
#: that would be wrong for both.
FUNNEL_SEGMENTS: Final[frozenset[str]] = frozenset(
    {"country", "channel", "persona", "form_factor", "platform", "premium"}
)

#: Segments with fewer sessions than this are omitted from segmented output.
DEFAULT_MIN_COHORT_SIZE: Final[int] = 30


def _validate_segment(segment_by: str) -> str:
    """Check a segment dimension against the query's allowlist.

    The SQL is injection-safe without this — an unrecognised value falls through
    the ``CASE`` to ``'all'`` — but it would then return a single row labelled
    "all" to a caller who asked for something else, which reads as a real answer
    to a question nobody asked. Validating converts that into a 422.

    Args:
        segment_by: Requested dimension.

    Returns:
        The dimension, unchanged.

    Raises:
        ValidationError: If the dimension is not one the query supports.
    """
    if segment_by not in FUNNEL_SEGMENTS:
        raise ValidationError(
            f"Cannot segment the funnel by {segment_by!r}. "
            f"Allowed: {', '.join(sorted(FUNNEL_SEGMENTS))}."
        )
    return segment_by


async def get_discovery_to_watch(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return the session-scoped discovery-to-watch funnel.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per funnel step, ordered top to bottom, with keys ``step_order``,
        ``step_name``, ``sessions``, ``pct_of_entry``, ``pct_of_previous`` and
        ``dropped_from_previous``.

        ``pct_of_previous`` and ``dropped_from_previous`` are ``None`` on the first
        step, which has no predecessor. ``pct_of_entry`` measures against the top of
        the funnel; ``pct_of_previous`` localises where a loss actually occurs and
        is the more actionable of the two.
    """
    return await fetch_all(
        session,
        "funnel/funnel_discovery_to_watch",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_signup_to_subscribe(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return the user-scoped signup-to-subscription funnel.

    Scoped to each user's whole lifetime, not to a single visit, so a user who
    browsed one day and subscribed another is correctly counted as converted.

    Args:
        session: A read-only session.
        date_from: Earliest signup date included, inclusive.
        date_to: Latest signup date included, inclusive.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per funnel step, ordered top to bottom, with keys ``step_order``,
        ``step_name``, ``users``, ``pct_of_signups``, ``pct_of_previous`` and
        ``dropped_from_previous``. The last two are ``None`` on the first step.
    """
    return await fetch_all(
        session,
        "funnel/funnel_signup_to_subscribe",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_step_dropoff(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return the loss between consecutive funnel steps, ranked two ways.

    ``loss_rank`` orders by absolute sessions lost; ``rate_rank`` by proportional
    drop-off. They usually disagree, and the disagreement is informative: the step
    that loses the most people and the step that leaks most heavily imply different
    work.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per step transition, ordered by ``step_order``, with keys
        ``step_order``, ``from_step``, ``to_step``, ``from_count``, ``to_count``,
        ``users_lost``, ``dropoff_pct``, ``conversion_pct``, ``loss_rank`` and
        ``rate_rank``.
    """
    return await fetch_all(
        session,
        "funnel/funnel_step_dropoff",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_time_between_steps(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return elapsed time between consecutive funnel steps, as percentiles.

    Percentiles rather than a mean: the distribution has a long tail from sessions
    left open, and an average would be dominated by it.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per transition, ordered by ``step_order``, with keys
        ``step_order``, ``transition``, ``observations``, ``p25_seconds``,
        ``median_seconds``, ``p90_seconds`` and ``median_minutes``.

        Negative gaps are excluded by the query — they would indicate a clock or
        ordering fault — so a populated result is itself a quiet assertion that
        event ordering is sound.
    """
    return await fetch_all(
        session,
        "funnel/funnel_time_between_steps",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_funnel_by_segment(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    segment_by: str,
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return the discovery funnel split by a caller-chosen dimension.

    Comparing funnels across segments is where funnel analysis stops being a
    number and becomes a decision: an aggregate 30% view-to-start rate is a
    statistic, while discovering it is 55% on TV and 18% on phones is something to
    act on.

    Note the device dimension here is split into ``form_factor`` and ``platform``,
    unlike the retention equivalent.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        segment_by: One of :data:`FUNNEL_SEGMENTS`.
        min_cohort_size: Segments with fewer sessions than this are omitted.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per segment, ordered by end-to-end conversion descending, with keys
        ``segment``, ``opened``, ``discovered``, ``viewed``, ``started``,
        ``completed``, ``open_to_view_pct``, ``view_to_start_pct``,
        ``start_to_complete_pct`` and ``end_to_end_pct``.

        Device-based segments count the *session's* device rather than the signup
        device, because the funnel is a property of the visit.

    Raises:
        ValidationError: If ``segment_by`` is not a supported dimension.
    """
    return await fetch_all(
        session,
        "funnel/funnel_by_segment",
        {
            "date_from": date_from,
            "date_to": date_to,
            "segment_by": _validate_segment(segment_by),
            "min_cohort_size": min_cohort_size,
            **(filters or FilterSet()).as_params(),
        },
    )


__all__ = [
    "DEFAULT_MIN_COHORT_SIZE",
    "FUNNEL_SEGMENTS",
    "get_discovery_to_watch",
    "get_funnel_by_segment",
    "get_signup_to_subscribe",
    "get_step_dropoff",
    "get_time_between_steps",
]

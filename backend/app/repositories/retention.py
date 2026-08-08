"""Retention: three definitions, plus segment, persona and resurrection views.

Retention is the metric most often quoted without saying which definition
produced it, so this module exposes all three separately and names them
explicitly rather than shipping one "retention" function and an argument.

* **Classic day-N** (:func:`get_retention_nday`) — active on day N *exactly*.
  The strictest reading, and the one most dashboards mean without saying so.
* **Rolling** (:func:`get_retention_rolling`) — active on day N *or later*. The
  most forgiving, and the right one for "is this user genuinely gone", because it
  does not count someone who skipped day 7 and returned on day 8 as churned.
* **Unbounded** (:func:`get_retention_unbounded`) — active at any point *within*
  the first N days. Answers a different question altogether: not "are they still
  here" but "did they ever engage", which makes day-1 unbounded retention an
  activation metric.

The same window produces three different numbers from these, all correct. Which
is why they are three functions.

The cohort denominator
----------------------
Every one of them takes an ``observation_end``, and it is the parameter that
makes the figures honest. A user who signed up three days ago cannot have a day-7
retention outcome yet; counting them in the day-7 denominator silently understates
retention, and the error grows the closer the cohort sits to the end of the
window. Passing ``observation_end`` lets the SQL exclude users who have not had
time to reach each milestone. It defaults to ``date_to`` here, which is correct
whenever the window ends at the edge of available data.

Small cohorts are suppressed via ``min_cohort_size`` on the segmented queries.
Retention over eleven users swings twenty points on one person's behaviour, and
plotting that as signal is worse than omitting it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from app.core.exceptions import ValidationError
from app.repositories.base import FilterSet, fetch_all

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

#: Dimensions ``retention/retention_by_segment`` can group by.
#:
#: These are exactly the ``CASE`` arms in that file. The SQL is injection-safe
#: without this check — an unknown value falls through to ``'all'`` — but it would
#: then return one row labelled "all" for a caller who asked for something else,
#: which reads as a real answer to the wrong question. Validating here turns that
#: into a 422. Note ``device`` means *form factor*, and that this set differs from
#: the funnel equivalent in :data:`app.repositories.funnel.FUNNEL_SEGMENTS`.
RETENTION_SEGMENTS: Final[frozenset[str]] = frozenset(
    {"country", "channel", "persona", "device", "premium"}
)

#: Cohorts smaller than this are omitted from segmented retention output.
DEFAULT_MIN_COHORT_SIZE: Final[int] = 30


def _validate_segment(segment_by: str) -> str:
    """Check a segment dimension against the query's allowlist.

    Args:
        segment_by: Requested dimension.

    Returns:
        The dimension, unchanged.

    Raises:
        ValidationError: If the dimension is not one the query supports.
    """
    if segment_by not in RETENTION_SEGMENTS:
        raise ValidationError(
            f"Cannot segment retention by {segment_by!r}. "
            f"Allowed: {', '.join(sorted(RETENTION_SEGMENTS))}."
        )
    return segment_by


async def get_retention_nday(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    observation_end: date | None = None,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return classic day-N retention for users who signed up in the window.

    "Day 7 retention" here means active on day 7 precisely — not day 7 or later,
    and not within the first seven days.

    Args:
        session: A read-only session.
        date_from: Earliest signup date included in the cohort, inclusive.
        date_to: Latest signup date included in the cohort, inclusive.
        observation_end: Last date with activity data, used to exclude users who
            have not had time to reach a milestone. Defaults to ``date_to``.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per milestone day, ordered ascending, with keys ``day_n``,
        ``cohort_size``, ``retained_users`` and ``retention_pct``.
    """
    return await fetch_all(
        session,
        "retention/retention_nday",
        {
            "date_from": date_from,
            "date_to": date_to,
            "observation_end": observation_end or date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_retention_rolling(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    observation_end: date | None = None,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return rolling retention: active on day N or any day after it.

    Reads higher than classic retention at every milestone, by construction. The
    gap between the two curves is the population who use the product irregularly
    rather than having left.

    Args:
        session: A read-only session.
        date_from: Earliest signup date included in the cohort, inclusive.
        date_to: Latest signup date included in the cohort, inclusive.
        observation_end: Last date with activity data. Defaults to ``date_to``.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per milestone day, ordered ascending, with keys ``day_n``,
        ``cohort_size``, ``retained_users`` and ``retention_pct``.
    """
    return await fetch_all(
        session,
        "retention/retention_rolling",
        {
            "date_from": date_from,
            "date_to": date_to,
            "observation_end": observation_end or date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_retention_unbounded(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    observation_end: date | None = None,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return unbounded retention: active at any point within the first N days.

    Effectively an activation curve. A user who signed up and never came back
    still shows 100% at day 1 and flat thereafter, which is the correct reading of
    "did they ever engage" and the wrong reading of "are they still here".

    Args:
        session: A read-only session.
        date_from: Earliest signup date included in the cohort, inclusive.
        date_to: Latest signup date included in the cohort, inclusive.
        observation_end: Last date with activity data. Defaults to ``date_to``.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per milestone day, ordered ascending, with keys ``day_n``,
        ``cohort_size``, ``retained_users`` and ``retention_pct``.
    """
    return await fetch_all(
        session,
        "retention/retention_unbounded",
        {
            "date_from": date_from,
            "date_to": date_to,
            "observation_end": observation_end or date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_retention_by_segment(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    segment_by: str,
    observation_end: date | None = None,
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return day-N retention split by a caller-chosen dimension.

    The dimension reaches SQL as a bound parameter resolved through a ``CASE``,
    never as an interpolated column name.

    Args:
        session: A read-only session.
        date_from: Earliest signup date included in the cohort, inclusive.
        date_to: Latest signup date included in the cohort, inclusive.
        segment_by: One of :data:`RETENTION_SEGMENTS`. Note ``device`` groups by
            form factor.
        observation_end: Last date with activity data. Defaults to ``date_to``.
        min_cohort_size: Segments with fewer users than this are omitted.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per segment and milestone day, ordered by segment then day, with
        keys ``segment``, ``day_n``, ``cohort_size``, ``retained_users`` and
        ``retention_pct``.

    Raises:
        ValidationError: If ``segment_by`` is not a supported dimension.
    """
    return await fetch_all(
        session,
        "retention/retention_by_segment",
        {
            "date_from": date_from,
            "date_to": date_to,
            "segment_by": _validate_segment(segment_by),
            "observation_end": observation_end or date_to,
            "min_cohort_size": min_cohort_size,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_retention_curve_by_persona(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    observation_end: date | None = None,
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return weekly retention curves per persona, weeks 0 through 12.

    This is the query that demonstrates the dataset has causal structure rather
    than being random noise. The persona coefficients live in ``core.personas``
    and in the seeder; the underlying SQL reads neither — it counts activity in the
    event stream and groups by a foreign key. The resulting ordering of personas
    was therefore recovered from behaviour, not asserted by the query.

    Args:
        session: A read-only session.
        date_from: Earliest signup date included in the cohort, inclusive.
        date_to: Latest signup date included in the cohort, inclusive.
        observation_end: Last date with activity data. Defaults to ``date_to``.
        min_cohort_size: Personas with fewer users than this are omitted.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per persona and week, ordered by persona then week, with keys
        ``persona``, ``week_n``, ``cohort_size``, ``retained_users`` and
        ``retention_pct``.
    """
    return await fetch_all(
        session,
        "retention/retention_curve_by_persona",
        {
            "date_from": date_from,
            "date_to": date_to,
            "observation_end": observation_end or date_to,
            "min_cohort_size": min_cohort_size,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_resurrection_rate(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return monthly resurrection: users returning after 28+ days dormant.

    The only positive signal available about users who have already lapsed. Read
    alongside retention: a rising resurrection rate with falling retention usually
    indicates a cadence problem rather than a value problem — people keep coming
    back, just not on schedule.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per month, ordered ascending, with keys ``month``,
        ``resurrected``, ``dormant_pool``, ``active_returning``,
        ``avg_dormant_days`` and ``resurrection_rate_pct``. The rate is ``None``
        for a month with an empty dormant pool.
    """
    return await fetch_all(
        session,
        "retention/resurrection_rate",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


__all__ = [
    "DEFAULT_MIN_COHORT_SIZE",
    "RETENTION_SEGMENTS",
    "get_resurrection_rate",
    "get_retention_by_segment",
    "get_retention_curve_by_persona",
    "get_retention_nday",
    "get_retention_rolling",
    "get_retention_unbounded",
]

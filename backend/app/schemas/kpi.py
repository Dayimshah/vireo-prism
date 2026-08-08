"""Response models for the six headline KPI series.

One model per query in :mod:`app.repositories.kpi`, field-for-field with what Postgres
returns. All six read ``analytics.mv_user_daily`` and all six are daily series keyed on
``day``, so a client can plot any of them on the same axis.

Where these field lists come from
---------------------------------
Every model in this package was written against the *observed* result of running its
query on live data — column names, Python types and nullability all read off the real
rows rather than transcribed from the ``.sql`` file. Two of them would have been wrong
the other way round, and both are in this module:

* ``median_sessions_per_user`` and ``p90_sessions_per_user`` come back as ``float``,
  not ``Decimal``, because ``PERCENTILE_CONT`` returns double precision and those two
  are the only percentile columns in the API with no ``::numeric`` cast. Declared as
  ``float`` to match. It is not worth changing the frozen SQL over — the values are
  session counts to one decimal place — but declaring ``Decimal`` here would reject
  every row.
* ``watch_minutes_per_user`` and ``stickiness_pct`` are genuinely nullable, on exactly
  one day each in the seeded window. Both are ratios, and the null is the project's
  standing convention rather than missing data: a day with no active users has no
  average, and reporting zero would plot an absence as a measurement.

What is *not* restated here
---------------------------
The semantic contract of each row — what the metric means, which rows can be compared
against which, what a null implies — lives in :mod:`app.repositories.kpi` beside the
query that produces it. These models declare names and types so OpenAPI can describe
the payload; they do not re-describe the numbers. One description of a result shape can
be wrong, two will eventually disagree, and the copy further from the SQL is the one
that goes stale.

Field descriptions appear only where a name is not self-explanatory, or where there is
a trap worth naming at the point a reader meets it.
"""

from __future__ import annotations

from datetime import date  # noqa: TC003 — Pydantic resolves annotations at runtime

from pydantic import Field

from app.schemas.base import Number, RowModel


class DauRow(RowModel):
    """One day of daily active users.

    Attributes:
        day: The activity date.
        dau: Distinct users active that day.
        sessions: Sessions started that day.
        watch_seconds: Total watch time that day, in seconds.
        watch_minutes_per_user: Mean watch minutes per active user, or ``None`` on a
            day with no active users.
    """

    day: date
    dau: int
    sessions: int
    watch_seconds: int
    watch_minutes_per_user: Number | None = None


class WauRow(RowModel):
    """One day's trailing 7-day active users.

    Attributes:
        day: The as-of date.
        wau: Distinct users active in the 7 days ending on ``day``, inclusive.
    """

    day: date
    wau: int


class MauRow(RowModel):
    """One day's trailing 28-day active users.

    Attributes:
        day: The as-of date.
        mau: Distinct users active in the 28 days ending on ``day``, inclusive.
    """

    day: date
    mau: int


class StickinessRow(RowModel):
    """One day's DAU/MAU ratio.

    Attributes:
        day: The as-of date.
        dau: Distinct users active on ``day``.
        mau: Distinct users active in the trailing 28 days.
        stickiness_pct: ``dau / mau`` as a percentage, or ``None`` when ``mau`` is
            zero — an undefined ratio rather than a stickiness of nought.
    """

    day: date
    dau: int
    mau: int
    stickiness_pct: Number | None = None


class NewVsReturningRow(RowModel):
    """One day's active users split by how long they have been around.

    Attributes:
        day: The activity date.
        new_users: Active users who signed up that day.
        returning_users: Active users who were also active recently.
        resurrected_users: Active users returning after a dormant spell.
        total_active: The three categories summed, equal to that day's DAU.
    """

    day: date
    new_users: int
    returning_users: int
    resurrected_users: int
    total_active: int


class SessionsPerUserRow(RowModel):
    """One day's session-count distribution across active users.

    Attributes:
        day: The activity date.
        active_users: Users active that day.
        total_sessions: Sessions started that day.
        mean_sessions_per_user: ``total_sessions / active_users``.
        median_sessions_per_user: Median sessions per active user. A ``float`` rather
            than a ``Decimal`` — see the module docstring.
        p90_sessions_per_user: 90th percentile sessions per active user, likewise a
            ``float``.
        mean_events_per_user: Mean events per active user across all their sessions.
    """

    day: date
    active_users: int
    total_sessions: int
    mean_sessions_per_user: Number
    median_sessions_per_user: float = Field(
        description="Median sessions per active user. Double precision, not decimal.",
    )
    p90_sessions_per_user: float = Field(
        description="90th percentile sessions per active user. Double precision.",
    )
    mean_events_per_user: Number


__all__ = [
    "DauRow",
    "MauRow",
    "NewVsReturningRow",
    "SessionsPerUserRow",
    "StickinessRow",
    "WauRow",
]

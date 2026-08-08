"""The repository layer: one typed function per named analytics query.

Fourteen domain modules, mirroring the fourteen namespaces under
``app/sql/queries/`` exactly. ``kpi/dau.sql`` is reached through
:func:`app.repositories.kpi.get_dau`, and that one-to-one correspondence is the
point — a reader who finds a number on the dashboard can follow it to a function,
then to a ``.sql`` file, without a translation table.

Nothing in this package builds SQL, opens a transaction, or touches the engine.
Each function takes a session, names a query, hands over parameters, and returns
plain dictionaries. The shared machinery lives in :mod:`app.repositories.base`,
which is worth reading first: it reconciles parameters against what each query
declares, coerces dates for asyncpg, and normalises empty filter lists to ``NULL``
so an empty multi-select widens a query instead of silently excluding everything.

Import modules, not names
-------------------------
This package deliberately exports the modules rather than re-exporting their
functions flat::

    from app.repositories import kpi, retention

    series = await kpi.get_dau(session, date_from, date_to)
    curve = await retention.get_retention_nday(session, date_from, date_to)

Two reasons. The call site reads as ``kpi.get_dau`` rather than a bare ``get_dau``,
which matters when a service composes several domains in one function. And a flat
namespace would collide: six modules define ``DEFAULT_MIN_COHORT_SIZE``, each tuned
to its own query, and ``retention.RETENTION_SEGMENTS`` and
``funnel.FUNNEL_SEGMENTS`` are genuinely different allowlists that a shared name
would conflate.

Module map
----------
=================== ======= =====================================================
Module              Queries Covers
=================== ======= =====================================================
``kpi``                   6 DAU, WAU, MAU, stickiness, daily composition
``retention``             6 three retention definitions, segments, resurrection
``sessions``              6 duration, depth, composition, timing, device switching
``content``               6 leaderboards, completion, decay, genre economics
``funnel``                5 two funnels, drop-off, elapsed time, segments
``cohort``                4 retention matrices, cumulative revenue, LTV by channel
``monetization``          4 ARPU, MRR movement, trial and paywall conversion
``marketing``             3 attribution, LTV:CAC, payback period
``churn``                 2 reason mix, risk scorecard
``geo``                   2 country ranking, device breakdown
``events``                1 event-stream composition
``experiments``           1 per-variant A/B outcome counts
``search``                1 global search
``users``                 1 RFM segmentation
=================== ======= =====================================================

Conventions that hold across every module
-----------------------------------------
* **Filters.** Optional :class:`~app.repositories.base.FilterSet`, defaulting to
  unfiltered. Passing a full set is always safe — each query receives only the
  parameters it declares, so the same object serves a user-scoped and a
  catalogue-scoped query.
* **Types are preserved.** Money and rates come back as :class:`~decimal.Decimal`,
  days as :class:`~datetime.date`. Rounding and serialisation are the response
  schemas' decision, not this layer's.
* **``None`` means undefined, never zero.** A ratio with an empty denominator, an
  unobservable cohort cell, a payback period not yet reached — all ``None``. Zero
  would be plotted as a measurement.
* **Small denominators are suppressed** via ``min_cohort_size`` or ``min_starts``,
  because a rate over nine users is noise wearing the costume of a finding.
* **Dates are inclusive** at both ends of every window.
"""

from __future__ import annotations

from app.repositories import (
    churn,
    cohort,
    content,
    events,
    experiments,
    funnel,
    geo,
    kpi,
    marketing,
    monetization,
    retention,
    search,
    sessions,
    users,
)
from app.repositories.base import FilterSet, bind_params, fetch_all, fetch_one

__all__ = [
    "FilterSet",
    "bind_params",
    "churn",
    "cohort",
    "content",
    "events",
    "experiments",
    "fetch_all",
    "fetch_one",
    "funnel",
    "geo",
    "kpi",
    "marketing",
    "monetization",
    "retention",
    "search",
    "sessions",
    "users",
]

"""The service layer: filters resolved, windows validated, results cached.

Between the routers and the repositories. Repositories take integer keys and run one
query; routers parse HTTP. Something has to turn ``?country=India&country=IN`` into
``country_ids=[7]``, refuse a window nobody should be allowed to ask for, and decide
whether the answer can come from cache — and that is this package.

Fourteen domain modules mirror :mod:`app.repositories` one-to-one, so a number on the
dashboard leads to a service function, then a repository function, then a ``.sql``
file, without a translation table. Three modules have no repository counterpart:
:mod:`~app.services.base` holds the shared machinery, :mod:`~app.services.stats` the
significance tests, and :mod:`~app.services.overview` composes other services rather
than wrapping a query.

Nothing here imports ``fastapi``. Every service takes its session and its dimension
catalogue as arguments, so the whole layer is callable from a test, a script or the
seeder, and the HTTP vocabulary — status codes, headers, query strings — stays at the
edge. :class:`~app.db.deps.DimensionCatalog` does import ``fastapi``, which is exactly
why it is passed in rather than fetched, and why it appears in these modules under
``TYPE_CHECKING`` only.

Import modules, not names
-------------------------
As in :mod:`app.repositories`, this package exports the modules::

    from app.services import cohort, kpi

    series = await kpi.get_dau(session, catalog, date_from, date_to, filters)
    matrix = await cohort.get_monthly_matrix(session, catalog, date_from, date_to)

``kpi.get_dau`` reads better than a bare ``get_dau`` where a router composes several
domains, and a flat namespace would collide outright: six modules re-export
``DEFAULT_MIN_COHORT_SIZE``, each tuned to its own query, and
``retention.RETENTION_SEGMENTS`` and ``funnel.FUNNEL_SEGMENTS`` are genuinely
different allowlists that one shared name would conflate.

The shape almost every service function follows
-----------------------------------------------
``(session, catalog, window arguments, filters)`` in, ``list[dict]`` out::

    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)
    return await cached_rows(namespace, name, params, producer, ttl)

Validation runs before the cache lookup, so a 422 never depends on cache state. The
cache key carries the *resolved* parameters, so ``?country=IN`` and ``?country=India``
share one entry rather than computing the same answer twice.

Where a function departs from that shape, it is because the metric does — not for
convenience:

* ``churn.get_risk_scorecard`` and ``users.get_rfm_segments`` take **no dates**. Both
  describe the present state of the user base, anchored to the dataset's latest
  activity date. A window parameter would imply a time-slicing neither supports.
* ``geo.get_country_ranking`` takes **only ``date_to``**. It is a lifetime-to-date
  league table, and a start date would quietly make it a windowed one.
* ``experiments.get_variant_metrics`` takes an ``observation_end`` but no range: an
  experiment's window is a property of the experiment, carried on
  ``core.experiments``.
* ``search.search`` takes neither dates, filters nor a catalogue. It is navigation
  over the whole dataset; restricting it to the active dashboard filters would make
  results appear and disappear for reasons the user cannot see.
* ``experiments.get_results`` and ``overview.get_overview`` return **dataclasses**,
  not rows, because both compute something the SQL deliberately does not.

Module map
----------
=================== ========= ===================================================
Module              Functions Covers
=================== ========= ===================================================
``kpi``                     6 DAU, WAU, MAU, stickiness, daily composition
``retention``               6 three retention definitions, segments, resurrection
``sessions``                6 duration, depth, composition, timing, switching
``content``                 6 leaderboards, completion, decay, genre economics
``funnel``                  5 two funnels, drop-off, elapsed time, segments
``cohort``                  4 retention matrices, cumulative revenue, LTV
``monetization``            4 ARPU, MRR movement, trial and decile conversion
``marketing``               3 attribution, LTV:CAC, payback period
``churn``                   2 reason mix, risk scorecard
``geo``                     2 country ranking, device breakdown
``experiments``             2 raw variant counts, and tested results
``events``                  1 event-stream composition
``search``                  1 global search
``users``                   1 RFM segmentation
``overview``                1 headline tiles with period-over-period deltas
=================== ========= ===================================================

Caching, and the one thing to know about it
-------------------------------------------
:func:`~app.services.base.cached_rows` is the only way this layer caches, and it
exists because the two backends disagree about types. ``LocalCache`` stores live
objects; ``RedisCache`` serialises with ``json.dumps(..., default=str)``, which turns
a :class:`~decimal.Decimal` into ``"12.34"``. Left alone, a type guarantee would hold
on a miss and break on a hit. A tagged codec runs on **both** paths so a hit and a
miss are indistinguishable — see that module's docstring for why encoding a freshly
computed result is deliberate rather than wasteful.

Lifetimes come from three named bands (:class:`~app.services.base.Ttl`) rather than a
number per function: ``KPI`` for the headline row, ``HEAVY`` for anything scanning the
event table, ``DEFAULT`` for the rest.

:func:`~app.services.base.cache_status` reports ``HIT``/``MISS``/``PARTIAL``/``NONE``
for the request, for phase 9's ``X-Cache`` header. ``PARTIAL`` is a real state, not a
symptom: ``overview.get_overview`` performs six lookups and will routinely mix hits
with misses.

Conventions inherited from the repository layer
-----------------------------------------------
* **``None`` means undefined, never zero.** An unobservable cohort cell, a ratio with
  an empty denominator, a payback period not yet reached. Zero would be plotted as a
  measurement.
* **Types are preserved.** Money and rates stay :class:`~decimal.Decimal`, days stay
  :class:`~datetime.date`. Rounding and serialisation are phase 9's decision.
* **Dates are inclusive** at both ends of every window.
* **Row shapes are documented in the repository, not here.** Each service names the
  function it wraps rather than restating twenty column names — one description of a
  result shape can be wrong, two will eventually disagree, and the copy further from
  the SQL is the one that goes stale.
"""

from __future__ import annotations

from app.services import (
    base,
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
    overview,
    retention,
    search,
    sessions,
    stats,
    users,
)
from app.services.base import (
    NO_FILTERS,
    DateWindow,
    FilterRequest,
    Ttl,
    cache_status,
    cached_rows,
    record_cache_lookup,
    reset_cache_status,
    resolve_filters,
    resolve_limit,
    resolve_window,
)

__all__ = [
    "NO_FILTERS",
    "DateWindow",
    "FilterRequest",
    "Ttl",
    "base",
    "cache_status",
    "cached_rows",
    "churn",
    "cohort",
    "content",
    "events",
    "experiments",
    "funnel",
    "geo",
    "kpi",
    "marketing",
    "monetization",
    "overview",
    "record_cache_lookup",
    "reset_cache_status",
    "resolve_filters",
    "resolve_limit",
    "resolve_window",
    "retention",
    "search",
    "sessions",
    "stats",
    "users",
]

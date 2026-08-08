"""Request and response schemas: the API's wire contract.

The outermost layer of the backend. Repositories run SQL, services filter and cache,
and this package decides what a caller actually sees — field names, types, nullability,
and the envelope around them.

Import modules, not names
-------------------------
As in :mod:`app.repositories` and :mod:`app.services`, this package exports modules::

    from app.schemas import kpi, overview

    router.get("/dau", response_model=DataResponse[kpi.DauRow])

Unlike those two layers there is no name collision forcing the choice — every model in
the package happens to be uniquely named. It is done for symmetry: a number on the
dashboard traces through ``schemas.kpi`` → ``services.kpi`` → ``repositories.kpi`` →
``sql/queries/kpi/`` with the same spelling at every step, and a flat namespace here
would break that at the last hop.

The shared envelope lives in :mod:`app.schemas.base` and is re-exported flat, because
every router needs :class:`~app.schemas.base.DataResponse` and
:func:`~app.schemas.base.build_meta` and qualifying them adds nothing.

Where the field lists come from
-------------------------------
Every row model was written against the *observed* output of running its query on live
data — column names, Python types and nullability read off real rows rather than
transcribed from the ``.sql`` files. Two fields would have been wrong the other way
round, and both are in :mod:`app.schemas.kpi`: ``median_sessions_per_user`` and
``p90_sessions_per_user`` come back as ``float``, not ``Decimal``, because
``PERCENTILE_CONT`` returns double precision and those two are the only percentile
columns in the API with no ``::numeric`` cast.

Nullability carries a deliberate bias, set out at length in :mod:`app.schemas.content`:
observed nulls make a field optional, and ratios whose denominator can be empty are
optional even where none was observed. An over-strict field is a 500 in front of a
reader on some filter nobody tried; an over-loose one is a ``| null`` in the schema and
a check a client wants anyway.

Two conventions worth stating once
----------------------------------
**Decimals cross the wire as JSON numbers.** Pydantic renders a
:class:`~decimal.Decimal` as a string by default, to preserve exactness. Every decimal
here has already been rounded by SQL to two or three places, so that exactness was spent
upstream, while a string costs every consumer a parse — Recharts plots ``"12.34"`` as a
category rather than a magnitude, silently, with an axis that looks plausible. The type
*distinction* survives: integers stay integers, fractional columns stay fractional.

**``None`` means undefined, never zero.** Inherited from the repository layer and
preserved end to end: an unobservable cohort cell, a ratio with an empty denominator, a
payback period not yet reached. Zero would be plotted as a measurement.

Module map
----------
======================= ======== ==================================================
Module                  Models   Covers
======================= ======== ==================================================
``base``                       9 envelope, meta, problem details, shared types
``params``                     5 window, filters, limit, observation cut-off
``kpi``                        6 DAU, WAU, MAU, stickiness, daily composition
``retention``                  6 three retention definitions, segments, resurrection
``sessions``                   6 duration, depth, composition, timing, switching
``content``                    6 leaderboards, completion, decay, genre economics
``funnel``                     5 two funnels, drop-off, elapsed time, segments
``cohort``                     4 retention matrices, cumulative revenue, LTV
``monetization``               4 ARPU, MRR movement, trial and decile conversion
``marketing``                  3 attribution, LTV:CAC, payback period
``churn``                      2 reason mix, risk scorecard
``geo``                        2 country ranking, device breakdown
``experiments``                4 variant counts, tested results, intervals
``events``                     1 event-stream composition
``search``                     1 global search
``users``                      1 RFM segmentation
``overview``                   2 headline tiles with period-over-period deltas
``meta``                       4 filter options, dataset bounds, health, refresh
======================= ======== ==================================================
"""

from __future__ import annotations

from app.schemas import (
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
    meta,
    monetization,
    overview,
    params,
    retention,
    search,
    sessions,
    users,
)
from app.schemas.base import (
    COMMON_ERROR_RESPONSES,
    DataResponse,
    Number,
    PrismModel,
    ProblemDetail,
    ResponseMeta,
    RowModel,
    Seconds,
    ValueResponse,
    WindowEcho,
    build_meta,
    with_rate_limit,
)
from app.schemas.params import (
    EndDateDep,
    FilterDep,
    LimitDep,
    ObservationDep,
    WindowDep,
)

__all__ = [
    "COMMON_ERROR_RESPONSES",
    "DataResponse",
    "EndDateDep",
    "FilterDep",
    "LimitDep",
    "Number",
    "ObservationDep",
    "PrismModel",
    "ProblemDetail",
    "ResponseMeta",
    "RowModel",
    "Seconds",
    "ValueResponse",
    "WindowDep",
    "WindowEcho",
    "base",
    "build_meta",
    "churn",
    "cohort",
    "content",
    "events",
    "experiments",
    "funnel",
    "geo",
    "kpi",
    "marketing",
    "meta",
    "monetization",
    "overview",
    "params",
    "retention",
    "search",
    "sessions",
    "users",
    "with_rate_limit",
]

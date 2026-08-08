"""Every router in the API, in the order they are mounted and documented.

:data:`ALL_ROUTERS` is the single list :mod:`app.main` iterates over. Adding a router
means adding it here and nowhere else, so a new domain cannot be written, imported, and
then silently left unmounted — a failure that looks like a 404 on a route the code
clearly defines.

Why the order is written out rather than sorted
----------------------------------------------
It is the order the tags appear in ``/docs``, which is the order a reader meets the API.
Alphabetical would open on Admin and bury the overview tiles in the middle. So the
sequence runs roughly as a reader would explore it: the landing tiles, then engagement,
then what people watched, then the funnels and cohorts behind conversion, then money,
then the specialised lookups, and finally the service's own metadata and the one
privileged write.

Route matching is unaffected by this order — every router carries a distinct prefix, and
the only path parameters in the API sit under ``/experiments/{experiment_key}/``, where no
static sibling route exists to shadow or be shadowed.

:mod:`app.routers.base` is deliberately absent: it holds the shared response helpers, not
a router, and it is the one module in this package with no ``router`` attribute.
"""

# No `from __future__ import annotations` in this package — see the note in
# `app/routers/kpi.py`. That makes `APIRouter` below a *runtime* import rather than a
# TYPE_CHECKING one: the annotation on `ALL_ROUTERS` is a module-level variable
# annotation, which Python evaluates eagerly without postponed evaluation. Guarding it
# raises `NameError: name 'APIRouter' is not defined` at import and the app never starts.
from fastapi import APIRouter

from app.routers import (
    admin,
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
    retention,
    search,
    sessions,
    users,
)

#: Every router, in mount and documentation order.
ALL_ROUTERS: tuple[APIRouter, ...] = (
    overview.router,
    kpi.router,
    retention.router,
    sessions.router,
    content.router,
    funnel.router,
    cohort.router,
    monetization.router,
    marketing.router,
    churn.router,
    geo.router,
    users.router,
    experiments.router,
    events.router,
    search.router,
    meta.router,
    admin.router,
)

__all__ = [
    "ALL_ROUTERS",
    "admin",
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
    "retention",
    "search",
    "sessions",
    "users",
]

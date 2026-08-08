"""Response models for the service's own metadata: filters, health, dataset bounds.

Three things a client needs that are not analytics.

**Filter options.** The dashboard's multi-selects are populated from
:meth:`~app.db.deps.DimensionCatalog.options` rather than hard-coded, so a dimension row
added by a migration appears in the UI without a frontend change.

**Health.** Readiness distinguishes "connected but unseeded" from "connected and
ready", because those need different actions and a single boolean would hide which.

**Dataset bounds.** The window parameters have no defaults — see
:mod:`app.schemas.params` for why a "last 30 days" default would open every chart empty
on a repository cloned months after its data was generated. This endpoint is the
replacement: it reports the real first and last activity dates, so a client can pick a
window that contains data instead of guessing.
"""

from __future__ import annotations

from datetime import date  # noqa: TC003 — Pydantic resolves annotations at runtime

from pydantic import Field

from app.schemas.base import PrismModel


class FilterOptions(PrismModel):
    """Every filter's valid values, for the dashboard's filter bar.

    Countries are reported by name only. ISO codes remain accepted as input, but
    listing both would double the length of a list a human reads.

    Attributes:
        country: Country names.
        device: Device names.
        platform: Device platforms, e.g. ``iOS``.
        form_factor: Device form factors, e.g. ``phone``.
        channel: Marketing channel names.
        channel_group: Channel groups, e.g. ``Paid``.
        persona: Persona names.
        genre: Genre names.
        plan: Subscription plan names.
        plan_tier: Plan tiers, e.g. ``premium``.
        content_type: ``core.content_type`` enum labels.
    """

    country: list[str] = Field(default_factory=list)
    device: list[str] = Field(default_factory=list)
    platform: list[str] = Field(default_factory=list)
    form_factor: list[str] = Field(default_factory=list)
    channel: list[str] = Field(default_factory=list)
    channel_group: list[str] = Field(default_factory=list)
    persona: list[str] = Field(default_factory=list)
    genre: list[str] = Field(default_factory=list)
    plan: list[str] = Field(default_factory=list)
    plan_tier: list[str] = Field(default_factory=list)
    content_type: list[str] = Field(default_factory=list)


class DatasetBounds(PrismModel):
    """The span of the seeded data, so a client can choose a window inside it.

    Attributes:
        first_activity_date: Earliest day with recorded activity, or ``None`` when the
            database is migrated but unseeded.
        last_activity_date: Latest day with recorded activity, or ``None``.
        days: Days spanned, inclusive of both ends, or ``None``.
        users: Users in the dataset.
        events: Events recorded. Approximate: read from the planner's row estimate
            rather than counted, because an exact count over a 65-partition table is a
            full scan and this figure exists for orientation, not arithmetic.
        is_seeded: Whether the dataset contains any activity at all. When ``False`` the
            dates above are ``None`` and every analytics endpoint will return empty
            series rather than failing.
    """

    first_activity_date: date | None = None
    last_activity_date: date | None = None
    days: int | None = None
    users: int = 0
    events: int = Field(
        default=0,
        description="Approximate, from the planner's estimate. Not an exact count.",
    )
    is_seeded: bool = False


class HealthStatus(PrismModel):
    """Liveness and readiness for the service and its dependencies.

    Attributes:
        status: ``ok`` when the database is connected, migrated and populated;
            ``degraded`` when it is reachable but not ready; ``error`` when it is not
            reachable. A single flag would collapse the middle case, which is the one
            with an actionable fix.
        version: The API version from configuration.
        environment: Which environment this process believes it is running in.
        database_connected: Whether a connection succeeded.
        schema_ready: Whether the migrations have been applied.
        analytics_ready: Whether the materialized views hold data. ``False`` here with
            ``schema_ready`` true means the database was migrated but never seeded —
            run ``make seed``.
        cache_backend: ``redis`` or ``local``. Redis is optional and the local LRU is a
            deliberate fallback, not a failure, so this reports which one is actually
            serving rather than which one was configured.
        detail: A message when something is not ready, naming the fix.
    """

    status: str
    version: str
    environment: str
    database_connected: bool = False
    schema_ready: bool = False
    analytics_ready: bool = False
    cache_backend: str = "local"
    detail: str | None = None


class RefreshResult(PrismModel):
    """The outcome of an analytics refresh.

    Attributes:
        refreshed: Views rebuilt, in the order the database reported them.
        duration_seconds: Wall-clock time the refresh took.
        concurrent: Whether ``REFRESH MATERIALIZED VIEW CONCURRENTLY`` was used.
            Concurrent refresh does not block readers but requires the view to have
            been populated once already, so the first refresh after a migration is
            necessarily non-concurrent.
        detail: A human-readable summary.
    """

    refreshed: list[str] = Field(default_factory=list)
    duration_seconds: float
    concurrent: bool
    detail: str


__all__ = [
    "DatasetBounds",
    "FilterOptions",
    "HealthStatus",
    "RefreshResult",
]

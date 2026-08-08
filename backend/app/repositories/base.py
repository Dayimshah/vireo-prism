"""Shared execution machinery for the repository layer.

Every repository in this package is a thin, typed function over one named query
from :mod:`app.sql.registry`. None of them build SQL, open transactions, or touch
the engine; they take a session, name a query, hand over parameters, and return
plain Python rows. This module is the one place that knows how to do that
correctly, which is why it is worth reading before any of the fourteen domain
modules.

Four things happen here that a naive ``session.execute(text(sql), params)`` gets
wrong, each of which cost real debugging time during phase 6 verification.

**Parameters are reconciled against the query, not assumed.** The 48 queries do
not take a uniform parameter set: ``geo/country_engagement_ranking`` takes
``:date_to`` with no ``:date_from``, and ``users/power_users_rfm_decile`` takes no
dates at all. :func:`fetch_all` intersects what a caller supplies with what
:meth:`~app.sql.registry.SqlRegistry.params` reports the file actually declares —
dropping extras, and raising on a genuine omission. A missing parameter is a
programming error, and it surfaces here naming the query and the parameter
instead of as a driver-level complaint about a bind that has no value.

**Dates are coerced to real date objects.** asyncpg is strict where psycopg was
lenient: ``CAST(:date_from AS date)`` bound to the string ``"2024-01-01"`` fails
with ``'str' object has no attribute 'toordinal'``. The driver wants a
:class:`datetime.date`. Converting at this boundary means no caller has to
remember, and an ISO string arriving from a query parameter is handled rather
than fatal.

**Empty filter lists become NULL.** Every optional filter in the SQL reads
``CAST(:country_ids AS int[]) IS NULL OR u.country_id = ANY(...)``. Passing ``[]``
does not disable that predicate — it makes it match nothing, so an empty
multi-select would silently return zero rows and read as a real finding.
:meth:`FilterSet.as_params` normalises empty sequences to ``None``.

**Driver errors are translated.** Raw asyncpg messages can carry schema names and
parameter values. :func:`app.db.session.translate_db_error` maps them onto the
application taxonomy — a statement timeout becomes a 504 with advice, an
unpopulated materialized view becomes a 503 naming the fix — while the original
is logged in full.

There is deliberately no dynamic ``ORDER BY`` support. All 48 queries sort on
fixed expressions, and the two segment-comparison queries resolve the caller's
choice through bound ``CASE`` arms rather than an interpolated column name, so
:func:`app.core.security.build_order_by` is not needed by this layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import time
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy.exc import SQLAlchemyError

from app.core.logging import get_logger
from app.db.session import translate_db_error
from app.sql.registry import get_registry

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

#: Parameters that must reach the driver as :class:`datetime.date`, never as a
#: string. Small and explicit rather than inferred from the value's shape: a
#: pattern match would eventually coerce something that only looked like a date.
DATE_PARAMS: Final[frozenset[str]] = frozenset({"date_from", "date_to", "observation_end"})

#: Queries slower than this are logged at warning level. Phase 6 measured 47 of
#: 48 queries under two seconds against the small profile, so anything above this
#: is a regression worth seeing in the log rather than a known cost.
_SLOW_QUERY_MS: Final[float] = 2_000.0


def _coerce_date(name: str, value: object) -> object:
    """Convert a date-like parameter into a :class:`datetime.date`.

    Args:
        name: Parameter name, used in the error message.
        value: Supplied value. ``None``, a ``date``, a ``datetime`` or an ISO
            ``YYYY-MM-DD`` string.

    Returns:
        A :class:`datetime.date`, or ``None`` when the input was ``None``.

    Raises:
        RuntimeError: If the value cannot be interpreted as a date. This is a
            programming error at the service boundary, not caller input, which is
            validated by the request schemas.
    """
    # The datetime exclusion is load-bearing: datetime is a subclass of date, so
    # without it a datetime would be returned untouched and reach a date column.
    if value is None or (isinstance(value, date) and not isinstance(value, datetime)):
        return value

    # Truncating is correct here: every consumer of these parameters compares
    # against a date column.
    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError as exc:
            raise RuntimeError(
                f"Parameter {name!r} must be a date or an ISO 'YYYY-MM-DD' string, "
                f"got {value!r}."
            ) from exc

    raise RuntimeError(
        f"Parameter {name!r} must be a date, datetime or ISO string, "
        f"got {type(value).__name__}."
    )


@dataclass(frozen=True, slots=True)
class FilterSet:
    """Resolved values for the two shared SQL filter fragments.

    Forty of the 48 queries include ``{{user_filter}}``, and six include
    ``{{content_filter}}``. Carrying those eight values as one object keeps them
    out of forty function signatures and gives the service layer a single thing
    to construct.

    The fields line up exactly with what :class:`app.db.deps.DimensionCatalog`
    returns: the ``*_ids`` fields hold surrogate keys from its ``resolve_*``
    methods, and ``content_types`` and ``languages`` hold allowlisted display
    strings from its ``validate_*`` methods. Nothing here re-validates, because a
    value reaching this point has already been checked against the dimension
    tables — validating twice would invite the two checks to disagree.

    ``None`` means "no filter". So does an empty sequence, which
    :meth:`as_params` normalises, because the SQL treats ``NULL`` as "match all"
    and an empty array as "match nothing".

    Attributes:
        country_ids: ``core.countries`` keys.
        channel_ids: ``core.marketing_channels`` keys.
        persona_ids: ``core.personas`` keys.
        signup_device_ids: ``core.devices`` keys, matched against the signup
            device on ``core.users``. Session-level device filtering is a
            property of individual queries, not of this scope block.
        is_premium: Current paid state. ``False`` is a real filter and is
            preserved; only ``None`` disables it.
        genre_ids: ``core.genres`` keys.
        content_types: ``core.content_type`` enum labels.
        languages: Catalogue language names.
    """

    country_ids: Sequence[int] | None = None
    channel_ids: Sequence[int] | None = None
    persona_ids: Sequence[int] | None = None
    signup_device_ids: Sequence[int] | None = None
    is_premium: bool | None = None
    genre_ids: Sequence[int] | None = None
    content_types: Sequence[str] | None = None
    languages: Sequence[str] | None = None

    def as_params(self) -> dict[str, Any]:
        """Render the filters as bound parameters.

        Sequences are materialised into lists — asyncpg accepts a list for an
        array parameter but not an arbitrary iterable — and empty ones collapse to
        ``None`` so the predicate disappears instead of excluding everything.

        Returns:
            Mapping of parameter name to value, covering both fragments. Callers
            pass this to :func:`fetch_all`, which drops whatever the target query
            does not declare.
        """

        def array(values: Sequence[Any] | None) -> list[Any] | None:
            """Normalise a filter sequence to a list, or ``None`` when empty."""
            if values is None:
                return None
            materialised = list(values)
            return materialised or None

        return {
            "country_ids": array(self.country_ids),
            "channel_ids": array(self.channel_ids),
            "persona_ids": array(self.persona_ids),
            "signup_device_ids": array(self.signup_device_ids),
            # Not passed through `array`: False is a meaningful filter value and
            # must survive, where an empty list must not.
            "is_premium": self.is_premium,
            "genre_ids": array(self.genre_ids),
            "content_types": array(self.content_types),
            "languages": array(self.languages),
        }

    def describe(self) -> dict[str, Any]:
        """Return only the active filters, for logging and cache keys.

        Returns:
            Mapping of parameter name to value, omitting every disabled filter.
        """
        return {key: value for key, value in self.as_params().items() if value is not None}


def bind_params(name: str, supplied: dict[str, Any]) -> dict[str, Any]:
    """Reconcile supplied parameters with what a query declares.

    Args:
        name: Query name, e.g. ``"kpi/dau"``.
        supplied: Candidate parameters. Typically a :class:`FilterSet` rendering
            merged with the query's own arguments; extras are expected and
            discarded.

    Returns:
        Exactly the parameters the query declares, with date values coerced.

    Raises:
        QueryNotFoundError: If the query is not registered.
        RuntimeError: If the query declares a parameter the caller did not
            supply, or a date parameter cannot be coerced.
    """
    declared = get_registry().params(name)

    missing = declared - supplied.keys()
    if missing:
        raise RuntimeError(
            f"Query {name!r} declares parameter(s) {sorted(missing)} that were not "
            f"supplied. Supplied: {sorted(supplied)}."
        )

    return {
        key: _coerce_date(key, value) if key in DATE_PARAMS else value
        for key, value in supplied.items()
        if key in declared
    }


async def fetch_all(
    session: AsyncSession,
    name: str,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Execute a named query and return every row as a dictionary.

    The workhorse of this package. Values keep their native PostgreSQL-mapped
    types — :class:`~decimal.Decimal` for money and rates,
    :class:`~datetime.date` for days — because rounding or stringifying money in
    the data layer is a decision the response schemas should make, not this one.

    Args:
        session: A read-only session, normally from :data:`app.db.deps.SessionDep`.
        name: Query name, e.g. ``"retention/retention_nday"``.
        params: Candidate parameters, reconciled by :func:`bind_params`.

    Returns:
        One dictionary per row, in the order the query returned them. Ordering is
        meaningful for most of these queries and is never re-sorted here.

    Raises:
        QueryNotFoundError: If the query is not registered.
        QueryTimeoutError: If the statement exceeded the server-side timeout.
        StaleAnalyticsError: If a materialized view has never been populated.
        DatabaseError: On any other driver failure.
    """
    statement = get_registry().get(name)
    bound = bind_params(name, params or {})

    started = time.perf_counter()
    try:
        result = await session.execute(statement, bound)
        rows = [dict(row) for row in result.mappings().all()]
    except SQLAlchemyError as exc:
        logger.error(
            "query_failed",
            query=name,
            duration_ms=round((time.perf_counter() - started) * 1_000, 1),
            error=str(exc),
            exc_info=True,
        )
        raise translate_db_error(exc) from exc

    duration_ms = round((time.perf_counter() - started) * 1_000, 1)
    # Parameter values are omitted deliberately: filters can carry enough detail
    # to be worth keeping out of a log aggregator. Names are enough to reproduce.
    log = logger.warning if duration_ms >= _SLOW_QUERY_MS else logger.debug
    log("query_executed", query=name, duration_ms=duration_ms, rows=len(rows))

    return rows


async def fetch_one(
    session: AsyncSession,
    name: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Execute a named query and return its first row.

    For the queries that aggregate to a single row — a KPI summary, an
    experiment's headline metrics. Extra rows are ignored rather than treated as
    an error, so a query gaining a grouping column does not start raising.

    Args:
        session: A read-only session.
        name: Query name.
        params: Candidate parameters.

    Returns:
        The first row as a dictionary, or ``None`` if the query returned nothing.

    Raises:
        QueryNotFoundError: If the query is not registered.
        QueryTimeoutError: If the statement exceeded the server-side timeout.
        StaleAnalyticsError: If a materialized view has never been populated.
        DatabaseError: On any other driver failure.
    """
    rows = await fetch_all(session, name, params)
    return rows[0] if rows else None


__all__ = [
    "DATE_PARAMS",
    "FilterSet",
    "bind_params",
    "fetch_all",
    "fetch_one",
]

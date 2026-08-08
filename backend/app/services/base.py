"""Shared machinery for the service layer: filters, windows and caching.

The service layer sits between the routers and the repositories, and it exists to
hold the three concerns that belong to neither. Repositories take integer keys and
:class:`~datetime.date` objects and execute one query. Routers parse HTTP. Between
them something has to turn ``?country=India&country=IN`` into
``country_ids=[7]``, reject a window nobody should be allowed to ask for, and
decide whether the answer can come from cache. That is this module.

Nothing here imports ``fastapi``. Services stay callable from a test, a script or
the seeder, and the HTTP vocabulary — status codes, headers, query strings — lives
only at the edge.

Filters are resolved once, then trusted
---------------------------------------
:func:`resolve_filters` is the single crossing point from human strings to
surrogate keys. It delegates every value to :class:`~app.db.deps.DimensionCatalog`,
which raises :class:`~app.core.exceptions.UnknownDimensionValueError` naming the
valid options. Past this point a :class:`~app.repositories.base.FilterSet` holds
integers that came out of the dimension tables, so no repository re-validates.

One filter is the exception, and it is worth stating plainly rather than hiding:
``languages`` has no dimension table. ``content_filter.sql`` declares
``:languages`` and matches against the language column on ``core.content``, but
there is no ``core.languages`` for the catalogue to load an allowlist from. Those
values are bound as parameters — so they cannot be interpreted as SQL — but a
misspelling produces an empty result rather than a 422. Every other filter is
allowlisted.

Windows are validated, not clamped
----------------------------------
:func:`resolve_window` enforces two rules: the window runs forwards, and it is no
wider than ``PRISM_API__MAX_DATE_RANGE_DAYS``. The second is a real defence — the
events table is partitioned by month, and an unbounded window is a full scan of
all 65 partitions.

It deliberately does *not* clamp to the dataset's actual bounds. Asking for a
window past the end of the seeded data returns empty rows, which is the honest
answer; silently rewriting the caller's dates would make the response describe a
different question from the one asked, and discovering the real bounds would mean
a query this layer has no business issuing.

Caching preserves types, which is harder than it looks
------------------------------------------------------
:func:`cached_rows` is the only way this layer caches. It exists because the two
cache backends disagree about types, and the disagreement is invisible until it
reaches a chart.

:class:`~app.core.cache.LocalCache` stores live Python objects, so a
:class:`~decimal.Decimal` comes back a ``Decimal``. :class:`~app.core.cache.RedisCache`
serialises with ``json.dumps(..., default=str)``, so the same ``Decimal`` comes
back as the string ``"12.34"`` and a :class:`~datetime.date` as ``"2024-01-01"``.
The repository layer takes care to preserve those types; without intervention that
guarantee would hold on a cache miss and break on a hit, and hold under the local
fallback and break under Redis. A response schema would then see a string where it
declared a number, depending on which requests happened to come first.

The fix is a tagged encoding applied before the value ever reaches a backend.
``Decimal("12.34")`` is stored as ``{"__t": "dec", "v": "12.34"}`` and restored on
read, so the round trip is lossless and ``default=str`` becomes unreachable — it
can no longer silently flatten a type, because nothing non-JSON-native is handed
to it.

Both paths decode, including the miss path where the values are already correct.
Encoding and immediately decoding a freshly computed result looks redundant and is
the point: cache hits and misses return values built the same way, so a codec bug
surfaces on the first request rather than on the first hit, and no caller can come
to depend on the richer types a miss would otherwise return.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final, cast

from app.core.cache import build_key, cached
from app.core.config import get_settings
from app.core.exceptions import ValidationError
from app.core.logging import get_logger
from app.repositories.base import FilterSet

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from app.db.deps import DimensionCatalog

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Cache status, reported out of band
# ---------------------------------------------------------------------------

#: Per-request tally of cache lookups, read by the ``X-Cache`` middleware in
#: phase 9. A context variable rather than a return value: threading a hit flag
#: back would mean every one of the fourteen service modules returned a wrapper
#: object instead of a list of rows, and the flag is telemetry, not an answer.
#:
#: Stored as ``(hits, lookups)`` so a composite endpoint that calls four services
#: reports ``HIT`` only when all four hit. ``PARTIAL`` is a truthful third state
#: and the reason the denominator is carried rather than a bare boolean.
_cache_tally: ContextVar[tuple[int, int]] = ContextVar("prism_cache_tally", default=(0, 0))


def reset_cache_status() -> None:
    """Clear the per-request cache tally.

    Called by the middleware at the start of a request. Without it a worker would
    accumulate counts across requests, since a context variable's default only
    applies until something sets it.
    """
    _cache_tally.set((0, 0))


def record_cache_lookup(*, hit: bool) -> None:
    """Record one cache lookup against the current request.

    Args:
        hit: Whether the lookup was served from cache.
    """
    hits, lookups = _cache_tally.get()
    _cache_tally.set((hits + (1 if hit else 0), lookups + 1))


def cache_status() -> str:
    """Summarise cache behaviour for the current request.

    Returns:
        ``"HIT"`` when every lookup hit, ``"MISS"`` when none did, ``"PARTIAL"``
        when a composite endpoint mixed both, and ``"NONE"`` when nothing was
        looked up — an endpoint that does not cache, which is different from one
        that cached and missed.
    """
    hits, lookups = _cache_tally.get()
    if lookups == 0:
        return "NONE"
    if hits == 0:
        return "MISS"
    if hits == lookups:
        return "HIT"
    return "PARTIAL"


# ---------------------------------------------------------------------------
# TTL bands
# ---------------------------------------------------------------------------


class Ttl(StrEnum):
    """Named cache lifetime bands.

    Three bands rather than a number per function, so the policy is legible in one
    place and a service declares intent instead of a magic integer.

    Attributes:
        KPI: Headline numbers users expect to feel live. Shortest lifetime.
        DEFAULT: Everything ordinary.
        HEAVY: Cohort matrices, segment funnels, anything that scans the event
            table. These describe a closed historical window that will not change
            until the next refresh, so a long lifetime costs nothing.
    """

    KPI = "kpi"
    DEFAULT = "default"
    HEAVY = "heavy"

    def seconds(self) -> int:
        """Return this band's configured lifetime.

        Returns:
            Lifetime in seconds, from :class:`~app.core.config.CacheSettings`.
        """
        cache = get_settings().cache
        if self is Ttl.KPI:
            return cache.kpi_ttl_seconds
        if self is Ttl.HEAVY:
            return cache.heavy_ttl_seconds
        return cache.default_ttl_seconds


# ---------------------------------------------------------------------------
# Date windows
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DateWindow:
    """A validated, inclusive reporting window.

    Constructed only by :func:`resolve_window`, so holding one is proof the range
    runs forwards and is within the configured maximum.

    Attributes:
        date_from: First day, inclusive.
        date_to: Last day, inclusive.
    """

    date_from: date
    date_to: date

    @property
    def days(self) -> int:
        """Return the window length in days, counting both endpoints."""
        return (self.date_to - self.date_from).days + 1

    def as_params(self) -> dict[str, Any]:
        """Render the window as bound parameters.

        Returns:
            Mapping with ``date_from`` and ``date_to``. Queries declaring only one
            of the two receive only that one — :func:`app.repositories.base.bind_params`
            discards the rest.
        """
        return {"date_from": self.date_from, "date_to": self.date_to}

    def preceding(self) -> DateWindow:
        """Return the equally long window immediately before this one.

        The comparison basis for period-over-period deltas. Equal length matters:
        comparing a 30-day window against a calendar month would move the delta
        whenever the month had 31 days, which reads as a trend and is an artefact.

        Returns:
            A window of the same length ending the day before :attr:`date_from`.
        """
        end = self.date_from - timedelta(days=1)
        return DateWindow(date_from=end - timedelta(days=self.days - 1), date_to=end)


def resolve_limit(limit: int | None, default: int) -> int:
    """Validate and clamp a row limit.

    Several queries take a ``limit``: content leaderboards, the churn scorecard,
    search. The repository layer passes whatever it is given straight to SQL, so an
    unbounded value would let one request ask for every row in the catalogue. The
    ceiling is ``PRISM_API__MAX_PAGE_SIZE``.

    Clamping rather than rejecting an over-large value is the deliberate choice: a
    caller asking for more than the maximum wants "as many as possible", and a 422
    would be a worse answer than the maximum. A caller asking for zero or a negative
    count, by contrast, has a bug.

    Args:
        limit: Requested row count, or ``None`` to use the default.
        default: The query's own default, from its repository module.

    Returns:
        A positive row count, no greater than the configured maximum.

    Raises:
        ValidationError: If ``limit`` is zero or negative.
    """
    if limit is None:
        return default

    if limit < 1:
        raise ValidationError(
            f"limit must be at least 1, got {limit}.",
            errors=[{"field": "limit", "message": "must be at least 1"}],
        )

    return min(limit, get_settings().api.max_page_size)


def resolve_window(date_from: date, date_to: date) -> DateWindow:
    """Validate a requested reporting window.

    Args:
        date_from: First day, inclusive.
        date_to: Last day, inclusive.

    Returns:
        The validated window.

    Raises:
        ValidationError: If the range runs backwards, or is wider than
            ``PRISM_API__MAX_DATE_RANGE_DAYS``.
    """
    if date_from > date_to:
        raise ValidationError(
            f"date_from ({date_from.isoformat()}) must not be later than "
            f"date_to ({date_to.isoformat()}).",
            errors=[{"field": "date_from", "message": "must not be later than date_to"}],
        )

    window = DateWindow(date_from=date_from, date_to=date_to)
    maximum = get_settings().api.max_date_range_days
    if window.days > maximum:
        raise ValidationError(
            f"The requested window spans {window.days} days, which exceeds the "
            f"{maximum}-day maximum. Narrow the range.",
            errors=[{"field": "date_to", "message": f"window must not exceed {maximum} days"}],
        )

    return window


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FilterRequest:
    """Filter values as they arrive from a caller: human-readable strings.

    The input side of :func:`resolve_filters`. Phase 9's query models build one of
    these; nothing here knows they came over HTTP.

    Attributes:
        countries: Country names or ISO codes. Both forms resolve.
        channels: Marketing channel names.
        personas: Persona names.
        devices: Device names, matched against each user's signup device.
        genres: Genre names.
        content_types: ``core.content_type`` enum labels.
        languages: Catalogue language names. The one field with no allowlist —
            see the module docstring.
        is_premium: Restrict to currently paid or currently unpaid users.
            ``False`` is a real filter; only ``None`` disables it.
    """

    countries: Sequence[str] | None = None
    channels: Sequence[str] | None = None
    personas: Sequence[str] | None = None
    devices: Sequence[str] | None = None
    genres: Sequence[str] | None = None
    content_types: Sequence[str] | None = None
    languages: Sequence[str] | None = None
    is_premium: bool | None = None

    @property
    def is_active(self) -> bool:
        """Return whether this request narrows the population at all.

        Two details a plainer truthiness test would get wrong. ``is_premium=False``
        is a real filter — only ``None`` disables it. And an empty or all-blank
        sequence narrows nothing, because :func:`resolve_filters` discards blanks
        and treats what remains of an empty list as absent; reporting such a request
        as filtered would contradict the :class:`~app.repositories.base.FilterSet`
        it produces.

        Returns:
            ``True`` when at least one filter would reach SQL.
        """
        if self.is_premium is not None:
            return True

        return any(
            any(value and value.strip() for value in values)
            for values in (
                self.countries,
                self.channels,
                self.personas,
                self.devices,
                self.genres,
                self.content_types,
                self.languages,
            )
            if values is not None
        )


#: An unfiltered request. A module-level constant because it is the default for
#: most service calls and the dataclass is immutable, so one instance is enough.
NO_FILTERS: Final[FilterRequest] = FilterRequest()

#: Longest accepted language value. Bound parameters make injection impossible,
#: but an unbounded string still becomes part of a cache key and a log line.
_MAX_LANGUAGE_LENGTH: Final[int] = 64


def resolve_filters(
    request: FilterRequest | None,
    catalog: DimensionCatalog,
) -> FilterSet:
    """Translate a caller's filter strings into bound query parameters.

    Args:
        request: Requested filters. ``None`` means unfiltered.
        catalog: The loaded dimension catalogue.

    Returns:
        A :class:`~app.repositories.base.FilterSet` holding surrogate keys and
        allowlisted literals, safe to pass to any repository function.

    Raises:
        UnknownDimensionValueError: If any value is absent from its dimension
            table, with the valid options named.
        ValidationError: If a language value exceeds
            :data:`_MAX_LANGUAGE_LENGTH` characters.
    """
    if request is None:
        return FilterSet()

    def listed(values: Sequence[str] | None) -> list[str] | None:
        """Normalise a sequence of strings, dropping blanks."""
        if values is None:
            return None
        cleaned = [value.strip() for value in values if value and value.strip()]
        return cleaned or None

    return FilterSet(
        country_ids=catalog.resolve_countries(listed(request.countries)),
        channel_ids=catalog.resolve_channels(listed(request.channels)),
        persona_ids=catalog.resolve_personas(listed(request.personas)),
        signup_device_ids=catalog.resolve_devices(listed(request.devices)),
        is_premium=request.is_premium,
        genre_ids=catalog.resolve_genres(listed(request.genres)),
        content_types=catalog.validate_content_types(listed(request.content_types)),
        languages=_resolve_languages(listed(request.languages)),
    )


def _resolve_languages(values: list[str] | None) -> list[str] | None:
    """Normalise language values, which have no dimension table to check against.

    Args:
        values: Requested language names, already stripped of blanks.

    Returns:
        The values de-duplicated and sorted — sorted so two requests differing
        only in filter order share a cache key — or ``None``.

    Raises:
        ValidationError: If any value is implausibly long.
    """
    if not values:
        return None

    overlong = [value for value in values if len(value) > _MAX_LANGUAGE_LENGTH]
    if overlong:
        raise ValidationError(
            f"language values must be at most {_MAX_LANGUAGE_LENGTH} characters.",
            errors=[{"field": "language", "message": "value too long"}],
        )

    return sorted(set(values))


# ---------------------------------------------------------------------------
# Cache codec
# ---------------------------------------------------------------------------

#: Tag key marking an encoded value. Double-underscored so it cannot collide with
#: a column name from any of the 48 queries.
_TAG: Final[str] = "__t"

#: Value key alongside :data:`_TAG`.
_VALUE: Final[str] = "v"

#: Number of keys an encoded value has: the tag and the payload, nothing else. A
#: mapping with any other size is data, not an encoding.
_TAGGED_KEY_COUNT: Final[int] = 2

_DECIMAL: Final[str] = "dec"
_DATE: Final[str] = "date"
_DATETIME: Final[str] = "dt"
_TIME: Final[str] = "time"
_DELTA: Final[str] = "td"


def encode_for_cache(value: Any) -> Any:  # noqa: PLR0911 — one return per encoded type
    """Convert a value into a losslessly JSON-serialisable form.

    Recurses through lists and mappings, tagging the types PostgreSQL returns
    that JSON has no representation for. Everything else — ``int``, ``float``,
    ``str``, ``bool``, ``None`` — passes through untouched.

    Args:
        value: A query row, a list of rows, or any value inside one.

    Returns:
        A structure containing only JSON-native types.
    """
    # datetime before date: datetime subclasses date, so the looser check first
    # would encode a timestamp as a day and silently drop the time. The same
    # ordering trap as `app.repositories.base._coerce_date`.
    if isinstance(value, Decimal):
        # str, not float: the whole point of a Decimal here is that money and
        # rates survive without binary rounding.
        return {_TAG: _DECIMAL, _VALUE: str(value)}
    if isinstance(value, datetime):
        return {_TAG: _DATETIME, _VALUE: value.isoformat()}
    if isinstance(value, date):
        return {_TAG: _DATE, _VALUE: value.isoformat()}
    if isinstance(value, time):
        return {_TAG: _TIME, _VALUE: value.isoformat()}
    if isinstance(value, timedelta):
        # Postgres intervals reach here as timedelta. Seconds as a string keeps
        # microsecond precision exact, which a float would not for large spans.
        return {_TAG: _DELTA, _VALUE: str(value.total_seconds())}
    if isinstance(value, dict):
        return {key: encode_for_cache(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [encode_for_cache(item) for item in value]
    return value


def decode_from_cache(value: Any) -> Any:
    """Restore a structure produced by :func:`encode_for_cache`.

    Args:
        value: A decoded JSON structure, or a live object from the local cache.

    Returns:
        The value with tagged entries turned back into their Python types.
    """
    if isinstance(value, dict):
        tag = value.get(_TAG)
        # Require the exact two-key shape before treating this as a tagged value,
        # so a query that one day returns a column literally named `__t` is not
        # misread as an encoding.
        if tag is not None and len(value) == _TAGGED_KEY_COUNT and _VALUE in value:
            return _decode_tagged(tag, value[_VALUE])
        return {key: decode_from_cache(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode_from_cache(item) for item in value]
    return value


def _decode_tagged(tag: Any, raw: Any) -> Any:  # noqa: PLR0911 — one return per tag
    """Rebuild one tagged value.

    Args:
        tag: The type tag.
        raw: The encoded payload.

    Returns:
        The reconstructed value, or the raw payload if the tag is unrecognised —
        which can only happen against an entry written by an older process, and is
        better handled as degraded data than as a 500.
    """
    try:
        if tag == _DECIMAL:
            return Decimal(raw)
        if tag == _DATETIME:
            return datetime.fromisoformat(raw)
        if tag == _DATE:
            return date.fromisoformat(raw)
        if tag == _TIME:
            return time.fromisoformat(raw)
        if tag == _DELTA:
            return timedelta(seconds=float(raw))
    except (TypeError, ValueError, ArithmeticError) as exc:
        logger.warning("cache_decode_tag_failed", tag=tag, error=str(exc))
        return raw

    logger.warning("cache_decode_unknown_tag", tag=tag)
    return raw


# ---------------------------------------------------------------------------
# The cache-aside entry point
# ---------------------------------------------------------------------------


async def cached_rows[T](
    namespace: str,
    name: str,
    params: dict[str, Any],
    producer: Callable[[], Awaitable[T]],
    ttl: Ttl = Ttl.DEFAULT,
) -> T:
    """Return a cached result, or compute, store and return it.

    Every caching service function goes through here. The encode/decode round trip
    runs on both paths so a hit and a miss are indistinguishable to the caller —
    see the module docstring for why that is deliberate rather than wasteful.

    Args:
        namespace: Cache namespace, matching the service module — ``"kpi"``,
            ``"cohort"``. Groups keys so one domain can be invalidated alone.
        name: Query or answer name within the namespace, e.g. ``"dau"``.
        params: Everything that makes this answer distinct: window, filters and
            any query-specific arguments. Hashed into the key, so omitting one
            would serve a different filter's numbers.
        producer: Coroutine computing the result on a miss. Normally a repository
            call.
        ttl: Lifetime band.

    Returns:
        Whatever ``producer`` returns, with :class:`~decimal.Decimal` and
        :class:`~datetime.date` values intact regardless of which backend served it.
    """
    key = build_key(namespace, name, params=params)

    async def encoded() -> Any:
        """Compute the result and encode it for storage."""
        return encode_for_cache(await producer())

    payload, was_hit = await cached(key, ttl.seconds(), encoded)
    record_cache_lookup(hit=was_hit)

    # The cast is where the type guarantee is asserted rather than proven. A value
    # that has been through JSON and back is untyped as far as a checker is concerned,
    # so `T` in, `T` out rests on the codec being lossless — which is exactly the
    # property the round trip on both paths exists to keep true, and which is checked
    # against a live Redis and a live local cache rather than assumed.
    return cast("T", decode_from_cache(payload))


__all__ = [
    "NO_FILTERS",
    "DateWindow",
    "FilterRequest",
    "Ttl",
    "cache_status",
    "cached_rows",
    "decode_from_cache",
    "encode_for_cache",
    "record_cache_lookup",
    "reset_cache_status",
    "resolve_filters",
    "resolve_limit",
    "resolve_window",
]

"""Cache-aside layer with graceful degradation.

Analytics answers describe a fixed historical window, so they cache extremely
well: the same filter combination asked twice within a few minutes must return
the same numbers, and recomputing a cohort matrix for each identical request is
pure waste.

Two backends, one interface
---------------------------
:class:`RedisCache` is used when Redis is configured and reachable.
:class:`LocalCache` — a bounded, TTL-aware LRU in process memory — is used
otherwise. The API never fails because a cache is unavailable; that is the whole
point of the cache-aside pattern, and it is why ``docker compose stop redis``
leaves the dashboard working, only slower.

Degradation is one-way within a process lifetime: if Redis is unreachable at
startup, the local cache is installed and no further connection attempts are made
on the hot path. Reconnect-on-every-miss would add latency to exactly the
requests already paying for a cache miss.

Key construction
----------------
Cache keys are built from the query name plus a hash of its bound parameters, so
two requests with the same filters in a different order hit the same entry.
:func:`build_key` sorts and canonicalises before hashing, which matters because
the frontend serialises multi-select filters in whatever order the user clicked
them.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import time
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable

from app.core.config import get_settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger(__name__)

#: Marker distinguishing "cached value is None" from "not in cache". Without it a
#: query that legitimately returns no rows would be recomputed on every request.
_MISS: Final[object] = object()

#: Separator inside cache keys. Colon is the Redis convention and makes
#: `redis-cli --scan --pattern 'prism:v1:kpi:*'` work as expected.
_SEP: Final[str] = ":"


@dataclass(frozen=True, slots=True)
class CacheStats:
    """Counters for the health endpoint and the ``X-Cache`` header.

    Attributes:
        backend: ``"redis"``, ``"local"`` or ``"disabled"``.
        hits: Successful lookups since process start.
        misses: Lookups that had to compute.
        errors: Backend failures that were swallowed.
        entries: Current entry count. Local backend only; ``None`` for Redis,
            where counting keys would mean a ``SCAN`` over a shared database.
    """

    backend: str
    hits: int
    misses: int
    errors: int
    entries: int | None = None

    @property
    def hit_rate(self) -> float:
        """Return the fraction of lookups served from cache.

        Returns:
            Hit rate in ``[0, 1]``; zero when nothing has been looked up yet.
        """
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


@runtime_checkable
class CacheBackend(Protocol):
    """Minimal async cache interface.

    Deliberately narrow: analytics caching needs get, set, invalidate and close.
    Anything richer (counters, sets, pub/sub) would tie the service layer to
    Redis specifically.
    """

    name: str

    async def get(self, key: str) -> Any:
        """Return the cached value, or the sentinel when absent."""
        ...

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        """Store a value under a key with a time to live."""
        ...

    async def delete_prefix(self, prefix: str) -> int:
        """Delete every key beginning with ``prefix``; return the count."""
        ...

    async def close(self) -> None:
        """Release backend resources."""
        ...

    def stats(self) -> CacheStats:
        """Return current counters."""
        ...


class LocalCache:
    """Bounded in-process LRU cache with per-entry TTL.

    The fallback backend, and the only backend when Redis is disabled. Values are
    stored as live Python objects rather than serialised, which makes it faster
    than Redis for a single process and useless across several — an acceptable
    trade for a fallback, and noted in ``docs/decisions.md``.

    Not thread-safe by design. FastAPI runs handlers on one event loop, and an
    ``asyncio.Lock`` around a dictionary operation would cost more than it
    protects.
    """

    name = "local"

    def __init__(self, max_entries: int) -> None:
        """Initialise the cache.

        Args:
            max_entries: Hard ceiling on stored entries. The least recently used
                entry is evicted when exceeded.
        """
        self._max_entries = max_entries
        # OrderedDict gives O(1) LRU: move_to_end on read, popitem(last=False)
        # to evict.
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._hits = 0
        self._misses = 0

    async def get(self, key: str) -> Any:
        """Return the cached value, or :data:`_MISS`.

        Args:
            key: Cache key.

        Returns:
            The stored value, or the miss sentinel if absent or expired.
        """
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return _MISS

        expires_at, value = entry
        if expires_at < time.monotonic():
            # Lazy expiry: cheaper than a sweeper task, and an expired entry
            # occupies a slot only until it is next touched or evicted.
            del self._store[key]
            self._misses += 1
            return _MISS

        self._store.move_to_end(key)
        self._hits += 1
        return value

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        """Store a value, evicting the least recently used entry if full.

        Args:
            key: Cache key.
            value: Value to store. Kept by reference, not copied.
            ttl_seconds: Lifetime. Values of zero or less are not stored.
        """
        if ttl_seconds <= 0:
            return

        self._store[key] = (time.monotonic() + ttl_seconds, value)
        self._store.move_to_end(key)

        while len(self._store) > self._max_entries:
            self._store.popitem(last=False)

    async def delete_prefix(self, prefix: str) -> int:
        """Delete every key with the given prefix.

        Args:
            prefix: Key prefix to match.

        Returns:
            Number of entries removed.
        """
        doomed = [key for key in self._store if key.startswith(prefix)]
        for key in doomed:
            del self._store[key]
        return len(doomed)

    async def close(self) -> None:
        """Drop every entry."""
        self._store.clear()

    def stats(self) -> CacheStats:
        """Return current counters.

        Returns:
            A :class:`CacheStats` snapshot including the entry count.
        """
        return CacheStats(
            backend=self.name,
            hits=self._hits,
            misses=self._misses,
            errors=0,
            entries=len(self._store),
        )


class RedisCache:
    """Redis-backed cache.

    Values are JSON-encoded. That rules out arbitrary Python objects and is
    intentional: everything cached here is a query result already destined for a
    JSON response, and pickle would be both slower and a deserialisation hazard.

    Every operation is wrapped so a Redis failure degrades to a miss rather than
    a 500. A cache that can take the API down is worse than no cache.
    """

    name = "redis"

    def __init__(self, client: Any) -> None:  # noqa: ANN401 - redis.asyncio.Redis
        """Initialise with a connected client.

        Args:
            client: A ``redis.asyncio.Redis`` instance. Typed loosely so this
                module imports cleanly when ``redis`` is not installed.
        """
        self._client = client
        self._hits = 0
        self._misses = 0
        self._errors = 0

    async def get(self, key: str) -> Any:
        """Return the cached value, or :data:`_MISS`.

        Args:
            key: Cache key.

        Returns:
            The decoded value, or the miss sentinel on absence or any failure.
        """
        try:
            raw = await self._client.get(key)
        except Exception as exc:  # noqa: BLE001 - a cache must never propagate
            self._errors += 1
            self._misses += 1
            logger.warning("cache_get_failed", key=key, error=str(exc))
            return _MISS

        if raw is None:
            self._misses += 1
            return _MISS

        try:
            value = json.loads(raw)
        except (TypeError, ValueError) as exc:
            # Corrupt or stale-format entry, e.g. after a serialisation change.
            # Treat as a miss and let it be overwritten.
            self._errors += 1
            self._misses += 1
            logger.warning("cache_decode_failed", key=key, error=str(exc))
            return _MISS

        self._hits += 1
        return value

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        """Store a JSON-encoded value with an expiry.

        Args:
            key: Cache key.
            value: JSON-serialisable value.
            ttl_seconds: Lifetime. Values of zero or less are not stored.
        """
        if ttl_seconds <= 0:
            return

        try:
            payload = json.dumps(value, default=str, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            # Not fatal: skip caching this result rather than failing the request.
            self._errors += 1
            logger.warning("cache_encode_failed", key=key, error=str(exc))
            return

        try:
            await self._client.set(key, payload, ex=ttl_seconds)
        except Exception as exc:  # noqa: BLE001 - a cache must never propagate
            self._errors += 1
            logger.warning("cache_set_failed", key=key, error=str(exc))

    async def delete_prefix(self, prefix: str) -> int:
        """Delete every key with the given prefix.

        Uses ``SCAN`` rather than ``KEYS``: ``KEYS`` blocks the server for the
        duration of the sweep, which is unacceptable even on a cache.

        Args:
            prefix: Key prefix to match.

        Returns:
            Number of keys removed; zero on failure.
        """
        removed = 0
        try:
            async for key in self._client.scan_iter(match=f"{prefix}*", count=500):
                await self._client.delete(key)
                removed += 1
        except Exception as exc:  # noqa: BLE001 - a cache must never propagate
            self._errors += 1
            logger.warning("cache_delete_prefix_failed", prefix=prefix, error=str(exc))
        return removed

    async def close(self) -> None:
        """Close the client connection pool."""
        try:
            await self._client.aclose()
        except Exception as exc:  # noqa: BLE001 - shutdown must not fail
            logger.warning("cache_close_failed", error=str(exc))

    def stats(self) -> CacheStats:
        """Return current counters.

        Returns:
            A :class:`CacheStats` snapshot. ``entries`` is ``None`` because
            counting Redis keys would require a full scan.
        """
        return CacheStats(
            backend=self.name,
            hits=self._hits,
            misses=self._misses,
            errors=self._errors,
            entries=None,
        )


class DisabledCache:
    """No-op backend used when caching is switched off.

    Exists so the service layer has no ``if cache_enabled`` branches. Every miss,
    every set discarded.
    """

    name = "disabled"

    async def get(self, key: str) -> Any:
        """Always report a miss.

        Args:
            key: Ignored.

        Returns:
            The miss sentinel.
        """
        del key
        return _MISS

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        """Discard the value.

        Args:
            key: Ignored.
            value: Ignored.
            ttl_seconds: Ignored.
        """
        del key, value, ttl_seconds

    async def delete_prefix(self, prefix: str) -> int:
        """Report nothing deleted.

        Args:
            prefix: Ignored.

        Returns:
            Zero.
        """
        del prefix
        return 0

    async def close(self) -> None:
        """Do nothing."""

    def stats(self) -> CacheStats:
        """Return zeroed counters.

        Returns:
            A :class:`CacheStats` snapshot with the ``disabled`` backend.
        """
        return CacheStats(backend=self.name, hits=0, misses=0, errors=0, entries=0)


# ---------------------------------------------------------------------------
# Process-wide backend
# ---------------------------------------------------------------------------

_backend: CacheBackend | None = None


async def init_cache() -> CacheBackend:
    """Select and install the cache backend.

    Tries Redis when enabled, falls back to the local LRU on any failure. Called
    once from the FastAPI lifespan.

    Returns:
        The installed backend.
    """
    global _backend  # noqa: PLW0603 - process-wide singleton

    if _backend is not None:
        return _backend

    settings = get_settings()

    if not settings.cache.enabled:
        _backend = DisabledCache()
        logger.info("cache_disabled", reason="PRISM_CACHE__ENABLED is false")
        return _backend

    if settings.redis.enabled:
        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(
                settings.redis.dsn,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=settings.redis.socket_timeout_seconds,
                socket_connect_timeout=settings.redis.socket_timeout_seconds,
                # A cache must never be the reason a request hangs.
                retry_on_timeout=False,
                health_check_interval=30,
            )
            # Verify reachability now: discovering it on the first request would
            # mean one user pays for the timeout.
            await asyncio.wait_for(
                client.ping(), timeout=settings.redis.socket_timeout_seconds
            )
            _backend = RedisCache(client)
            logger.info("cache_ready", backend="redis", host=settings.redis.host)
            return _backend
        except Exception as exc:  # noqa: BLE001 - any failure means fall back
            logger.warning(
                "cache_redis_unavailable",
                host=settings.redis.host,
                error=str(exc),
                fallback="local LRU",
            )

    _backend = LocalCache(max_entries=settings.cache.local_max_entries)
    logger.info(
        "cache_ready", backend="local", max_entries=settings.cache.local_max_entries
    )
    return _backend


async def close_cache() -> None:
    """Release the backend and clear process state. Idempotent."""
    global _backend  # noqa: PLW0603 - process-wide singleton

    if _backend is not None:
        await _backend.close()
        logger.info("cache_closed", backend=_backend.name)
    _backend = None


def get_cache() -> CacheBackend:
    """Return the installed backend.

    Falls back to :class:`DisabledCache` rather than raising if the lifespan has
    not run — a missing cache is never worth failing a request over.

    Returns:
        The active backend.
    """
    if _backend is None:
        return DisabledCache()
    return _backend


# ---------------------------------------------------------------------------
# Key construction and the cache-aside helper
# ---------------------------------------------------------------------------


def build_key(namespace: str, *parts: str, params: dict[str, Any] | None = None) -> str:
    """Build a deterministic cache key.

    Parameters are canonicalised before hashing so that logically identical
    requests share an entry regardless of key order or the order in which the
    frontend serialised a multi-select filter.

    Args:
        namespace: Logical group, e.g. ``"kpi"`` or ``"cohort"``. Becomes a key
            prefix so a group can be invalidated on its own.
        *parts: Additional literal segments, typically the query name.
        params: Bound query parameters. Hashed rather than embedded, keeping keys
            short and avoiding illegal characters.

    Returns:
        A key of the form ``prism:v1:kpi:dau:a1b2c3d4e5f6g7h8``.
    """
    settings = get_settings()
    segments = [settings.cache.key_namespace, namespace, *parts]

    if params:
        # sort_keys canonicalises mapping order; sorting list values canonicalises
        # multi-select order. default=str handles date and Decimal.
        canonical = json.dumps(
            {
                key: sorted(value) if isinstance(value, (list, set, tuple)) else value
                for key, value in params.items()
                if value is not None
            },
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        # 16 hex chars of BLAKE2b. Not cryptographic — this is a cache key, and
        # 64 bits makes an accidental collision irrelevant at this scale.
        digest = hashlib.blake2b(canonical.encode(), digest_size=8).hexdigest()
        segments.append(digest)

    return _SEP.join(segments)


def namespace_prefix(namespace: str) -> str:
    """Return the key prefix for a namespace.

    Args:
        namespace: Logical group name.

    Returns:
        The prefix used by :meth:`CacheBackend.delete_prefix`.
    """
    settings = get_settings()
    return f"{settings.cache.key_namespace}{_SEP}{namespace}{_SEP}"


async def cached(
    key: str,
    ttl_seconds: int,
    producer: Callable[[], Awaitable[Any]],
) -> tuple[Any, bool]:
    """Return a cached value or compute and store it.

    The single cache-aside entry point for the service layer. Returning the
    hit flag alongside the value is what lets the router set ``X-Cache``, which
    in turn makes the caching visible to anyone poking at the API — worth more
    than a line in the README.

    Args:
        key: Cache key from :func:`build_key`.
        ttl_seconds: Lifetime for a freshly computed value.
        producer: Coroutine computing the value on a miss.

    Returns:
        ``(value, was_hit)``.
    """
    backend = get_cache()

    value = await backend.get(key)
    if value is not _MISS:
        return value, True

    value = await producer()
    await backend.set(key, value, ttl_seconds)
    return value, False


async def invalidate(namespace: str | None = None) -> int:
    """Drop cached entries.

    Called by ``POST /admin/refresh`` after the materialized views are rebuilt:
    the underlying data has changed, so every cached answer derived from it is
    now wrong.

    Args:
        namespace: Group to clear. Clears everything under the configured
            namespace when omitted.

    Returns:
        Number of entries removed.
    """
    settings = get_settings()
    backend = get_cache()
    prefix = (
        namespace_prefix(namespace)
        if namespace
        else f"{settings.cache.key_namespace}{_SEP}"
    )
    removed = await backend.delete_prefix(prefix)
    logger.info("cache_invalidated", prefix=prefix, entries=removed)
    return removed


__all__ = [
    "CacheBackend",
    "CacheStats",
    "DisabledCache",
    "LocalCache",
    "RedisCache",
    "build_key",
    "cached",
    "close_cache",
    "get_cache",
    "init_cache",
    "invalidate",
    "namespace_prefix",
]

"""Global search: one ranked result set across titles, users, sessions and genres.

Wraps :mod:`app.repositories.search`. The odd one out in this package — it takes no
date range, no catalogue and no filters. Search is a navigation aid over the whole
dataset, and quietly restricting it to the active dashboard filters would make
results appear and disappear for reasons the user cannot see.

Two service-layer concerns sit on top of the repository.

The term is trimmed and length-checked here, before the cache is consulted, so a
term too short to be useful returns 422 regardless of what is cached. The repository
performs the same check; keeping it there means a direct caller — a script, a test —
is still guarded, and the duplication costs one comparison.

Trimming before the key is built is what makes ``"shadow"`` and ``" shadow "`` share
a cache entry. That normalisation is safe *only* because the SQL applies ``trim()``
to every use of the term, so it provably cannot change the result set. Case is left
alone even though the query happens to match case-insensitively today: lowercasing
would be a second, unenforced assumption about the SQL, and the payoff is a slightly
better hit rate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.exceptions import ValidationError
from app.repositories import search as repo
from app.repositories.search import DEFAULT_LIMIT, MIN_QUERY_LENGTH
from app.services.base import Ttl, cached_rows, resolve_limit

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

#: Cache namespace for this module.
NAMESPACE = "search"


async def search(
    session: AsyncSession,
    query: str,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Search titles, users, sessions and genres in one ranked result set.

    Args:
        session: A read-only session.
        query: Search term. Surrounding whitespace is ignored.
        limit: Maximum results across all types combined. Defaults to
            :data:`app.repositories.search.DEFAULT_LIMIT`, which is sized for a
            command palette rather than a page; clamped to the configured maximum
            page size.

    Returns:
        The rows from :func:`app.repositories.search.search`, unchanged — ordered by
        score descending, then by type and label.

    Raises:
        ValidationError: If the trimmed term is shorter than
            :data:`app.repositories.search.MIN_QUERY_LENGTH`, or if ``limit`` is
            below 1.
    """
    term = query.strip()
    if len(term) < MIN_QUERY_LENGTH:
        raise ValidationError(f"Search term must be at least {MIN_QUERY_LENGTH} characters.")

    rows = resolve_limit(limit, DEFAULT_LIMIT)

    return await cached_rows(
        NAMESPACE,
        "global",
        {"query": term, "limit": rows},
        lambda: repo.search(session, term, rows),
        ttl=Ttl.DEFAULT,
    )


__all__ = [
    "DEFAULT_LIMIT",
    "MIN_QUERY_LENGTH",
    "NAMESPACE",
    "search",
]

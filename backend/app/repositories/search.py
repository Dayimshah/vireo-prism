"""Global search across titles, users, sessions and genres.

One query backing the command palette. A single ``UNION`` rather than four
endpoints, so one keystroke queries everything and the frontend manages one loading
state instead of four.

Ranked, not merely matched
--------------------------
Results carry a ``score`` and arrive sorted by it. An exact title match scores 1.00,
a prefix match 0.90, and everything below that is a trigram similarity capped at
0.85 — so a typo still finds its target but never outranks an exact hit. Anything
scoring under 0.10 is discarded as noise rather than padding the result list.

Title matching relies on the ``pg_trgm`` GIN index from Alembic revision 0005. That
index is the reason an unanchored ``ILIKE '%shadow%'`` is fast; a btree cannot serve
a leading-wildcard pattern, and without trigrams every keystroke would sequentially
scan the catalogue.

Numeric terms are treated as possible identifiers as well as text, because someone
pasting a user id into a search box expects to find that user. The SQL guards the
integer comparison behind a ``~ '^[0-9]+$'`` test, so a non-numeric term never
reaches an integer cast — which would raise rather than simply not match.

No filters, and no user-scope parameters: search is a navigation aid over the whole
dataset, and silently restricting it to the active dashboard filters would make
results appear and disappear for reasons the user cannot see.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from app.core.exceptions import ValidationError
from app.repositories.base import fetch_all

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

#: Default number of results. Sized for a command palette rather than a page.
DEFAULT_LIMIT: Final[int] = 20

#: Shortest accepted term. A single character matches most of the catalogue through
#: trigram similarity, which costs a full scan to return nothing useful.
MIN_QUERY_LENGTH: Final[int] = 2


async def search(
    session: AsyncSession,
    query: str,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Search titles, users, sessions and genres in one ranked result set.

    Args:
        session: A read-only session.
        query: Search term. Bound as a parameter; matching is case-insensitive and
            surrounding whitespace is ignored.
        limit: Maximum results to return across all types combined.

    Returns:
        Rows ordered by score descending, then by type and label, with keys
        ``result_type`` (``'content'``, ``'user'``, ``'session'`` or ``'genre'``),
        ``result_id``, ``label``, ``sublabel`` and ``score``.

        Users and sessions are only ever matched by numeric id. Matching them on
        persona or country text would return thousands of rows for a term like
        "india" and bury the useful results.

    Raises:
        ValidationError: If the term is shorter than :data:`MIN_QUERY_LENGTH` after
            trimming.
    """
    term = query.strip()
    if len(term) < MIN_QUERY_LENGTH:
        raise ValidationError(f"Search term must be at least {MIN_QUERY_LENGTH} characters.")

    return await fetch_all(
        session,
        "search/global_search_union",
        {"query": term, "limit": limit},
    )


__all__ = [
    "DEFAULT_LIMIT",
    "MIN_QUERY_LENGTH",
    "search",
]

"""Load, compose and serve the named analytics queries.

Every line of SQL in this project lives in a ``.sql`` file under
``app/sql/queries/``. Nothing is built by string concatenation at request time,
and no repository holds a query inline. Two reasons this matters more than it
looks:

**Reviewability.** A reviewer can read ``queries/retention/retention_nday.sql``
as SQL, paste it into psql, and check it. That is not true of a query assembled
from six f-strings across a service and a repository.

**Injection surface.** Query text is read from disk at startup and never varies
per request. The only request-derived values are bound parameters. The single
exception — a dynamic ``ORDER BY``, which PostgreSQL will not accept as a
parameter — goes through :func:`app.core.security.build_order_by` against an
explicit column allowlist.

Fragment includes
-----------------
Roughly forty of the queries need the same user-scope filter block. Repeating it
forty times would be forty places to fix a bug, so files may reference a shared
fragment::

    WHERE e.event_time >= :date_from
      {{user_filter}}

Fragments live in ``app/sql/fragments/`` and are spliced in at load time, once.
The composed text is what gets executed and what gets logged, so the indirection
costs nothing at runtime and an unresolved placeholder fails at startup rather
than on the request that happens to need it.

Optional filters
----------------
Every filter is optional, and the same predicate must work with the filter absent.
The pattern used throughout is::

    AND (:country_ids::int[] IS NULL OR u.country_id = ANY(:country_ids::int[]))

Passing ``None`` makes the left side true and the filter disappears; passing a
list applies it. The explicit ``::int[]`` cast is required — asyncpg cannot infer
the type of a bare parameter in an ``ANY`` position, and omitting it produces a
runtime type error rather than a wrong answer.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import TYPE_CHECKING, Final

from sqlalchemy import text

from app.core.config import SQL_QUERIES_DIR
from app.core.exceptions import QueryNotFoundError
from app.core.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.sql.elements import TextClause

logger = get_logger(__name__)

#: Directory holding reusable SQL fragments, sibling to ``queries/``.
SQL_FRAGMENTS_DIR: Final[Path] = SQL_QUERIES_DIR.parent / "fragments"

#: Matches ``{{fragment_name}}`` include directives.
_INCLUDE_RE: Final[re.Pattern[str]] = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")

#: Matches a bound parameter. This is SQLAlchemy's own ``BIND_PARAMS`` pattern,
#: copied deliberately rather than approximated.
#:
#: The trailing ``(?![:\w$])`` is the part that matters. It means SQLAlchemy does
#: **not** treat ``:date_from`` as a parameter in ``:date_from::date``, because the
#: name is immediately followed by a colon — so the whole expression is passed to
#: PostgreSQL as literal text and fails with "syntax error at or near :".
#:
#: An earlier version of this regex omitted that lookahead. It therefore reported
#: parameters that SQLAlchemy would never bind, and every query using
#: ``:param::type`` shorthand was broken while the registry claimed it was fine. All
#: casts are now written ``CAST(:param AS type)``; keeping the two patterns
#: identical is what guarantees ``params()`` reflects reality, and
#: ``tests/test_sql_registry.py`` asserts the agreement.
_PARAM_RE: Final[re.Pattern[str]] = re.compile(r"(?<![:\w$]):([\w$]+)(?![:\w$])", re.UNICODE)

#: Maximum include depth. Fragments may include fragments, but a cycle must fail
#: loudly rather than recurse until the stack gives out.
_MAX_INCLUDE_DEPTH: Final[int] = 4


class SqlRegistry:
    """An immutable, eagerly loaded catalogue of named queries.

    Loading everything at startup is deliberate. A missing file, a syntax error in
    an include, or a typo in a fragment name becomes a boot failure with a clear
    message, instead of a 500 on whichever page happens to use that query.

    Names are ``namespace/query``, mirroring the directory layout::

        kpi/dau
        retention/retention_nday
        experiments/experiment_variant_metrics
    """

    __slots__ = ("_fragments", "_params", "_queries", "_statements")

    def __init__(self) -> None:
        """Create an empty registry. Call :meth:`load` to populate it."""
        self._fragments: dict[str, str] = {}
        self._queries: dict[str, str] = {}
        self._statements: dict[str, TextClause] = {}
        self._params: dict[str, frozenset[str]] = {}

    # -- loading -------------------------------------------------------------

    def load(self) -> SqlRegistry:
        """Read every fragment and query from disk.

        Returns:
            This registry, for chaining.

        Raises:
            RuntimeError: If the queries directory is missing, an include cannot be
                resolved, or two files would occupy the same name.
        """
        if not SQL_QUERIES_DIR.is_dir():
            raise RuntimeError(
                f"SQL queries directory not found: {SQL_QUERIES_DIR}. "
                "The analytics layer cannot start without it."
            )

        self._load_fragments()
        self._load_queries()

        logger.info(
            "sql_registry_loaded",
            queries=len(self._queries),
            fragments=len(self._fragments),
            namespaces=sorted({name.split("/", 1)[0] for name in self._queries}),
        )
        return self

    def _load_fragments(self) -> None:
        """Read the shared fragment files.

        Fragments are optional: a project with no shared filter blocks is valid.
        """
        if not SQL_FRAGMENTS_DIR.is_dir():
            return

        for path in sorted(SQL_FRAGMENTS_DIR.glob("*.sql")):
            self._fragments[path.stem] = path.read_text(encoding="utf-8").strip()

    def _load_queries(self) -> None:
        """Read and compose every query file.

        Raises:
            RuntimeError: On a duplicate name or an unresolvable include.
        """
        for path in sorted(SQL_QUERIES_DIR.rglob("*.sql")):
            relative = path.relative_to(SQL_QUERIES_DIR)
            # queries/kpi/dau.sql -> "kpi/dau"
            name = "/".join([*relative.parts[:-1], path.stem])

            if name in self._queries:
                raise RuntimeError(f"Duplicate SQL query name: {name!r} ({path})")

            composed = self._compose(path.read_text(encoding="utf-8"), origin=name)
            self._queries[name] = composed
            self._statements[name] = text(composed)
            self._params[name] = frozenset(_PARAM_RE.findall(composed))

    def _compose(self, sql: str, *, origin: str, depth: int = 0) -> str:
        """Resolve ``{{fragment}}`` includes.

        Args:
            sql: Raw file contents.
            origin: Query name, for error messages.
            depth: Current recursion depth.

        Returns:
            SQL with every include substituted.

        Raises:
            RuntimeError: On an unknown fragment or an include cycle.
        """
        if depth > _MAX_INCLUDE_DEPTH:
            raise RuntimeError(
                f"SQL include depth exceeded in {origin!r}. A fragment cycle is the "
                "usual cause."
            )

        def substitute(match: re.Match[str]) -> str:
            fragment = match.group(1)
            if fragment not in self._fragments:
                available = ", ".join(sorted(self._fragments)) or "(none)"
                raise RuntimeError(
                    f"Unknown SQL fragment {{{{{fragment}}}}} referenced by {origin!r}. "
                    f"Available fragments: {available}."
                )
            return self._fragments[fragment]

        composed = _INCLUDE_RE.sub(substitute, sql)

        # A fragment may itself include another; recurse until stable.
        if _INCLUDE_RE.search(composed):
            return self._compose(composed, origin=origin, depth=depth + 1)

        return composed

    # -- access --------------------------------------------------------------

    def get(self, name: str) -> TextClause:
        """Return a query as an executable statement.

        Args:
            name: Query name, e.g. ``"kpi/dau"``.

        Returns:
            A cached :class:`~sqlalchemy.sql.elements.TextClause`.

        Raises:
            QueryNotFoundError: If no such query is registered.
        """
        statement = self._statements.get(name)
        if statement is None:
            raise QueryNotFoundError(name, available=len(self._statements))
        return statement

    def raw(self, name: str) -> str:
        """Return a query's composed SQL text.

        For ``EXPLAIN``, for the tests, and for the documentation generator.

        Args:
            name: Query name.

        Returns:
            The composed SQL.

        Raises:
            QueryNotFoundError: If no such query is registered.
        """
        sql = self._queries.get(name)
        if sql is None:
            raise QueryNotFoundError(name, available=len(self._queries))
        return sql

    def params(self, name: str) -> frozenset[str]:
        """Return the bound parameter names a query expects.

        Args:
            name: Query name.

        Returns:
            Parameter names, without the leading colon.

        Raises:
            QueryNotFoundError: If no such query is registered.
        """
        params = self._params.get(name)
        if params is None:
            raise QueryNotFoundError(name, available=len(self._params))
        return params

    def names(self, namespace: str | None = None) -> list[str]:
        """Return registered query names.

        Args:
            namespace: Restrict to one namespace, e.g. ``"kpi"``.

        Returns:
            Sorted names.
        """
        if namespace is None:
            return sorted(self._queries)
        prefix = f"{namespace}/"
        return sorted(name for name in self._queries if name.startswith(prefix))

    def __len__(self) -> int:
        """Return the number of registered queries."""
        return len(self._queries)

    def __contains__(self, name: object) -> bool:
        """Return whether a name is registered."""
        return name in self._queries


#: Process-wide registry. Populated by :func:`init_registry` during startup.
_registry: Final[SqlRegistry] = SqlRegistry()

_loaded = False


def init_registry() -> SqlRegistry:
    """Load the registry once per process.

    Called from the FastAPI lifespan and from the test fixtures.

    Returns:
        The loaded registry.
    """
    global _loaded  # noqa: PLW0603 - one-time load guard
    if not _loaded:
        _registry.load()
        _loaded = True
    return _registry


def get_registry() -> SqlRegistry:
    """Return the process-wide registry, loading it on first use.

    Returns:
        The loaded registry.
    """
    return init_registry()


__all__ = [
    "SQL_FRAGMENTS_DIR",
    "SqlRegistry",
    "get_registry",
    "init_registry",
]

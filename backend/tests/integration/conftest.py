"""Fixtures for the tests that need a live, seeded database.

Opting in
---------
Every test in this directory is marked ``integration`` and skips unless
``PRISM_TEST_DB`` is set, matching the contract already documented in
``tests/conftest.py`` and in the marker description.

The variable is an **opt-in switch first and a DSN second**. If its value looks
like a DSN — it contains ``://`` — that database is used. Any other truthy value
means *use the database this process is already configured for*, read from
``Settings``. That second form exists so a container run needs only
``-e PRISM_TEST_DB=1``: compose has already injected the credentials from
``.env``, so nothing has to restate a password on a command line where it would
land in shell history and process listings.

Why one connection per query
----------------------------
Phase 6 built a verification harness that ran all the queries inside a single
transaction. The first genuine failure aborted that transaction, and the
remaining calls then failed with ``InFailedSQLTransactionError`` — 47 fabricated
failures burying the one real one, and every message pointing at the wrong
query. :func:`fetch` therefore opens its own connection per call. At these
volumes the cost is irrelevant and the diagnostic value is the whole point.

Why NullPool
------------
``pytest-asyncio`` is configured with a function-scoped event loop, so each test
runs on a fresh loop. An asyncpg connection is bound to the loop that created it,
and a pooled connection reused across two loops raises "attached to a different
loop" — which reads as an application bug and is not one. ``NullPool`` holds
nothing between checkouts, so a session-scoped engine stays safe.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import date
import os
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.repositories.base import FilterSet, bind_params
from app.sql.registry import SqlRegistry, init_registry

#: Every parameter name declared anywhere in the SQL, with a value that returns
#: rows against the seeded dataset. A generic executor needs the whole union: a
#: query is only exercised if all of its binds are supplied, and a missing one
#: raises out of ``bind_params`` rather than reaching the driver.
#:
#: ``min_cohort_size`` is 1 rather than the API's default of 30 deliberately.
#: Several cohort queries return zero rows at 30 against a 600-user dataset, and
#: a test that asserted "some rows came back" would then fail for a reason that
#: is not a defect. Where the floor itself is the subject, it is passed
#: explicitly.
GENERIC_PARAMS: dict[str, Any] = {
    "limit": 50,
    "max_months": 12,
    "max_weeks": 12,
    "min_cohort_size": 1,
    "min_risk_score": 0,
    "min_starts": 1,
    # 'persona' is the one token present in *both* `segment_by` vocabularies —
    # retention offers country/channel/persona/device/premium while the funnel
    # offers country/channel/persona/form_factor/platform. Anything else would
    # fall through one query's CASE to `ELSE 'all'` and collapse it to a single
    # row, which reads as a pass. The vocabularies are pinned per query in
    # test_queries_execute.py rather than trusted to this default.
    "segment_by": "persona",
    "query": "a",
    "experiment_key": "paywall-copy-value-first",
}


def _async_dsn() -> str:
    """Return the SQLAlchemy asyncpg DSN for the test database.

    Raises:
        pytest.skip.Exception: If ``PRISM_TEST_DB`` is unset.
    """
    flag = os.environ.get("PRISM_TEST_DB")
    if not flag:
        pytest.skip("PRISM_TEST_DB is not set; skipping integration test")

    if "://" not in flag:
        # The opt-in form: use whatever this process is configured for, so no
        # credential is ever restated on a command line.
        return get_settings().db.async_dsn

    # A DSN was given. Normalise the scheme: `postgresql://` is what libpq and
    # every connection-string example use, and SQLAlchemy needs the driver
    # marker to pick asyncpg rather than the default sync DBAPI.
    if flag.startswith("postgresql+"):
        return flag
    return flag.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
        "postgres://", "postgresql+asyncpg://", 1
    )


@pytest.fixture(scope="session")
def registry() -> SqlRegistry:
    """Load the SQL registry once for the whole tier.

    The process-wide registry is populated during app startup, which does not
    happen under pytest.
    """
    return init_registry()


@pytest.fixture(scope="session")
def pg_engine() -> AsyncEngine:
    """Return an engine for the test database.

    Not disposed in a finalizer: ``NullPool`` leaves nothing to close, and a
    teardown that awaited ``dispose`` would need an event loop that has already
    gone by the time a session-scoped fixture unwinds.
    """
    return create_async_engine(
        _async_dsn(),
        poolclass=NullPool,
        future=True,
        connect_args={
            # Mirrors `app.db.session.create_engine`. The event table is
            # partitioned, and a cached plan can be invalidated by a partition
            # change, raising InvalidCachedStatementError on a valid query.
            "statement_cache_size": 0,
        },
    )


Fetch = Callable[..., Coroutine[Any, Any, list[dict[str, Any]]]]


@pytest.fixture
def fetch(pg_engine: AsyncEngine, registry: SqlRegistry) -> Fetch:
    """Return an async callable that runs one registered query and returns rows.

    One connection per call, for the reason in the module docstring. Parameters
    default to :data:`GENERIC_PARAMS` plus an inactive filter set, and any
    keyword overrides them — so a test states only what it actually varies.
    """

    async def run(name: str, **overrides: Any) -> list[dict[str, Any]]:
        supplied: dict[str, Any] = {
            **FilterSet().as_params(),
            **GENERIC_PARAMS,
            **overrides,
        }
        statement = registry.get(name)
        params = bind_params(name, supplied)

        async with pg_engine.connect() as conn:
            result = await conn.execute(statement, params)
            return [dict(row) for row in result.mappings()]

    return run


@pytest.fixture
async def bounds(pg_engine: AsyncEngine, registry: SqlRegistry) -> dict[str, Any]:
    """Return the dataset's activity bounds, read from the API's own query.

    Read rather than hardcoded: the seed is regenerable, and a test suite that
    embedded 2025-02-07 would start failing on a reseed for no real reason. The
    window every other test uses is derived from this.

    Function-scoped despite being the same answer every time. ``pytest-asyncio``
    is configured with ``asyncio_default_fixture_loop_scope = "function"``, so a
    session-scoped *async* fixture would be requesting a narrower loop than its
    own scope — which pytest-asyncio reports as a ``DeprecationWarning``, and
    ``filterwarnings = ["error::DeprecationWarning"]`` promotes that to a
    failure. One trivial aggregate over a small matview per test is cheaper than
    the workaround.
    """
    statement = registry.get("meta/activity_bounds")
    async with pg_engine.connect() as conn:
        rows = [dict(row) for row in (await conn.execute(statement)).mappings()]

    assert rows, "meta/activity_bounds returned nothing; is the database seeded?"
    row = rows[0]
    assert isinstance(row["first_activity_date"], date), (
        "no activity bounds — the analytics matviews look unpopulated. "
        "Run `make refresh` or POST /admin/refresh-analytics."
    )
    return row


@pytest.fixture
def window(bounds: dict[str, Any]) -> dict[str, date]:
    """Return the full dataset window as query parameters.

    The widest honest window: narrower ones make "zero rows" ambiguous between a
    broken query and a quiet period, and this tier exists to tell those apart.
    """
    return {
        "date_from": bounds["first_activity_date"],
        "date_to": bounds["last_activity_date"],
        "observation_end": bounds["last_activity_date"],
    }

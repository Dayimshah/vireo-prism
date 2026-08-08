"""Shared pytest fixtures.

Phase 12 of the build plan fills this directory out. What is here now is the
minimum the container image needs to exist, plus the fixtures that are genuinely
useful before the API layer is written.

Test tiers
----------
Most tests are pure unit tests over the generator and the statistics helpers, and
need no database. The few that do are marked ``integration`` and skip unless
``PRISM_TEST_DB`` names a reachable database, so ``pytest`` stays fast and
runnable on a machine with no Postgres.
"""

from __future__ import annotations

from collections.abc import Iterator
import os
import random
from typing import TYPE_CHECKING

import pytest

from app.core.config import Settings, get_settings

if TYPE_CHECKING:
    pass


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Return the process settings.

    Returns:
        The validated settings singleton.
    """
    return get_settings()


@pytest.fixture
def rng() -> random.Random:
    """Return a seeded random source.

    A fixed seed here rather than a per-test one, so a failing assertion about
    generated data reproduces exactly on the next run.

    Returns:
        A :class:`random.Random` seeded with a constant.
    """
    return random.Random(20_240_817)


@pytest.fixture(scope="session")
def integration_dsn() -> Iterator[str]:
    """Yield a DSN for tests that need a live database.

    Skips the test rather than failing it when ``PRISM_TEST_DB`` is unset, which
    keeps the default ``pytest`` run green on a machine without Postgres.

    Yields:
        A libpq connection string.
    """
    dsn = os.environ.get("PRISM_TEST_DB")
    if not dsn:
        pytest.skip("PRISM_TEST_DB is not set; skipping integration test")
    yield dsn

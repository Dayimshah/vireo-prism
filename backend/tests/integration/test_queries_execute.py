"""Every registered query executes against a real seeded database.

What this tier adds over the unit tests
--------------------------------------
``tests/unit/test_registry.py`` proves the SQL parses, composes, declares the
parameters it uses, and avoids the ``:param::type`` cast trap. None of that
requires a server, and none of it proves a query *runs*: a wrong column name, a
type mismatch asyncpg refuses to infer, or a join against a matview that was
created ``WITH NO DATA`` all pass every unit test and fail on the first request.

This file closes that gap for all of them at once. Parametrized per query, so a
failure names the query rather than reporting "the suite is red".

One connection per query
-----------------------
See ``conftest.py``. The short version: a shared transaction turns the first real
failure into N fabricated ones and hides which query actually broke.

Segment vocabularies are pinned, not guessed
-------------------------------------------
Two queries resolve a ``:segment_by`` parameter through a ``CASE`` with an
``ELSE 'all'`` fallback, and **the two vocabularies are not the same** —
retention offers ``device`` where the funnel offers ``form_factor`` and
``platform``. A token from the wrong list does not error; it falls through to
``'all'`` and collapses the result to one bucket. That is the failure mode this
file is most useful against, because it looks exactly like success.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from app.sql.registry import init_registry

if TYPE_CHECKING:
    from tests.integration.conftest import Fetch

pytestmark = pytest.mark.integration

#: Loaded at import so the parametrization below can enumerate query names. This
#: reads files on disk and needs no database, so it is safe at collection time
#: even when the whole tier is about to skip.
_NAMES: list[str] = init_registry().names()

#: The full ``CASE`` vocabulary of ``retention/retention_by_segment.sql``.
RETENTION_SEGMENTS = ("country", "channel", "persona", "device", "premium")

#: The full ``CASE`` vocabulary of ``funnel/funnel_by_segment.sql``. Note
#: ``form_factor``/``platform`` where retention has ``device`` — the funnel
#: segments by the *session's* device, which carries both attributes.
FUNNEL_SEGMENTS = ("country", "channel", "persona", "form_factor", "platform", "premium")


@pytest.mark.parametrize("name", _NAMES)
async def test_every_registered_query_executes(
    name: str, fetch: Fetch, window: dict[str, Any]
) -> None:
    """The query runs and returns rows shaped as mappings.

    No claim about row *counts* here — several cohort queries legitimately return
    nothing at a high enough ``min_cohort_size``, and conflating "empty" with
    "broken" is what makes a suite like this untrustworthy. Emptiness is
    addressed separately below.
    """
    rows = await fetch(name, **window)

    assert isinstance(rows, list), name
    for row in rows[:5]:
        assert isinstance(row, dict), name
        assert row, f"{name} returned a row with no columns"


@pytest.mark.parametrize("name", _NAMES)
async def test_every_query_is_repeatable(name: str, fetch: Fetch, window: dict[str, Any]) -> None:
    """Running the same query twice returns the same number of rows.

    These are all pure reads over a static dataset, so anything else means either
    non-determinism in an ``ORDER BY`` that reaches a ``LIMIT``, or a query whose
    result depends on wall-clock time. The second would make every chart shift
    under a user for no reason they could see.
    """
    first = await fetch(name, **window)
    second = await fetch(name, **window)
    assert len(first) == len(second), name


@pytest.mark.parametrize("segment_by", RETENTION_SEGMENTS)
async def test_retention_by_segment_accepts_every_token_in_its_vocabulary(
    segment_by: str, fetch: Fetch, window: dict[str, Any]
) -> None:
    """Each declared dimension resolves to a real column, not to the fallback.

    ``min_cohort_size=1`` so this tests the ``CASE`` rather than the floor: at the
    API's default of 30 several dimensions return nothing against a 600-user
    dataset, which is a documented property of the data and not of the SQL.
    """
    rows = await fetch(
        "retention/retention_by_segment",
        segment_by=segment_by,
        min_cohort_size=1,
        **window,
    )

    segments = {row["segment"] for row in rows}
    assert segments, f"no rows for segment_by={segment_by!r}"
    # The tell-tale of a token that missed the CASE: everything lands in 'all'.
    assert segments != {"all"}, (
        f"segment_by={segment_by!r} fell through to the ELSE branch — it is not "
        "in this query's CASE vocabulary"
    )


@pytest.mark.parametrize("segment_by", FUNNEL_SEGMENTS)
async def test_funnel_by_segment_accepts_every_token_in_its_vocabulary(
    segment_by: str, fetch: Fetch, window: dict[str, Any]
) -> None:
    """The same check against the funnel's own, different, vocabulary."""
    rows = await fetch("funnel/funnel_by_segment", segment_by=segment_by, **window)

    segments = {row["segment"] for row in rows}
    assert segments, f"no rows for segment_by={segment_by!r}"
    assert segments != {"all"}, f"segment_by={segment_by!r} fell through to the ELSE branch"


async def test_the_two_segment_vocabularies_really_do_differ(
    fetch: Fetch, window: dict[str, Any]
) -> None:
    """Pins the asymmetry, so nobody 'tidies' one list to match the other.

    ``device`` is valid for retention and *not* for the funnel. If a future edit
    unified them, the parametrized tests above would still pass while this one
    fails and says why.
    """
    assert "device" in RETENTION_SEGMENTS
    assert "device" not in FUNNEL_SEGMENTS

    # And the behaviour, not just the constants: 'device' silently degrades here.
    rows = await fetch("funnel/funnel_by_segment", segment_by="device", **window)
    assert {row["segment"] for row in rows} == {"all"}


async def test_an_unrecognised_segment_token_degrades_rather_than_erroring(
    fetch: Fetch, window: dict[str, Any]
) -> None:
    """Documents the ``ELSE 'all'`` fallback as deliberate.

    A bound parameter cannot be validated by Postgres, so the ``CASE`` needs some
    default. Collapsing to a single bucket is the chosen one: the request still
    answers, and the response visibly says ``all``. The request schemas restrict
    the parameter to the vocabulary before it ever reaches here.
    """
    rows = await fetch(
        "retention/retention_by_segment",
        segment_by="no-such-dimension",
        min_cohort_size=1,
        **window,
    )
    assert {row["segment"] for row in rows} == {"all"}


async def test_the_search_union_respects_its_limit(fetch: Fetch) -> None:
    """``limit`` is a real bind, and the union honours it.

    Worth an explicit test because a ``LIMIT`` applied to only one leg of a
    ``UNION ALL`` still looks correct on a small dataset.
    """
    rows = await fetch("search/global_search_union", query="a", limit=3)
    assert len(rows) <= 3


@pytest.mark.parametrize("name", ["meta/activity_bounds", "meta/dataset_counts"])
async def test_the_meta_queries_take_no_parameters(name: str, fetch: Fetch) -> None:
    """Both are parameterless, and must stay so.

    ``meta/dataset_counts`` is the only query that has to survive the *unseeded*
    state — it is what tells a client whether the analytics views are populated —
    so it cannot depend on a window, a filter, or a matview.
    """
    rows = await fetch(name)
    assert len(rows) == 1, f"{name} is expected to return exactly one summary row"

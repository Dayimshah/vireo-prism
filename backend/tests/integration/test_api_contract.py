"""Contract tests over the live HTTP surface: all 55 operations.

What this tier proves that the others cannot
-------------------------------------------
``test_queries_execute.py`` runs the SQL and ``test_query_values.py`` checks the
numbers, both by calling the registry directly. Neither touches a route. Between
a query and a response sit the request schemas, the repository parameter binding,
the service cache layer, the response envelope, five middlewares and the error
handlers — and every one of them can break a working query.

So this file drives the real ASGI app and asserts the *contract*: the envelope
shape, the status taxonomy, and the per-route window asymmetries.

Coverage identity
-----------------
55 operations = **54 GET + 1 POST**. The POST is
``/admin/refresh-analytics``, the only mutating endpoint in the service. The 54
GETs decompose as 50 page-owned + 4 non-page (``/search``, ``/meta/filters``,
``/meta/bounds``, ``/health``). :func:`test_the_operation_count_is_what_is_documented`
pins that arithmetic so this docstring cannot quietly go stale.

Two things about the harness
---------------------------
**The lifespan must be driven by hand.** ``httpx.ASGITransport`` does not run
startup or shutdown events, and the lifespan is what calls ``init_engine()``,
``init_dimension_catalog()`` and ``init_cache()``. Without it every route fails
on a missing engine — which would look like 54 broken endpoints.

**Each client gets its own address.** ``RateLimitMiddleware`` is a token bucket
keyed on ``scope["client"]``, with a 60-token burst refilling at 4/second. A
54-route sweep fits inside one burst; two sweeps from one address do not, and the
overflow arrives as ``429``s that read exactly like route failures.
``ASGITransport`` lets the peer address be set explicitly, so
:func:`fresh_client` hands out a distinct one per client and no test can be
poisoned by its neighbours. That the sweeps stay under the burst is itself
asserted, rather than assumed, in
:func:`test_a_full_route_sweep_fits_inside_the_rate_limit_burst`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from app.main import app, lifespan
from app.middleware import RATE_LIMIT_BURST
from app.repositories.search import MIN_QUERY_LENGTH

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration

#: Incremented per client so each gets its own rate-limit bucket. A plain counter
#: rather than a random address: reproducible, and a collision would silently
#: reintroduce the cross-test interference this exists to prevent.
_client_seq = 0

#: Values for every query parameter the API declares, so a generic sweep can send
#: a valid request to any route. Keyed by the OpenAPI parameter name, which is not
#: always the SQL parameter name — the filter parameters are singular here
#: (``country``) and plural in the SQL (``country_ids``).
PARAM_VALUES: dict[str, Any] = {
    "limit": 5,
    "max_months": 6,
    "max_weeks": 6,
    "min_cohort_size": 1,
    "min_risk_score": 0,
    "min_starts": 1,
    "segment_by": "persona",
    # `/search` enforces `min_length=MIN_QUERY_LENGTH` on `q`, so a single
    # character is a 422 and the sweeps below would report `/search` as a broken
    # route. Derived from the constant rather than written as "aa": if the minimum
    # is ever raised, this follows it instead of silently failing again.
    "q": "a" * MIN_QUERY_LENGTH,
    "alpha": 0.05,
}

#: Path parameter substitutions. Only one route family is parameterised.
PATH_VALUES: dict[str, str] = {"experiment_key": "paywall-copy-value-first"}

#: Keys every enveloped response carries in ``meta``.
META_KEYS = frozenset({"cache", "rows", "window", "filters_applied", "generated_at", "request_id"})

#: The one GET that is deliberately *not* enveloped. ``/health`` is a probe for
#: orchestrators and monitoring, so it returns a flat document they can read
#: without knowing this project's envelope — and it must answer even when the
#: database is down, which is when a ``{data, meta}`` wrapper would be a lie.
UNENVELOPED = "/health"


def fresh_client() -> httpx.AsyncClient:
    """Return a client with its own rate-limit bucket.

    See the module docstring: sharing an address across tests makes one test's
    request volume another test's ``429``.
    """
    global _client_seq  # noqa: PLW0603 - deliberate module-level counter
    _client_seq += 1
    octet_high, octet_low = divmod(_client_seq, 256)
    transport = httpx.ASGITransport(app=app, client=(f"10.{octet_high}.{octet_low}.1", 1))
    return httpx.AsyncClient(transport=transport, base_url="http://contract.test")


@asynccontextmanager
async def running_app() -> AsyncIterator[httpx.AsyncClient]:
    """Start the app, yield a client, then shut it down.

    Function-scoped by construction rather than shared: the lifespan disposes the
    engine on exit, and an engine created on one event loop cannot be reused from
    another. Re-running startup per test is the cost of not fighting that.
    """
    async with lifespan(app), fresh_client() as client:
        yield client


@pytest.fixture
def operations() -> list[tuple[str, str, dict[str, Any]]]:
    """Return ``(method, path, spec)`` for every operation in the OpenAPI schema.

    Read from the app's own schema rather than from a hand-kept list, so a route
    added without a test here still gets swept.
    """
    schema = app.openapi()
    found: list[tuple[str, str, dict[str, Any]]] = []
    for path, methods in schema["paths"].items():
        for method, spec in methods.items():
            if method in {"get", "post"}:
                found.append((method, path, spec))
    return sorted(found)


def request_for(path: str, spec: dict[str, Any], window: dict[str, Any]) -> tuple[str, dict]:
    """Build a valid URL and query mapping for one operation.

    Only parameters the operation *declares* are sent. That is not politeness:
    ``strict_query`` rejects an undeclared parameter with a ``422``, so a sweep
    that sent a uniform parameter set would fail on almost every route.
    """
    url = path
    query: dict[str, Any] = {}

    for parameter in spec.get("parameters", []):
        name = parameter["name"]
        if parameter.get("in") == "path":
            url = url.replace(f"{{{name}}}", PATH_VALUES[name])
            continue
        if name in window:
            query[name] = window[name].isoformat()
        elif name in PARAM_VALUES:
            query[name] = PARAM_VALUES[name]

    return url, query


@pytest.fixture
async def api_window(bounds: dict[str, Any]) -> dict[str, Any]:
    """Return the window parameters keyed by their *API* names.

    The API exposes ``date_from``/``date_to``/``observation_end``, the same names
    the SQL uses, but sourced from the bounds endpoint's own answer so the window
    is always inside the data.
    """
    return {
        "date_from": bounds["first_activity_date"],
        "date_to": bounds["last_activity_date"],
        "observation_end": bounds["last_activity_date"],
    }


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


def test_the_operation_count_is_what_is_documented(
    operations: list[tuple[str, str, dict[str, Any]]],
) -> None:
    """55 operations: 54 GET and exactly one POST.

    No database needed — this reads the schema. Pinned because the whole project's
    coverage claim rests on this arithmetic, and because a second mutating
    endpoint appearing unnoticed would be a genuine surprise in a read-only
    analytics service.
    """
    gets = [path for method, path, _ in operations if method == "get"]
    posts = [path for method, path, _ in operations if method == "post"]

    assert len(gets) == 54, sorted(gets)
    assert len(posts) == 1, posts
    assert posts[0].endswith("/admin/refresh-analytics")


def test_a_full_route_sweep_fits_inside_the_rate_limit_burst(
    operations: list[tuple[str, str, dict[str, Any]]],
) -> None:
    """Guard the harness: one sweep must not exhaust a client's bucket.

    If the route count ever exceeds the burst, the sweeps below would start
    reporting ``429``s that look like endpoint failures. This fails first, and
    says to split the sweep across clients instead.
    """
    gets = [path for method, path, _ in operations if method == "get"]
    assert len(gets) <= RATE_LIMIT_BURST, (
        f"{len(gets)} GET routes against a {RATE_LIMIT_BURST}-token burst: a "
        "single-client sweep will now be rate limited part-way through"
    )


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


async def test_every_get_route_answers_successfully(api_window: dict[str, Any]) -> None:
    """All 54 GETs return 200 with the parameters they declare.

    Failures are collected rather than raised on the first one: a broken shared
    dependency would otherwise surface as a single arbitrary route, hiding whether
    the problem is one endpoint or all of them.
    """
    failures: list[str] = []

    async with running_app() as client:
        schema = app.openapi()
        for path, methods in sorted(schema["paths"].items()):
            spec = methods.get("get")
            if spec is None:
                continue
            url, query = request_for(path, spec, api_window)
            response = await client.get(url, params=query)
            if response.status_code != 200:
                failures.append(
                    f"GET {url} {query} -> {response.status_code} {response.text[:180]}"
                )

    assert not failures, "routes did not return 200:\n  " + "\n  ".join(failures)


async def test_every_enveloped_response_has_the_same_shape(api_window: dict[str, Any]) -> None:
    """``{data, meta}`` with a fixed ``meta`` key set, on every route but ``/health``.

    The frontend's ``usePanel`` hook derives its ``rows`` from ``payload.data`` and
    reads ``meta.rows`` for its counts, so a route that answered with a bare list
    or a differently-named wrapper would break one page while every other test
    passed.
    """
    failures: list[str] = []

    async with running_app() as client:
        schema = app.openapi()
        for path, methods in sorted(schema["paths"].items()):
            spec = methods.get("get")
            if spec is None or path.endswith(UNENVELOPED):
                continue

            url, query = request_for(path, spec, api_window)
            payload = (await client.get(url, params=query)).json()

            if set(payload) != {"data", "meta"}:
                failures.append(f"{url}: top-level keys {sorted(payload)}")
                continue
            missing = META_KEYS - set(payload["meta"])
            if missing:
                failures.append(f"{url}: meta missing {sorted(missing)}")

    assert not failures, "envelope violations:\n  " + "\n  ".join(failures)


async def test_meta_rows_agrees_with_the_data_it_describes(api_window: dict[str, Any]) -> None:
    """``meta.rows`` counts the rows in ``data`` when ``data`` is a list.

    Four endpoints put an *object* in ``data`` rather than a list — the two
    ``/meta`` routes, ``/overview``, and the experiment results — and there the
    meaning of ``rows`` is not uniform, so it is pinned per endpoint rather than
    generalised:

    * ``/overview`` reports **6**, the length of its ``tiles`` array. The
      surrounding object is framing (window, comparison window, filter flag) and
      the tiles are the payload, so counting them is the more useful answer.
    * the other three report **1**, describing the single document — even the
      experiment results, which also contains a ``variants`` array.

    There is deliberately no clever rule inferring the count from the shape: any
    rule that produced 6 for ``/overview`` would also produce a variant count for
    the experiment results, which is not what the API returns. An explicit
    exception states the contract; an inferred one would state a coincidence.
    """
    #: Object-payload endpoints whose ``rows`` counts a nested collection.
    counts_a_nested_list = {"/api/v1/overview": "tiles"}
    failures: list[str] = []

    async with running_app() as client:
        schema = app.openapi()
        for path, methods in sorted(schema["paths"].items()):
            spec = methods.get("get")
            if spec is None or path.endswith(UNENVELOPED):
                continue

            url, query = request_for(path, spec, api_window)
            payload = (await client.get(url, params=query)).json()
            data, rows = payload["data"], payload["meta"]["rows"]

            if isinstance(data, list):
                if rows != len(data):
                    failures.append(f"{url}: meta.rows={rows} but data has {len(data)}")
            elif path in counts_a_nested_list:
                key = counts_a_nested_list[path]
                nested = data.get(key)
                if not isinstance(nested, list):
                    failures.append(f"{url}: expected a list at data.{key}")
                elif rows != len(nested):
                    failures.append(f"{url}: meta.rows={rows} but data.{key} has {len(nested)}")
            elif rows != 1:
                failures.append(f"{url}: object payload reported meta.rows={rows}")

    assert not failures, "row-count disagreements:\n  " + "\n  ".join(failures)


async def test_every_response_carries_a_request_id_header(api_window: dict[str, Any]) -> None:
    """``X-Request-ID`` on the response, and the same value inside ``meta``.

    The correlation contract: a user reporting a bad chart can quote the header,
    and it has to match what the logs recorded for that request.
    """
    async with running_app() as client:
        response = await client.get(
            "/api/v1/kpi/dau",
            params={
                "date_from": api_window["date_from"].isoformat(),
                "date_to": api_window["date_to"].isoformat(),
            },
        )

    assert response.headers.get("X-Request-ID")
    assert response.json()["meta"]["request_id"] == response.headers["X-Request-ID"]


# ---------------------------------------------------------------------------
# Window asymmetries — the parameter surface is not uniform
# ---------------------------------------------------------------------------


def test_the_window_parameter_shapes_are_exactly_as_catalogued(
    operations: list[tuple[str, str, dict[str, Any]]],
) -> None:
    """The window is **not** uniform across the API, and the shape counts are pinned.

    Measured: 34 routes take ``date_from``+``date_to``; 10 add ``observation_end``
    (retention and cohort routes, which need a maturity cutoff distinct from the
    window); 7 take no window at all; ``/geo/country-ranking`` takes ``date_to``
    only; and the two experiment routes take ``observation_end`` only. 34+10+7+1+2
    = 54.

    This asymmetry is why the frontend has a ``windowParamsFor(path, window)``
    helper that strips per path rather than spreading one window everywhere: with
    ``strict_query`` in force, sending ``date_from`` to a route that does not
    declare it is a **422**, not a harmlessly ignored parameter.
    """
    shapes: dict[tuple[str, ...], list[str]] = {}
    for method, path, spec in operations:
        if method != "get":
            continue
        names = {parameter["name"] for parameter in spec.get("parameters", [])}
        key = tuple(sorted(names & {"date_from", "date_to", "observation_end"}))
        shapes.setdefault(key, []).append(path)

    counts = {key: len(paths) for key, paths in shapes.items()}
    assert counts == {
        ("date_from", "date_to"): 34,
        ("date_from", "date_to", "observation_end"): 10,
        (): 7,
        ("date_to",): 1,
        ("observation_end",): 2,
    }, counts

    # The two singletons are named, because each is a deliberate design decision
    # rather than an oversight, and a regression would move them silently.
    assert shapes[("date_to",)] == ["/api/v1/geo/country-ranking"]
    assert all("experiments" in path for path in shapes[("observation_end",)])


async def test_a_window_parameter_a_route_does_not_declare_is_rejected(
    api_window: dict[str, Any],
) -> None:
    """``/geo/country-ranking`` takes ``date_to`` only; ``date_from`` is a 422.

    The concrete case behind the catalogue above. This endpoint ranks countries
    over all history up to a cutoff, so a lower bound is meaningless to it —
    and ``strict_query`` refuses rather than ignoring, on the grounds that a
    silently dropped parameter returns confidently wrong data.
    """
    async with running_app() as client:
        accepted = await client.get(
            "/api/v1/geo/country-ranking",
            params={"date_to": api_window["date_to"].isoformat()},
        )
        rejected = await client.get(
            "/api/v1/geo/country-ranking",
            params={
                "date_from": api_window["date_from"].isoformat(),
                "date_to": api_window["date_to"].isoformat(),
            },
        )

    assert accepted.status_code == 200
    assert rejected.status_code == 422


async def test_the_windowless_routes_reject_a_window(api_window: dict[str, Any]) -> None:
    """The seven routes with no window parameters refuse one.

    ``/churn/risk-scorecard`` and ``/users/rfm-segments`` score users on their
    *current* state, so a historical window has no meaning for them. Asserted
    because a client that assumed a uniform window would break here and nowhere
    else.
    """
    async with running_app() as client:
        for path in ("/api/v1/churn/risk-scorecard", "/api/v1/users/rfm-segments"):
            ok = await client.get(path)
            assert ok.status_code == 200, f"{path} -> {ok.status_code} {ok.text[:150]}"

            with_window = await client.get(
                path, params={"date_from": api_window["date_from"].isoformat()}
            )
            assert with_window.status_code == 422, path


# ---------------------------------------------------------------------------
# Status taxonomy
# ---------------------------------------------------------------------------


async def test_an_undeclared_query_parameter_is_a_422(api_window: dict[str, Any]) -> None:
    """``strict_query`` rejects rather than ignores, and says so as RFC 7807.

    The alternative — FastAPI's default of dropping unknown parameters — means a
    typo like ``date_form`` returns a 200 for the wrong window. Loud is correct
    here.
    """
    async with running_app() as client:
        response = await client.get(
            "/api/v1/kpi/dau",
            params={
                "date_from": api_window["date_from"].isoformat(),
                "date_to": api_window["date_to"].isoformat(),
                "not_a_real_parameter": "1",
            },
        )

    assert response.status_code == 422
    body = response.json()
    assert body["type"].endswith("/validation-error")
    assert body["title"] == "Validation error"


@pytest.mark.parametrize(
    ("label", "params"),
    [
        ("no window at all", {}),
        ("unparseable dates", {"date_from": "not-a-date", "date_to": "also-not"}),
        ("inverted window", {"date_from": "2026-08-06", "date_to": "2025-02-07"}),
    ],
)
async def test_malformed_window_parameters_are_422(label: str, params: dict[str, str]) -> None:
    """Missing, unparseable and inverted windows all validate to 422.

    The inverted case is the interesting one: both dates parse, so only an
    explicit model validator catches it. Without that check the query returns an
    empty result set, and an empty chart reads as "no activity" rather than as a
    bad request.
    """
    async with running_app() as client:
        response = await client.get("/api/v1/kpi/dau", params=params)

    assert response.status_code == 422, f"{label}: {response.status_code}"
    assert response.json()["type"].endswith("/validation-error"), label


async def test_an_unknown_experiment_key_is_a_404_problem_document() -> None:
    """A missing resource is a domain 404 in problem+json, not a 200 with nothing.

    An empty variant list would be indistinguishable from an experiment that ran
    with no participants.
    """
    async with running_app() as client:
        response = await client.get("/api/v1/experiments/no-such-experiment/results")

    assert response.status_code == 404
    body = response.json()
    assert body["type"].endswith("/not-found")
    assert body["title"] == "Resource not found"


async def test_an_unrouted_path_is_a_plain_404() -> None:
    """Documents the one 404 that is *not* a problem document.

    A path that matches no route never reaches this project's handlers, so it gets
    Starlette's default ``{"detail": "Not Found"}``. Recorded rather than
    normalised: making it uniform would mean intercepting every unmatched path,
    and the distinction is real — one means "no such endpoint", the other means
    "no such row".
    """
    async with running_app() as client:
        response = await client.get("/api/v1/no-such-endpoint")

    assert response.status_code == 404
    body = response.json()
    assert body == {"detail": "Not Found"}
    assert "type" not in body


async def test_the_admin_refresh_requires_a_key() -> None:
    """The only mutating endpoint refuses an unauthenticated call.

    Not merely a permissions detail: ``REFRESH MATERIALIZED VIEW`` on this dataset
    is expensive, so an open endpoint is also a denial-of-service lever. The
    request is not retried with a valid key — that would rebuild every matview
    mid-test-run and change the numbers the value assertions were verified
    against.
    """
    async with running_app() as client:
        response = await client.post("/api/v1/admin/refresh-analytics")

    assert response.status_code == 401


async def test_an_unsupported_method_on_a_real_route_is_405() -> None:
    """A GET-only route answers ``405``, not ``404``, when posted to.

    The distinction tells a client "right URL, wrong verb" rather than sending
    them hunting for a path that is in fact correct.
    """
    async with running_app() as client:
        response = await client.post("/api/v1/kpi/dau")

    assert response.status_code == 405


# ---------------------------------------------------------------------------
# Health, which follows none of the rules above
# ---------------------------------------------------------------------------


async def test_health_returns_a_flat_document_not_an_envelope() -> None:
    """``/health`` is a probe, and deliberately unenveloped.

    An orchestrator's readiness check should not have to understand this
    project's response wrapper, and the endpoint has to stay answerable when the
    database is down — the moment a ``{data, meta}`` envelope would be least
    truthful. Pinned so nobody "fixes" the inconsistency.
    """
    async with running_app() as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert "data" not in body
    assert {"status", "database_connected", "schema_ready", "analytics_ready"} <= set(body)
    assert body["database_connected"] is True
    assert body["status"] in {"ok", "degraded"}


async def test_health_is_exempt_from_rate_limiting() -> None:
    """More requests than the burst allows, all answered.

    ``EXEMPT_PATH_SUFFIXES`` covers ``/health`` because a liveness probe polling
    on a fixed interval would otherwise consume the same bucket as real traffic —
    and a limiter that throttles the health check takes the service down to
    protect it.
    """
    async with running_app() as client:
        statuses = [
            (await client.get("/api/v1/health")).status_code for _ in range(RATE_LIMIT_BURST + 5)
        ]

    assert set(statuses) == {200}, sorted(set(statuses))


async def test_exceeding_the_burst_on_a_normal_route_is_a_429() -> None:
    """The limiter does fire, on a route that is not exempt.

    Without this the exemption test above would pass just as happily against a
    limiter that never triggers at all.
    """
    async with running_app() as client:
        statuses = []
        for _ in range(RATE_LIMIT_BURST + 20):
            response = await client.get("/api/v1/meta/bounds")
            statuses.append(response.status_code)
            if response.status_code == 429:
                break

    assert 429 in statuses, "the rate limiter never rejected anything"
    assert statuses.count(200) >= 1


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


async def test_a_repeated_request_reports_a_cache_hit(api_window: dict[str, Any]) -> None:
    """The second identical request is served from cache, and says so.

    Both in the ``X-Cache`` header and in ``meta.cache``. Worth asserting because
    a silently disabled cache is invisible: every response stays correct and only
    the latency changes. Values must be identical across the two — the tagged
    codec in ``services/base.py`` exists precisely so a hit does not degrade
    ``Decimal`` to ``str``.
    """
    params = {
        "date_from": api_window["date_from"].isoformat(),
        "date_to": api_window["date_to"].isoformat(),
    }

    async with running_app() as client:
        first = await client.get("/api/v1/kpi/dau", params=params)
        second = await client.get("/api/v1/kpi/dau", params=params)

    assert first.status_code == second.status_code == 200
    assert second.headers["X-Cache"] == "HIT"
    assert second.json()["meta"]["cache"] == "HIT"
    # The payload survives the round trip through the cache unchanged.
    assert second.json()["data"] == first.json()["data"]


async def test_a_different_window_is_a_different_cache_entry(
    api_window: dict[str, Any],
) -> None:
    """The window is part of the cache key.

    If it were not, the second window would be served the first window's rows —
    correct-looking data for the wrong period, which no other test would catch.
    """
    wide = {
        "date_from": api_window["date_from"].isoformat(),
        "date_to": api_window["date_to"].isoformat(),
    }
    narrow = {
        "date_from": api_window["date_to"].isoformat(),
        "date_to": api_window["date_to"].isoformat(),
    }

    async with running_app() as client:
        first = await client.get("/api/v1/kpi/dau", params=wide)
        second = await client.get("/api/v1/kpi/dau", params=narrow)

    assert first.json()["meta"]["rows"] != second.json()["meta"]["rows"]
    assert second.json()["meta"]["window"]["days"] == 1

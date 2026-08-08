"""Tests for :mod:`app.repositories.base`.

Three things live here and each has a trap in it
-----------------------------------------------
* ``_coerce_date`` exists because asyncpg is strict where psycopg was lenient:
  ``CAST(:d AS date)`` needs a real :class:`datetime.date`, and an ISO string
  raises ``'str' object has no attribute 'toordinal'`` deep inside the driver.
  Its ``datetime``-subclass exclusion is documented as load-bearing.
* ``FilterSet.as_params`` normalises an empty sequence to ``None`` because the
  SQL reads ``NULL`` as *match all* and an empty array as *match nothing* — but
  ``is_premium=False`` is a real filter that must survive that normalisation.
* ``bind_params`` intersects what a caller supplies against what a query
  declares, so extras are discarded and a genuine omission raises instead of
  reaching the driver as a missing bind.

No database is needed for any of this. ``bind_params`` reads the SQL registry,
which is files on disk, so these stay unit tests.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pytest

from app.repositories.base import DATE_PARAMS, FilterSet, _coerce_date, bind_params
from app.sql.registry import get_registry, init_registry


@pytest.fixture(scope="module", autouse=True)
def _registry() -> None:
    """Load the SQL registry once for the module.

    ``bind_params`` calls ``get_registry().params(name)``, and the process-wide
    registry starts empty — it is populated during app startup, which does not
    happen in a unit test.
    """
    init_registry()


# ---------------------------------------------------------------------------
# _coerce_date
# ---------------------------------------------------------------------------


def test_coerce_date_passes_a_real_date_through_unchanged() -> None:
    """The common case: already the right type, so no work and no copy."""
    value = date(2026, 5, 9)
    assert _coerce_date("date_from", value) is value


def test_coerce_date_truncates_a_datetime_to_its_date() -> None:
    """The load-bearing subclass exclusion.

    ``datetime`` IS-A ``date``, so a guard written as
    ``isinstance(value, date)`` alone would return the datetime untouched and
    hand a timestamp to a date column. Every consumer of these parameters
    compares against a date, so truncation is the correct resolution.
    """
    # Naive on purpose: this is the value a caller actually passes when it hands a
    # timestamp where a date belongs, which is the mistake the truncation exists to
    # absorb. A tz-aware input would truncate to the same day and so would not
    # exercise anything extra.
    result = _coerce_date("date_to", datetime(2026, 5, 9, 23, 59, 59))  # noqa: DTZ001
    assert type(result) is date
    assert result == date(2026, 5, 9)


def test_coerce_date_parses_an_iso_string() -> None:
    """Accepted for convenience at the service boundary, then normalised."""
    result = _coerce_date("date_from", "2026-05-09")
    assert type(result) is date
    assert result == date(2026, 5, 9)


def test_coerce_date_tolerates_surrounding_whitespace() -> None:
    """``.strip()`` is applied before parsing."""
    assert _coerce_date("date_from", "  2026-05-09  ") == date(2026, 5, 9)


def test_coerce_date_passes_none_through() -> None:
    """``None`` means "no bound supplied" and is a legitimate value here."""
    assert _coerce_date("observation_end", None) is None


@pytest.mark.parametrize(
    "bad",
    [
        "not-a-date",
        "09/05/2026",
        "2026-13-45",
        "",
        "2026-05-09T12:00:00",
        20_260_509,
        3.14,
        object(),
    ],
)
def test_coerce_date_rejects_anything_it_cannot_interpret(bad: Any) -> None:
    """A ``RuntimeError``, deliberately, not a ``ValueError``.

    This is a programming error at the service boundary — caller input is already
    validated by the request schemas — so it is raised as an internal fault
    rather than as something a client could have caused.
    """
    with pytest.raises(RuntimeError, match=r"date|ISO"):
        _coerce_date("date_from", bad)


def test_coerce_date_names_the_parameter_in_its_error() -> None:
    """The message has to say which parameter, or it is useless in a log."""
    with pytest.raises(RuntimeError, match="observation_end"):
        _coerce_date("observation_end", "rubbish")


# ---------------------------------------------------------------------------
# FilterSet
# ---------------------------------------------------------------------------


def test_an_unset_filterset_disables_every_filter() -> None:
    """The default is "no filters", rendered as ``None`` for all eight keys."""
    params = FilterSet().as_params()
    assert set(params) == {
        "country_ids",
        "channel_ids",
        "persona_ids",
        "signup_device_ids",
        "is_premium",
        "genre_ids",
        "content_types",
        "languages",
    }
    assert all(value is None for value in params.values())


def test_empty_sequences_collapse_to_none() -> None:
    """An empty list must disable the predicate, not exclude every row.

    The SQL reads ``NULL`` as *match all* and an empty array as *match nothing*,
    so passing ``[]`` through unchanged would silently return zero rows for a
    filter the caller believed was inactive.
    """
    params = FilterSet(country_ids=[], genre_ids=(), languages=[]).as_params()
    assert params["country_ids"] is None
    assert params["genre_ids"] is None
    assert params["languages"] is None


def test_is_premium_false_survives_normalisation() -> None:
    """``False`` is a real filter — "free users only" — and is not falsy-collapsed.

    The one field deliberately routed around the empty-sequence helper. Getting
    this wrong would silently turn "show me non-paying users" into "show me
    everyone", which reads as plausible data rather than as a bug.
    """
    assert FilterSet(is_premium=False).as_params()["is_premium"] is False
    assert FilterSet(is_premium=True).as_params()["is_premium"] is True
    assert FilterSet(is_premium=None).as_params()["is_premium"] is None

    # And it reaches `describe`, which drops only `None`.
    assert FilterSet(is_premium=False).describe() == {"is_premium": False}


def test_sequences_are_materialised_into_lists() -> None:
    """Asyncpg accepts a list for an array parameter, not an arbitrary iterable."""
    params = FilterSet(country_ids=(1, 2, 3), persona_ids=range(4, 7)).as_params()
    assert params["country_ids"] == [1, 2, 3]
    assert type(params["country_ids"]) is list
    assert params["persona_ids"] == [4, 5, 6]
    assert type(params["persona_ids"]) is list


def test_describe_reports_only_active_filters() -> None:
    """For logging and cache keys, so an inactive filter cannot split the cache."""
    described = FilterSet(country_ids=[7], is_premium=True, genre_ids=[]).describe()
    assert described == {"country_ids": [7], "is_premium": True}


def test_filterset_is_frozen() -> None:
    """Immutable, so a cache key derived from it cannot go stale.

    A resolved filter set is what the cache key is built from, so allowing
    mutation afterwards would let the key and the filters it names disagree.
    """
    filters = FilterSet(country_ids=[1])
    with pytest.raises(AttributeError):
        filters.country_ids = [2]  # type: ignore[misc]


# ---------------------------------------------------------------------------
# bind_params
# ---------------------------------------------------------------------------


def pick_query_declaring(*required: str) -> str:
    """Return a registered query name that declares every given parameter.

    Chosen from the registry rather than hardcoded, so renaming a query file does
    not silently skip these tests.
    """
    registry = get_registry()
    for name in registry.names():
        if set(required) <= registry.params(name):
            return name
    pytest.skip(f"no registered query declares {required!r}")


def test_bind_params_returns_exactly_what_the_query_declares() -> None:
    """Extras are expected and dropped — a ``FilterSet`` rendering is a superset.

    Forty queries include the user-filter fragment and six the content-filter
    one, so every caller supplies all eight filter keys and most queries want a
    subset. Passing an undeclared parameter to the driver is an error, hence the
    intersection.
    """
    name = pick_query_declaring("date_from", "date_to")
    declared = get_registry().params(name)

    supplied: dict[str, Any] = {
        **FilterSet().as_params(),
        "date_from": date(2026, 5, 9),
        "date_to": date(2026, 8, 6),
        "totally_undeclared_parameter": "ignored",
    }

    bound = bind_params(name, supplied)
    assert set(bound) == set(declared)
    assert "totally_undeclared_parameter" not in bound


def test_bind_params_coerces_only_the_date_parameters() -> None:
    """ISO strings become dates; everything else is passed through as given."""
    name = pick_query_declaring("date_from", "date_to")

    bound = bind_params(
        name,
        {
            **FilterSet(country_ids=[3]).as_params(),
            "date_from": "2026-05-09",
            "date_to": "2026-08-06",
        },
    )

    assert bound["date_from"] == date(2026, 5, 9)
    assert type(bound["date_from"]) is date
    assert bound["date_to"] == date(2026, 8, 6)
    if "country_ids" in bound:
        assert bound["country_ids"] == [3]


def test_date_params_is_the_complete_set_of_coerced_names() -> None:
    """Pinned so a new date-typed parameter cannot be added without coercion.

    A date parameter missing from this set reaches asyncpg as a string and fails
    at the driver with a message that points nowhere useful.
    """
    assert frozenset({"date_from", "date_to", "observation_end"}) == DATE_PARAMS


def test_bind_params_raises_when_a_declared_parameter_is_missing() -> None:
    """A genuine omission is caught here, with the query and the gap named.

    Left to the driver this surfaces as an opaque bind error; the whole point of
    the intersection is that the message says which query and which parameter.
    """
    name = pick_query_declaring("date_from", "date_to")
    with pytest.raises(RuntimeError, match=r"declares parameter"):
        bind_params(name, {"date_from": date(2026, 5, 9)})


def test_bind_params_error_names_the_query_and_the_missing_parameter() -> None:
    """The message must be actionable without opening the SQL file."""
    name = pick_query_declaring("date_from", "date_to")
    with pytest.raises(RuntimeError) as excinfo:
        bind_params(name, {})
    message = str(excinfo.value)
    assert name in message
    assert "date_from" in message or "date_to" in message


def test_bind_params_rejects_an_unregistered_query() -> None:
    """An unknown name is a wiring error, raised as the domain's own exception."""
    from app.core.exceptions import QueryNotFoundError

    with pytest.raises(QueryNotFoundError):
        bind_params("no_such_namespace/no_such_query", {})


def test_a_parameterless_query_binds_to_an_empty_mapping() -> None:
    """Some queries take nothing; supplying filters anyway must not break them."""
    registry = get_registry()
    for name in registry.names():
        if not registry.params(name):
            assert bind_params(name, FilterSet(country_ids=[1]).as_params()) == {}
            return
    pytest.skip("every registered query declares at least one parameter")

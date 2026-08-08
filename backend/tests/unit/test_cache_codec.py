"""Tests for the tagged cache codec in :mod:`app.services.base`.

What this codec is for
---------------------
``core/cache.py`` is a frozen file with a type-fidelity bug: ``RedisCache``
serialises with ``json.dumps(..., default=str)``, so a ``Decimal("12.34")``
comes back as the string ``"12.34"`` and a ``date`` as ``"2024-01-01"``, while
``LocalCache`` stores live Python objects. Left alone, every repository type
guarantee holds on a cache miss and breaks on a hit — and holds locally while
breaking under Redis.

Rather than edit the frozen file, a tagged codec wraps both paths:
``{"__t": "dec", "v": "12.34"}`` on the way in, restored on the way out. It runs
when *encoding a freshly computed result too*, which looks redundant and is the
point: a codec bug surfaces on the first request rather than on the first cache
hit, and no caller can come to depend on richer types that only a miss returns.

So the assertions that matter here are about **type parity**, not values. A test
that only checked ``str(decoded) == str(original)`` would pass against the very
bug this module exists to prevent.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.services.base import decode_from_cache, encode_for_cache


def round_trip(value: Any) -> Any:
    """Encode then decode, as the cache does across a miss/hit pair."""
    return decode_from_cache(encode_for_cache(value))


# ---------------------------------------------------------------------------
# Type parity — the whole reason the codec exists
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        Decimal("12.34"),
        Decimal("0"),
        Decimal("-99999.999999"),
        date(2024, 1, 1),
        datetime(2024, 1, 1, 13, 45, 30, tzinfo=UTC),
        time(13, 45, 30),
        timedelta(seconds=90),
    ],
)
def test_round_trip_preserves_the_exact_type(value: Any) -> None:
    """The decoded value is the same *type*, not merely the same rendering.

    ``type(...) is`` rather than ``isinstance``, because ``datetime`` is a
    subclass of ``date``: an ``isinstance`` check would accept a timestamp that
    had been silently degraded to a day.
    """
    result = round_trip(value)
    assert type(result) is type(value)
    assert result == value


def test_decimal_survives_without_binary_rounding() -> None:
    """Money and rates keep their exact decimal representation.

    The codec stores a Decimal as a *string*, not a float. ``0.1`` is not
    representable in binary floating point, so a float round trip would corrupt
    a value like this in the last digits — invisible in a chart, wrong in a
    reconciliation.
    """
    value = Decimal("1234567.891234")
    result = round_trip(value)
    assert type(result) is Decimal
    assert result == value
    # Exact string equality, not numeric approximation.
    assert str(result) == "1234567.891234"
    # And the scale is intact: Decimal("9.99") != Decimal("9.990000000001").
    assert round_trip(Decimal("9.99")) - Decimal("9.99") == Decimal("0")


def test_datetime_is_not_degraded_to_a_date() -> None:
    """The subclass ordering trap, asserted directly.

    ``datetime`` IS-A ``date``, so a codec that checks ``isinstance(value, date)``
    before ``isinstance(value, datetime)`` encodes a timestamp as a bare day and
    throws the time away. The same trap exists in
    ``app.repositories.base._coerce_date``.
    """
    # Deliberately naive: `timestamp without time zone` columns decode to naive
    # datetimes, so this is the shape the codec actually receives from those. The
    # aware case is covered separately by
    # `test_timezone_aware_datetime_keeps_its_offset`, and adding a tzinfo here
    # would leave the naive path — the more common one — untested.
    moment = datetime(2026, 8, 8, 23, 59, 58)  # noqa: DTZ001
    result = round_trip(moment)

    assert type(result) is datetime
    assert result == moment
    assert result.hour == 23
    assert result.minute == 59
    assert result.second == 58


def test_timezone_aware_datetime_keeps_its_offset() -> None:
    """An aware timestamp must not come back naive, which would shift it."""
    aware = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    result = round_trip(aware)
    assert type(result) is datetime
    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(0)
    assert result == aware


def test_timedelta_keeps_microsecond_precision() -> None:
    """Postgres intervals arrive as ``timedelta``; seconds go through as a string.

    A float would lose precision on large spans, which is why the encoding is
    ``str(total_seconds())`` rather than the number itself.
    """
    delta = timedelta(days=3, hours=4, minutes=5, seconds=6, microseconds=7)
    result = round_trip(delta)
    assert type(result) is timedelta
    assert result == delta
    assert result.total_seconds() == pytest.approx(delta.total_seconds(), abs=1e-9)


def test_zero_valued_temporals_round_trip() -> None:
    """Zero is a value, not an absence. Midnight and a zero interval must survive."""
    assert round_trip(time(0, 0, 0)) == time(0, 0, 0)
    assert round_trip(timedelta(0)) == timedelta(0)
    assert type(round_trip(timedelta(0))) is timedelta


# ---------------------------------------------------------------------------
# Passthrough — JSON-native types must not be touched
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [0, 1, -42, 3.14, 0.0, "text", "", True, False, None])
def test_json_native_values_pass_through_unchanged(value: Any) -> None:
    """``int``, ``float``, ``str``, ``bool`` and ``None`` need no encoding."""
    assert encode_for_cache(value) == value
    assert round_trip(value) == value
    assert type(round_trip(value)) is type(value)


def test_bool_is_not_encoded_as_an_integer() -> None:
    """``bool`` is a subclass of ``int``; the codec must not flatten it.

    Several row models expose real booleans (``is_complete``, ``is_significant``),
    and a JSON ``1`` where ``true`` was expected changes how the frontend renders.
    """
    assert type(round_trip(True)) is bool
    assert round_trip(True) is True
    assert round_trip(False) is False


def test_float_and_decimal_stay_distinguishable() -> None:
    """A float must not silently become a Decimal, or the reverse.

    The distinction is load-bearing: phase 9 found that
    ``median_sessions_per_user`` is a genuine ``float`` because
    ``PERCENTILE_CONT`` without a ``::numeric`` cast returns one, while money
    columns are ``Decimal``. Conflating them would make a row model wrong.
    """
    assert type(round_trip(2.5)) is float
    assert type(round_trip(Decimal("2.5"))) is Decimal


# ---------------------------------------------------------------------------
# Structure — rows are lists of dicts
# ---------------------------------------------------------------------------


def test_a_row_set_round_trips_with_every_type_intact() -> None:
    """The realistic case: a list of row mappings straight off a query."""
    rows = [
        {
            "day": date(2026, 5, 9),
            "dau": 412,
            "stickiness_pct": Decimal("51.10"),
            "avg_duration": timedelta(minutes=42, seconds=30),
            "computed_at": datetime(2026, 8, 8, 11, 30, tzinfo=UTC),
            "label": "Monday",
            "is_complete": True,
            "retention_pct": None,
        },
        {
            "day": date(2026, 5, 10),
            "dau": 0,
            "stickiness_pct": None,
            "avg_duration": timedelta(0),
            "computed_at": datetime(2026, 8, 8, 11, 30, tzinfo=UTC),
            "label": "Tuesday",
            "is_complete": False,
            "retention_pct": Decimal("0.00"),
        },
    ]

    result = round_trip(rows)

    assert isinstance(result, list)
    assert len(result) == len(rows)
    for decoded, original in zip(result, rows, strict=True):
        assert decoded.keys() == original.keys()
        for key in original:
            assert type(decoded[key]) is type(original[key]), key
            assert decoded[key] == original[key], key

    # A real zero and a real null must stay distinguishable after the round trip
    # — the discipline the whole frontend rests on.
    assert result[1]["retention_pct"] == Decimal("0.00")
    assert result[0]["retention_pct"] is None


def test_nested_structures_are_encoded_at_every_depth() -> None:
    """The experiments results endpoint returns a nested object, not flat rows."""
    payload = {
        "experiment_key": "paywall-copy-value-first",
        "observation_end": date(2026, 6, 30),
        "variants": [
            {"variant": "control", "rate": Decimal("0.00"), "interval": {"low": Decimal("0.0")}},
            {"variant": "treatment", "rate": Decimal("1.85"), "interval": {"low": Decimal("0.1")}},
        ],
    }

    result = round_trip(payload)

    assert type(result["observation_end"]) is date
    assert type(result["variants"][0]["rate"]) is Decimal
    assert type(result["variants"][1]["interval"]["low"]) is Decimal
    assert result == payload


def test_a_tuple_becomes_a_list() -> None:
    """JSON has no tuple, so the codec normalises to a list on both paths.

    Asserted rather than glossed over: a caller that round-trips a tuple gets a
    list back, and pretending otherwise would be a false guarantee. Nothing in
    the 48 queries returns a tuple, so this is documentation of a boundary rather
    than a constraint anyone relies on.
    """
    assert encode_for_cache((1, 2, 3)) == [1, 2, 3]
    assert round_trip((Decimal("1.5"), date(2026, 1, 1))) == [Decimal("1.5"), date(2026, 1, 1)]


def test_empty_containers_survive() -> None:
    """An empty result set is a finding, and must not decode to ``None``."""
    assert round_trip([]) == []
    assert round_trip({}) == {}
    assert round_trip([{}]) == [{}]


# ---------------------------------------------------------------------------
# The two-key guard — a column named `__t` must not be misread
# ---------------------------------------------------------------------------


def test_a_column_literally_named_double_t_is_not_treated_as_an_encoding() -> None:
    """Decoding requires the exact ``{__t, v}`` two-key shape.

    If a query one day returns a column named ``__t``, a looser check would read
    the row as a tagged value and return garbage. The guard is the key *count*
    plus the presence of ``v``.
    """
    row = {"__t": "some label", "other": 1, "third": 2}
    assert decode_from_cache(row) == row

    # Two keys, but the second is not `v`: still data.
    not_tagged = {"__t": "dec", "value": "12.34"}
    assert decode_from_cache(not_tagged) == not_tagged

    # Three keys including both tag and `v`: still data, because the shape is wrong.
    over_full = {"__t": "dec", "v": "12.34", "extra": True}
    assert decode_from_cache(over_full) == over_full


def test_the_exact_two_key_shape_is_decoded() -> None:
    """The positive case, so the guard above is not passing for the wrong reason."""
    assert decode_from_cache({"__t": "dec", "v": "12.34"}) == Decimal("12.34")


def test_an_unknown_tag_degrades_to_its_payload() -> None:
    """An entry written by an older process must not raise.

    Returning the raw payload is a deliberate choice: degraded data beats a 500
    on a cache read, and the event is logged.
    """
    assert decode_from_cache({"__t": "no-such-tag", "v": "whatever"}) == "whatever"


def test_a_corrupt_payload_for_a_known_tag_degrades_rather_than_raising() -> None:
    """A malformed value under a valid tag is logged and passed through.

    Same reasoning as an unknown tag: a poisoned cache entry should not take down
    the endpoint that reads it.
    """
    assert decode_from_cache({"__t": "dec", "v": "not-a-number"}) == "not-a-number"
    assert decode_from_cache({"__t": "date", "v": "2026-13-45"}) == "2026-13-45"
    assert decode_from_cache({"__t": "td", "v": None}) is None


# ---------------------------------------------------------------------------
# Idempotence and JSON-safety
# ---------------------------------------------------------------------------


def test_encoded_output_contains_only_json_native_types() -> None:
    """The encoder's contract: whatever comes out, ``json.dumps`` can take.

    Verified by walking the encoded structure rather than by calling
    ``json.dumps`` with a ``default=`` fallback, which is exactly the escape
    hatch that hid the original bug.
    """
    import json

    payload = {
        "amount": Decimal("19.99"),
        "day": date(2026, 3, 1),
        # Naive on purpose — see the note in `test_datetime_is_not_degraded_to_a_date`.
        "at": datetime(2026, 3, 1, 8, 0),  # noqa: DTZ001
        "clock": time(8, 0),
        "elapsed": timedelta(minutes=5),
        "rows": [{"n": 1, "v": Decimal("0.5")}],
    }

    encoded = encode_for_cache(payload)

    # No `default=`: if anything non-native survived encoding, this raises.
    text = json.dumps(encoded)
    assert isinstance(text, str)

    # And the decoded form of the parsed JSON equals the original — which is the
    # real Redis path, string round trip included.
    assert decode_from_cache(json.loads(text)) == payload


def test_decode_is_idempotent_on_already_decoded_data() -> None:
    """Decoding live objects from the local cache must be a no-op.

    ``LocalCache`` stores real Python objects, so ``decode_from_cache`` is handed
    values that were never encoded. It has to leave them alone.
    """
    live = {"amount": Decimal("5.00"), "day": date(2026, 1, 1), "n": 3}
    assert decode_from_cache(live) == live
    assert decode_from_cache(decode_from_cache(live)) == live


def test_encode_is_stable_under_repetition() -> None:
    """Encoding an encoded structure changes nothing further.

    Not a property anything relies on, but a cheap check that the tag shape does
    not itself get re-tagged — which would nest ``{__t: ...}`` one level deeper on
    every pass and eventually blow the entry size.
    """
    once = encode_for_cache({"amount": Decimal("1.00")})
    twice = encode_for_cache(once)
    assert once == twice

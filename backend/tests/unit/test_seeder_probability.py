"""Tests for the probability clamps in :mod:`seeder.config`.

Why two clamps, and why conflating them ruins the dataset
--------------------------------------------------------
A probability decided **once** (does this session contain playback) and a
per-day **hazard** evaluated repeatedly (does this user convert today) need
different guards, and an earlier version of the seeder used the one-shot clamp
for both.

The failure is quantitative and silent. A hazard floored at ``PROBABILITY_FLOOR``
= 0.001 looks negligible for one day, but a user observed for 250 days has
cumulative probability ``1 - (1 - 0.001)^250`` ≈ **22%**. So every user who
should never convert — because they watch nothing — converted about a fifth of
the time anyway. That floor drowned the engagement signal the entire simulation
exists to plant, while every individual number still looked plausible.

:func:`~seeder.config.clamp_hazard` therefore **caps but does not floor**: a user
who watches nothing genuinely should never convert, and ``p = 0`` is the correct
answer rather than a degenerate one. The compounding assertions below are the
ones that would have caught the original bug, so they compute the cumulative
probability explicitly instead of trusting the daily figure to look small.

A note on ``clamp_probability``
------------------------------
It is exported in ``seeder.config.__all__`` and documented for one-shot
outcomes, but **nothing in the seeder calls it** — the call sites all moved to
:func:`~seeder.config.clamp_hazard` when the bug above was fixed. That is
recorded here rather than acted on: ``seeder/config.py`` is a frozen phase 1-6
file. It is still exported public API, so its behaviour is pinned below.
"""

from __future__ import annotations

import pytest

from seeder.config import (
    MAX_DAILY_CHURN_HAZARD,
    MAX_DAILY_CONVERSION_PROBABILITY,
    PROBABILITY_FLOOR,
    clamp_hazard,
    clamp_probability,
)


def cumulative(daily: float, days: int) -> float:
    """Return ``1 - (1 - daily)^days``, the chance of at least one occurrence.

    Written out rather than imported, so it is an independent statement of the
    relation the clamps are reasoned about.
    """
    return 1.0 - (1.0 - daily) ** days


# ---------------------------------------------------------------------------
# The constants
# ---------------------------------------------------------------------------


def test_the_floor_and_caps_are_sane_probabilities() -> None:
    """Each constant is a probability, and the caps sit above the floor."""
    assert 0.0 < PROBABILITY_FLOOR < 0.5
    assert 0.0 < MAX_DAILY_CONVERSION_PROBABILITY < 1.0
    assert 0.0 < MAX_DAILY_CHURN_HAZARD < 1.0
    assert PROBABILITY_FLOOR < MAX_DAILY_CONVERSION_PROBABILITY
    assert PROBABILITY_FLOOR < MAX_DAILY_CHURN_HAZARD


def test_the_floor_compounds_to_a_signal_destroying_rate_over_a_tenure() -> None:
    """The measurement behind the whole two-clamp split.

    This is the arithmetic that made the original bug invisible: 0.001 per day
    reads as "negligible" and is nothing of the sort once a user is observed for
    most of a year. Asserted as a documented magnitude, so the reasoning in
    ``clamp_hazard``'s docstring is checked rather than trusted.
    """
    over_a_long_tenure = cumulative(PROBABILITY_FLOOR, 250)
    assert over_a_long_tenure == pytest.approx(0.221, abs=0.005)
    # Which is the same order as the *real* conversion rate the simulation plants
    # in its most engaged quintile — i.e. entirely capable of masking it.
    assert over_a_long_tenure > 0.20


def test_each_cap_implies_a_documented_lifetime_ceiling() -> None:
    """A cap of ``c`` over ``n`` days implies a ceiling of ``1 - (1 - c)^n``.

    Stated so the caps are interpretable rather than magic: choosing one is a
    statement about the whole observation window, not about a single day.
    """
    # Conversion, over roughly the longest tenure in the 18-month window.
    assert cumulative(MAX_DAILY_CONVERSION_PROBABILITY, 250) == pytest.approx(0.78, abs=0.02)
    # Churn, over a single month, is already substantial — which is intended for
    # the most at-risk users.
    assert cumulative(MAX_DAILY_CHURN_HAZARD, 30) == pytest.approx(0.71, abs=0.02)


# ---------------------------------------------------------------------------
# clamp_probability — floors and caps
# ---------------------------------------------------------------------------


def test_clamp_probability_floors_away_from_zero() -> None:
    """A one-shot outcome is never made impossible.

    For a decision taken once, ``p = 0`` would make the outcome deterministic and
    let a model fit it exactly — the reason the floor exists at all.
    """
    assert clamp_probability(0.0) == PROBABILITY_FLOOR
    assert clamp_probability(-5.0) == PROBABILITY_FLOOR
    assert clamp_probability(PROBABILITY_FLOOR / 2) == PROBABILITY_FLOOR


def test_clamp_probability_caps_away_from_one() -> None:
    """Nor is it ever made certain."""
    assert clamp_probability(1.0) == pytest.approx(1.0 - PROBABILITY_FLOOR)
    assert clamp_probability(42.0) == pytest.approx(1.0 - PROBABILITY_FLOOR)


def test_clamp_probability_leaves_interior_values_alone() -> None:
    """Values already inside the band pass through untouched."""
    for value in (0.01, 0.25, 0.5, 0.75, 0.99):
        assert clamp_probability(value) == value


def test_clamp_probability_output_is_always_strictly_inside_the_unit_interval() -> None:
    """The invariant, over a range that includes both extremes."""
    for value in (-1.0, 0.0, 1e-9, 0.5, 1.0 - 1e-9, 1.0, 2.0):
        result = clamp_probability(value)
        assert 0.0 < result < 1.0


# ---------------------------------------------------------------------------
# clamp_hazard — caps only
# ---------------------------------------------------------------------------


def test_clamp_hazard_preserves_a_genuine_zero() -> None:
    """The distinction that fixed the bug.

    A user who watches nothing should never convert, so zero must survive. If
    this ever starts returning a floor, the engagement signal is being destroyed
    again — and nothing else in the pipeline would report it.
    """
    assert clamp_hazard(0.0, cap=MAX_DAILY_CONVERSION_PROBABILITY) == 0.0
    assert clamp_hazard(0.0, cap=MAX_DAILY_CHURN_HAZARD) == 0.0


def test_clamp_hazard_does_not_apply_the_one_shot_floor() -> None:
    """Explicitly contrasted with :func:`clamp_probability` on the same input.

    Written as a comparison rather than two separate assertions, because the
    thing being protected is the *difference* between the two functions.
    """
    tiny = PROBABILITY_FLOOR / 10
    assert clamp_probability(tiny) == PROBABILITY_FLOOR
    assert clamp_hazard(tiny, cap=MAX_DAILY_CONVERSION_PROBABILITY) == tiny
    assert clamp_hazard(tiny, cap=MAX_DAILY_CONVERSION_PROBABILITY) < PROBABILITY_FLOOR


def test_clamp_hazard_clamps_a_negative_hazard_to_zero() -> None:
    """A negative daily rate is meaningless; zero is the nearest honest value."""
    assert clamp_hazard(-0.5, cap=MAX_DAILY_CHURN_HAZARD) == 0.0


def test_clamp_hazard_caps_at_the_supplied_ceiling() -> None:
    """The upper guard, so a runaway coefficient cannot make churn certain."""
    assert clamp_hazard(1.0, cap=MAX_DAILY_CHURN_HAZARD) == MAX_DAILY_CHURN_HAZARD
    assert clamp_hazard(0.5, cap=MAX_DAILY_CONVERSION_PROBABILITY) == (
        MAX_DAILY_CONVERSION_PROBABILITY
    )


def test_clamp_hazard_leaves_interior_values_alone() -> None:
    """Below the cap and above zero, the value is the model's to decide."""
    for value in (0.0001, 0.001, 0.003):
        assert clamp_hazard(value, cap=MAX_DAILY_CONVERSION_PROBABILITY) == value


def test_clamp_hazard_respects_the_cap_it_is_given_not_a_global_one() -> None:
    """The cap is a parameter because the two hazards have different ceilings.

    Churn tolerates a far higher daily rate than conversion, and hardcoding
    either would silently reshape the other.
    """
    assert clamp_hazard(0.02, cap=MAX_DAILY_CONVERSION_PROBABILITY) == (
        MAX_DAILY_CONVERSION_PROBABILITY
    )
    assert clamp_hazard(0.02, cap=MAX_DAILY_CHURN_HAZARD) == 0.02
    assert MAX_DAILY_CONVERSION_PROBABILITY < MAX_DAILY_CHURN_HAZARD


def test_clamp_hazard_is_monotone_non_decreasing() -> None:
    """A larger raw hazard never yields a smaller clamped one."""
    raw = [-1.0, 0.0, 0.001, 0.002, 0.004, 0.006, 0.01, 1.0]
    clamped = [clamp_hazard(value, cap=MAX_DAILY_CONVERSION_PROBABILITY) for value in raw]
    assert clamped == sorted(clamped)


def test_clamp_hazard_output_stays_within_zero_and_the_cap() -> None:
    """The invariant, stated over a range that overshoots both ends."""
    for value in (-10.0, 0.0, 0.003, 0.5, 10.0):
        result = clamp_hazard(value, cap=MAX_DAILY_CONVERSION_PROBABILITY)
        assert 0.0 <= result <= MAX_DAILY_CONVERSION_PROBABILITY


def test_a_zero_hazard_stays_zero_however_long_it_compounds() -> None:
    """The property the whole split exists to protect, expressed cumulatively.

    Zero daily hazard over any number of days is still zero cumulative
    probability. Under the old one-shot clamp this same expression yielded 22%.
    """
    zero = clamp_hazard(0.0, cap=MAX_DAILY_CONVERSION_PROBABILITY)
    for days in (1, 30, 250, 550):
        assert cumulative(zero, days) == 0.0

    # And the contrast: the floored value does not have this property.
    floored = clamp_probability(0.0)
    assert cumulative(floored, 250) > 0.20

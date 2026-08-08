"""Tests for :mod:`app.services.stats`.

Why this file asserts against closed forms rather than recorded output
--------------------------------------------------------------------
A test that pins a function's current answer to four decimal places proves the
function has not changed. It does not prove the function is right, and it fails
for the wrong reason the moment somebody improves the numerics. So every
assertion here is anchored to something computed independently of the
implementation:

* The Student *t* CDF has a closed form at ``df=1`` (Cauchy, an arctangent) and
  at ``df=2`` (an elementary algebraic expression). Both are computed inline and
  compared against the incomplete-beta implementation.
* The normal CDF and its inverse are checked against each other and against the
  textbook 1.959964 at 97.5%.
* Wilson bounds are compared to published values for a standard case.

The two-sided p-value tests are the important ones
-------------------------------------------------
Phase 8's verification found that ``2 * (1 - cdf(abs(z)))`` returns exactly
``0.0`` for large *z*, because ``1 - cdf`` cancels catastrophically once ``cdf``
rounds to 1. The original assertion compared that zero against another zero and
passed — it measured nothing. The regression tests below assert a *positive*
result at ``z = 14.9``, which is the shape of assertion that would have caught
it, and separately assert the documented floor at ``z = 28.3`` where even
``erfc`` underflows.
"""

from __future__ import annotations

import math

import pytest

from app.services.stats import (
    DEFAULT_ALPHA,
    MIN_ARM_SIZE,
    MIN_P_VALUE,
    Verdict,
    compare_means,
    compare_proportions,
    minimum_detectable_effect,
    normal_cdf,
    normal_quantile,
    observed_power,
    required_sample_size,
    student_t_cdf,
    two_sided_normal_p,
    two_sided_t_p,
    wilson_interval,
)

# The standard normal 97.5th percentile, to more digits than any assertion needs.
Z_975 = 1.959963984540054


# ---------------------------------------------------------------------------
# Normal distribution
# ---------------------------------------------------------------------------


def test_normal_cdf_is_symmetric_about_zero() -> None:
    """``Phi(-z) == 1 - Phi(z)`` for every z, exactly as the definition requires."""
    assert normal_cdf(0.0) == pytest.approx(0.5, abs=1e-12)
    for z in (0.1, 0.5, 1.0, 1.959964, 3.0, 6.0):
        assert normal_cdf(-z) == pytest.approx(1.0 - normal_cdf(z), abs=1e-12)


def test_normal_cdf_matches_textbook_quantiles() -> None:
    """Two values every statistics table carries."""
    assert normal_cdf(Z_975) == pytest.approx(0.975, abs=1e-9)
    assert normal_cdf(1.6448536269514722) == pytest.approx(0.95, abs=1e-9)


def test_normal_quantile_inverts_the_cdf() -> None:
    """The quantile function is the CDF's inverse, so composing them is identity.

    Checked in both directions: a one-directional check would pass if both
    functions shared the same systematic error.
    """
    for p in (0.001, 0.025, 0.1, 0.5, 0.9, 0.975, 0.999):
        assert normal_cdf(normal_quantile(p)) == pytest.approx(p, abs=1e-9)
    for z in (-3.0, -1.0, 0.0, 1.0, 3.0):
        assert normal_quantile(normal_cdf(z)) == pytest.approx(z, abs=1e-6)


def test_normal_quantile_at_975_matches_the_published_constant() -> None:
    """The most-cited constant in applied statistics."""
    assert normal_quantile(0.975) == pytest.approx(Z_975, abs=1e-7)


# ---------------------------------------------------------------------------
# Student t — closed forms at df=1 and df=2
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("t", [-4.0, -1.5, -0.25, 0.0, 0.25, 1.5, 4.0, 12.0])
def test_student_t_cdf_matches_cauchy_at_df_one(t: float) -> None:
    """At ``df=1`` the t distribution *is* Cauchy, whose CDF is an arctangent.

    This is a genuine independent check: the implementation evaluates a
    regularised incomplete beta, and ``0.5 + atan(t)/pi`` shares none of that
    machinery.
    """
    expected = 0.5 + math.atan(t) / math.pi
    assert student_t_cdf(t, 1.0) == pytest.approx(expected, abs=1e-10)


@pytest.mark.parametrize("t", [-4.0, -1.5, -0.25, 0.0, 0.25, 1.5, 4.0, 12.0])
def test_student_t_cdf_matches_closed_form_at_df_two(t: float) -> None:
    """At ``df=2`` the CDF is ``0.5 * (1 + t / sqrt(2 + t^2))``, elementary."""
    expected = 0.5 * (1.0 + t / math.sqrt(2.0 + t * t))
    assert student_t_cdf(t, 2.0) == pytest.approx(expected, abs=1e-10)


def test_student_t_cdf_is_symmetric() -> None:
    """Symmetry about zero, for a df the closed forms do not cover."""
    for t in (0.5, 2.0, 5.0):
        assert student_t_cdf(-t, 7.0) == pytest.approx(1.0 - student_t_cdf(t, 7.0), abs=1e-12)


def test_student_t_approaches_normal_as_df_grows() -> None:
    """With many degrees of freedom the t distribution converges on the normal.

    Asserted as a *tightening* sequence rather than a single tolerance, so the
    test states the limiting behaviour instead of a magic number.
    """
    errors = [abs(student_t_cdf(1.5, df) - normal_cdf(1.5)) for df in (5.0, 50.0, 5_000.0)]
    assert errors[0] > errors[1] > errors[2]
    assert errors[-1] < 1e-4


def test_student_t_cdf_matches_published_table_values() -> None:
    """One-sided 95% critical values from a standard t-table."""
    # t(0.95, df=10) = 1.812, t(0.95, df=20) = 1.725
    assert student_t_cdf(1.8124611228, 10.0) == pytest.approx(0.95, abs=1e-6)
    assert student_t_cdf(1.7247182429, 20.0) == pytest.approx(0.95, abs=1e-6)


# ---------------------------------------------------------------------------
# Two-sided p-values — the catastrophic-cancellation regressions
# ---------------------------------------------------------------------------


def test_two_sided_normal_p_at_conventional_thresholds() -> None:
    """The z that defines a 5% two-sided test returns 5%."""
    assert two_sided_normal_p(Z_975) == pytest.approx(DEFAULT_ALPHA, abs=1e-9)
    assert two_sided_normal_p(0.0) == pytest.approx(1.0, abs=1e-12)


def test_two_sided_normal_p_is_positive_far_into_the_tail() -> None:
    """At ``z = 14.9`` the true p is about 3e-50 — emphatically not zero.

    ``2 * (1 - normal_cdf(14.9))`` evaluates to exactly ``0.0`` in IEEE double
    precision, because ``normal_cdf(14.9)`` rounds to 1.0 and the subtraction
    annihilates every significant digit. This is the assertion shape that
    catches that: a positive lower bound, plus an order-of-magnitude check.
    """
    p = two_sided_normal_p(14.9)
    assert p > 0.0
    assert 1e-51 < p < 1e-48
    # And the naive expression really is zero, which is why the function cannot
    # be written that way. If this ever fails, the platform's float widened and
    # the comment above needs revisiting.
    assert 2.0 * (1.0 - normal_cdf(14.9)) == 0.0


def test_two_sided_normal_p_never_returns_zero_at_extreme_z() -> None:
    """Beyond ``z ~ 28`` even ``erfc`` underflows, so a floor is documented.

    The floor is a floor, not a measurement: it asserts the invariant "a p-value
    is never exactly zero", which downstream code relies on when taking logs.
    """
    for z in (28.3, 40.0, 100.0):
        p = two_sided_normal_p(z)
        assert p > 0.0
        assert p >= MIN_P_VALUE


def test_two_sided_normal_p_is_monotone_decreasing_in_z() -> None:
    """More extreme evidence cannot yield a larger p-value."""
    values = [two_sided_normal_p(z) for z in (0.5, 1.0, 2.0, 4.0, 8.0)]
    assert values == sorted(values, reverse=True)


def test_two_sided_t_p_matches_the_cauchy_closed_form() -> None:
    """At ``df=1``, ``p = 2 * (1 - (0.5 + atan(|t|)/pi))`` exactly."""
    for t in (0.5, 2.0, 6.0):
        expected = 2.0 * (0.5 - math.atan(abs(t)) / math.pi)
        assert two_sided_t_p(t, 1.0) == pytest.approx(expected, abs=1e-10)


def test_two_sided_t_p_never_returns_zero() -> None:
    """Same invariant as the normal case, on the t path."""
    for t, df in ((50.0, 3.0), (200.0, 10.0), (1e4, 30.0)):
        assert two_sided_t_p(t, df) > 0.0


# ---------------------------------------------------------------------------
# Wilson interval
# ---------------------------------------------------------------------------


def test_wilson_interval_matches_published_values() -> None:
    """10 successes in 100 trials gives (5.52%, 17.44%) at 95% confidence.

    A standard worked example; the bounds are quoted in this form in most
    references that recommend Wilson over Wald.
    """
    interval = wilson_interval(10, 100)
    assert interval.low == pytest.approx(5.52, abs=0.01)
    assert interval.high == pytest.approx(17.44, abs=0.01)
    assert interval.confidence == pytest.approx(0.95)
    assert interval.width == pytest.approx(interval.high - interval.low)


def test_wilson_interval_stays_inside_the_unit_range_at_the_extremes() -> None:
    """The reason Wilson is used here: Wald escapes [0, 100] near 0% and 100%."""
    at_zero = wilson_interval(0, 40)
    assert at_zero.low == pytest.approx(0.0, abs=1e-12)
    assert 0.0 < at_zero.high < 100.0

    at_one = wilson_interval(40, 40)
    assert at_one.high == pytest.approx(100.0, abs=1e-12)
    assert 0.0 < at_one.low < 100.0


def test_wilson_interval_on_an_empty_sample_is_maximally_wide() -> None:
    """``n = 0`` yields (0, 100) — the widest honest claim about no data."""
    interval = wilson_interval(0, 0)
    assert interval.low == pytest.approx(0.0)
    assert interval.high == pytest.approx(100.0)


def test_wilson_interval_narrows_as_the_sample_grows() -> None:
    """Precision improves with n at a fixed rate, which is the whole point."""
    widths = [wilson_interval(n // 10, n).width for n in (50, 500, 5_000, 50_000)]
    assert widths == sorted(widths, reverse=True)


def test_wilson_interval_brackets_the_point_estimate() -> None:
    """The observed rate must lie inside its own interval."""
    for successes, n in ((1, 50), (17, 200), (450, 1_000)):
        interval = wilson_interval(successes, n)
        rate_pct = 100.0 * successes / n
        assert interval.low <= rate_pct <= interval.high


@pytest.mark.parametrize(
    ("successes", "n", "confidence"),
    [(-1, 10, 0.95), (11, 10, 0.95), (5, 10, 0.0), (5, 10, 1.0), (5, 10, -0.5)],
)
def test_wilson_interval_rejects_impossible_input(
    successes: int, n: int, confidence: float
) -> None:
    """Bad input raises rather than returning a plausible-looking interval."""
    with pytest.raises(ValueError, match=r"successes|confidence|n"):
        wilson_interval(successes, n, confidence)


def test_wider_confidence_gives_a_wider_interval() -> None:
    """99% must bracket 95%, which must bracket 80%."""
    narrow = wilson_interval(30, 200, 0.80)
    middle = wilson_interval(30, 200, 0.95)
    wide = wilson_interval(30, 200, 0.99)
    assert narrow.width < middle.width < wide.width
    assert wide.low <= middle.low <= narrow.low
    assert narrow.high <= middle.high <= wide.high


# ---------------------------------------------------------------------------
# Two-proportion comparison
# ---------------------------------------------------------------------------


def test_compare_proportions_detects_a_large_real_difference() -> None:
    """A 10-point lift on 2,000 users per arm is unambiguous."""
    result = compare_proportions(200, 2_000, 400, 2_000)
    assert result.control_rate_pct == pytest.approx(10.0)
    assert result.variant_rate_pct == pytest.approx(20.0)
    assert result.absolute_lift_pp == pytest.approx(10.0)
    assert result.relative_lift_pct == pytest.approx(100.0)
    assert result.z_statistic is not None
    assert result.z_statistic > 0
    assert result.p_value < 1e-15
    assert result.is_significant
    assert result.verdict is Verdict.WINNER
    assert not result.intervals_overlap


def test_compare_proportions_reports_a_significant_loss_as_loser() -> None:
    """A negative result is a result. One seeded experiment is genuinely negative."""
    result = compare_proportions(400, 2_000, 200, 2_000)
    assert result.absolute_lift_pp == pytest.approx(-10.0)
    assert result.relative_lift_pct == pytest.approx(-50.0)
    assert result.is_significant
    assert result.verdict is Verdict.LOSER


def test_compare_proportions_calls_a_flat_result_inconclusive() -> None:
    """Identical rates on large arms: no effect, and enough data to say so."""
    result = compare_proportions(200, 2_000, 200, 2_000)
    assert result.absolute_lift_pp == pytest.approx(0.0)
    assert result.z_statistic == pytest.approx(0.0)
    assert result.p_value == pytest.approx(1.0)
    assert not result.is_significant
    assert result.verdict is Verdict.INCONCLUSIVE


def test_compare_proportions_is_underpowered_below_the_minimum_arm_size() -> None:
    """Too few users means no verdict, not a verdict of "no difference"."""
    result = compare_proportions(1, 10, 5, 10)
    assert result.verdict is Verdict.UNDERPOWERED
    assert not result.is_significant


def test_minimum_arm_size_boundary_is_inclusive() -> None:
    """Exactly ``MIN_ARM_SIZE`` per arm is enough to offer a verdict.

    Pinned because an off-by-one here silently relabels a whole class of small
    experiments as underpowered.
    """
    at_floor = compare_proportions(MIN_ARM_SIZE // 2, MIN_ARM_SIZE, MIN_ARM_SIZE // 2, MIN_ARM_SIZE)
    below = compare_proportions(
        MIN_ARM_SIZE // 2, MIN_ARM_SIZE - 1, MIN_ARM_SIZE // 2, MIN_ARM_SIZE - 1
    )
    assert at_floor.verdict is not Verdict.UNDERPOWERED
    assert below.verdict is Verdict.UNDERPOWERED


def test_relative_lift_is_none_when_control_converted_nobody() -> None:
    """The ratio's denominator is zero, so the value is undefined — not infinite.

    Distinct from a relative lift of -100%, which is a real measurement meaning
    the *treatment* converted nobody. Both cases are asserted here so the two
    encodings cannot be conflated.
    """
    control_empty = compare_proportions(0, 500, 25, 500)
    assert control_empty.relative_lift_pct is None
    assert control_empty.absolute_lift_pp == pytest.approx(5.0)

    variant_empty = compare_proportions(25, 500, 0, 500)
    assert variant_empty.relative_lift_pct == pytest.approx(-100.0)


def test_z_statistic_is_none_when_pooled_variance_is_zero() -> None:
    """Both arms at exactly the same extreme: no evidence, and no test to run."""
    both_zero = compare_proportions(0, 400, 0, 400)
    assert both_zero.z_statistic is None
    assert both_zero.p_value == pytest.approx(1.0)
    assert not both_zero.is_significant

    both_full = compare_proportions(400, 400, 400, 400)
    assert both_full.z_statistic is None
    assert both_full.p_value == pytest.approx(1.0)


def test_compare_proportions_is_antisymmetric_in_its_arms() -> None:
    """Swapping the arms flips the lift's sign and leaves the p-value alone."""
    forward = compare_proportions(150, 1_000, 200, 1_000)
    reverse = compare_proportions(200, 1_000, 150, 1_000)
    assert forward.absolute_lift_pp == pytest.approx(-reverse.absolute_lift_pp)
    assert forward.p_value == pytest.approx(reverse.p_value)
    assert forward.z_statistic is not None
    assert reverse.z_statistic is not None
    assert forward.z_statistic == pytest.approx(-reverse.z_statistic)


def test_non_overlapping_intervals_imply_significance() -> None:
    """The one-directional implication that actually holds.

    Overlapping intervals do *not* imply non-significance — the difference has
    its own, narrower interval — so only this direction is asserted.
    """
    result = compare_proportions(100, 1_000, 200, 1_000)
    if not result.intervals_overlap:
        assert result.is_significant


def test_alpha_governs_the_significance_decision() -> None:
    """A borderline result flips with the threshold, and only with it."""
    lenient = compare_proportions(100, 1_000, 128, 1_000, alpha=0.10)
    strict = compare_proportions(100, 1_000, 128, 1_000, alpha=0.001)
    assert lenient.p_value == pytest.approx(strict.p_value)
    assert lenient.is_significant
    assert not strict.is_significant


@pytest.mark.parametrize(
    ("c_succ", "c_n", "v_succ", "v_n"),
    [(-1, 10, 5, 10), (11, 10, 5, 10), (5, 10, -1, 10), (5, 10, 11, 10)],
)
def test_compare_proportions_rejects_impossible_counts(
    c_succ: int, c_n: int, v_succ: int, v_n: int
) -> None:
    """Successes outside ``[0, n]`` is a caller bug, not a datum."""
    with pytest.raises(ValueError, match=r"successes|n"):
        compare_proportions(c_succ, c_n, v_succ, v_n)


# ---------------------------------------------------------------------------
# Power and sizing
# ---------------------------------------------------------------------------


def test_observed_power_is_high_for_an_obvious_effect() -> None:
    """A doubled rate on 5,000 per arm would be detected essentially always."""
    assert observed_power(0.10, 0.20, 5_000, 5_000) > 0.99


def test_observed_power_is_exactly_zero_for_an_identical_pair_of_arms() -> None:
    """A measured effect of exactly zero reports power 0.0, by deliberate choice.

    Textbook power at a true null equals alpha — you reject alpha of the time by
    construction. This function returns ``0.0`` instead, and both the docstring
    and an inline comment record why: the one-tailed formula it uses would yield
    ``alpha / 2`` at a null effect, and reporting 2.5% "power" for a result with
    no signal in it reads as a measurement rather than the absence of one.

    Asserted here because it is a contract the frontend depends on to tell a flat
    result apart from an underpowered one, and because the live dataset shows it:
    the onboarding experiment, with both arms at exactly 0%, reports power 0.0.
    """
    assert observed_power(0.10, 0.10, 5_000, 5_000) == 0.0


def test_observed_power_floors_near_alpha_over_two_for_a_vanishing_effect() -> None:
    """Approaching a null effect from above tends to ``alpha / 2``, not to zero.

    This is the other side of the discontinuity above: the limit of the formula
    as the effect shrinks is ``Phi(-z_crit) = alpha / 2``, and only the exact-zero
    case short-circuits. Pinning both sides means neither can be changed silently.
    """
    tiny = observed_power(0.10, 0.100_001, 5_000, 5_000)
    assert tiny == pytest.approx(DEFAULT_ALPHA / 2.0, abs=0.005)


def test_observed_power_is_bounded_to_a_probability() -> None:
    """It is a probability, so it lives in [0, 1] for every input."""
    for control, variant, n in ((0.001, 0.9, 10), (0.5, 0.5, 1), (0.0, 1.0, 100_000)):
        assert 0.0 <= observed_power(control, variant, n, n) <= 1.0


def test_observed_power_grows_with_sample_size() -> None:
    """Same effect, more users, more power. Monotone by construction."""
    powers = [observed_power(0.10, 0.12, n, n) for n in (100, 1_000, 10_000, 100_000)]
    assert powers == sorted(powers)


def test_mde_inverts_required_sample_size_up_to_its_documented_variance_choice() -> None:
    """The round trip recovers the lift scaled by a *predictable* variance ratio.

    These two functions are not exact inverses, and the asymmetry is documented
    rather than accidental: :func:`required_sample_size` knows both arms, so it
    uses ``p0(1-p0) + p1(1-p1)``, while :func:`minimum_detectable_effect` is given
    only a baseline and must estimate with ``2 * p0(1-p0)``. Exact inversion would
    mean solving for a lift that appears inside the variance term as well as the
    numerator.

    Rather than loosen a tolerance until that gap fits inside it, this asserts the
    gap itself. Dropping the ``ceil``, ``n = k^2 * V_n / L^2`` and
    ``MDE = k * sqrt(V_m / n)``, so the recovered lift is exactly
    ``L * sqrt(V_m / V_n)`` — independent of ``k``, and therefore of alpha and
    power. That is a sharper claim than approximate equality: it pins the variance
    expression in *both* functions, and a factor error in either one breaks it.
    """
    baseline = 0.10
    v_mde = 2.0 * baseline * (1.0 - baseline)

    for lift in (0.01, 0.02, 0.05):
        variant = baseline + lift
        v_required = baseline * (1.0 - baseline) + variant * (1.0 - variant)
        predicted = lift * math.sqrt(v_mde / v_required)

        n = required_sample_size(baseline, lift)
        recovered = minimum_detectable_effect(baseline, n)

        # Tight: only the rounding of n up to a whole user separates the two.
        assert recovered == pytest.approx(predicted, rel=1e-3)
        # And the bias has a direction. Raising a rate of 0.10 moves it toward
        # 0.5, so the true variance exceeds the baseline estimate, so the sample
        # is larger than MDE's formula assumes and the recovered lift is
        # conservative. Never optimistic, which is the direction that would
        # matter: it would claim a test can detect smaller effects than it can.
        assert recovered < lift


def test_mde_round_trip_is_exact_when_the_variance_terms_agree() -> None:
    """At a baseline of 0.5 minus half the lift, the two variance choices coincide.

    ``p0(1-p0) + p1(1-p1) == 2 * p0(1-p0)`` whenever ``p1`` and ``p0`` are
    equidistant from 0.5, because the variance function is symmetric about it.
    Choosing such a pair removes the approximation entirely and the round trip
    becomes exact — which confirms the gap above is only ever that variance
    choice, and not a second error hiding behind it.
    """
    lift = 0.04
    baseline = 0.5 - lift / 2.0  # 0.48 -> variant 0.52, mirrored about 0.5

    n = required_sample_size(baseline, lift)
    recovered = minimum_detectable_effect(baseline, n)

    assert recovered == pytest.approx(lift, rel=1e-3)


def test_required_sample_size_ignores_the_sign_of_the_lift() -> None:
    """The test is two-sided, so detecting -2pp needs the same n as +2pp."""
    assert required_sample_size(0.2, 0.02) == required_sample_size(0.2, -0.02)


def test_required_sample_size_grows_as_the_effect_shrinks() -> None:
    """Smaller effects need larger samples, roughly as the inverse square."""
    sizes = [required_sample_size(0.10, lift) for lift in (0.08, 0.04, 0.02, 0.01)]
    assert sizes == sorted(sizes)
    # Halving the effect should roughly quadruple the requirement.
    assert sizes[2] / sizes[1] == pytest.approx(4.0, rel=0.15)


def test_required_sample_size_returns_whole_users() -> None:
    """You cannot enrol 43.7 people."""
    n = required_sample_size(0.1, 0.015)
    assert isinstance(n, int)
    assert n > 0


def test_mde_shrinks_as_the_sample_grows() -> None:
    """More users can detect smaller effects."""
    effects = [minimum_detectable_effect(0.10, n) for n in (100, 1_000, 10_000)]
    assert effects == sorted(effects, reverse=True)


# ---------------------------------------------------------------------------
# Welch's t-test
# ---------------------------------------------------------------------------


def test_compare_means_detects_a_clear_difference() -> None:
    """Well-separated means with tight spreads and healthy n."""
    result = compare_means(10.0, 2.0, 500, 12.0, 2.0, 500)
    assert result.difference == pytest.approx(2.0)
    assert result.t_statistic is not None
    assert result.p_value < 1e-10
    assert result.is_significant


def test_compare_means_uses_welch_degrees_of_freedom() -> None:
    """Unequal variances must not collapse to ``n1 + n2 - 2``.

    Welch-Satterthwaite gives a non-integer df strictly below the pooled value;
    asserting that distinguishes Welch from Student, which is the documented
    reason this function exists.
    """
    result = compare_means(10.0, 1.0, 50, 12.0, 8.0, 50)
    pooled_df = 50 + 50 - 2
    assert result.degrees_freedom < pooled_df
    assert result.degrees_freedom != pytest.approx(float(pooled_df))


def test_compare_means_with_zero_variance_in_both_arms() -> None:
    """No spread anywhere means no t statistic — the same shape as the z case."""
    result = compare_means(10.0, 0.0, 30, 12.0, 0.0, 30)
    assert result.t_statistic is None
    assert result.p_value == pytest.approx(1.0)
    assert not result.is_significant


def test_compare_means_is_antisymmetric_in_its_arms() -> None:
    """Swapping arms negates the difference and the statistic, not the p-value."""
    forward = compare_means(10.0, 3.0, 200, 11.0, 3.5, 180)
    reverse = compare_means(11.0, 3.5, 180, 10.0, 3.0, 200)
    assert forward.difference == pytest.approx(-reverse.difference)
    assert forward.p_value == pytest.approx(reverse.p_value)
    assert forward.degrees_freedom == pytest.approx(reverse.degrees_freedom)

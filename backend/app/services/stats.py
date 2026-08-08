"""Significance testing for A/B experiments. Pure functions, no I/O.

``experiments/experiment_variant_metrics.sql`` deliberately stops at numerators and
denominators — ``n`` and ``successes`` per variant — and this module turns them into
a z-statistic, a p-value, a confidence interval and a verdict. The split exists
because significance testing is a decision rule wrapped around an inverse normal
CDF, and neither of those belongs in SQL where they cannot be unit-tested.

Everything here takes numbers and returns numbers. No session, no cache, no
settings. That makes this the one module in the service layer that is fully
testable without a database, which is exactly what phase 12 needs: the seeded
dataset contains two experiments whose true lift is exactly 0.0 and one whose lift
is negative, so these functions have a known correct answer to be checked against
rather than merely a plausible one.

Counts in, not rates
--------------------
Every entry point takes integer counts. The SQL also returns ``rate_pct`` rounded
to three decimals, and using it would be wrong: rounding perturbs the numerator
before it reaches the variance term, which shifts the z-statistic and can move a
borderline result across the threshold. The rates reported back are recomputed here
from the integers.

No scipy
--------
The project pins its dependencies exactly and scipy is not among them; adding it
for four distribution functions would be a change to the frozen architecture for a
few hundred lines of well-understood numerics. So the normal CDF comes from
:func:`math.erf`, the Student *t* CDF from a regularised incomplete beta evaluated
by continued fraction, and the inverse normal CDF from bisection refined by
Newton's method against the forward CDF.

That last choice is deliberate. The usual approach is a rational approximation with
a memorised coefficient table (Acklam, AS 241), which is faster and is also the
single easiest place in this file to introduce a silent numerical error that no
test would catch. Bisect-then-Newton is self-checking: it converges against the
exact forward function, so it cannot disagree with it. These functions are called
once per experiment, so the microseconds are irrelevant.

Choices a reviewer should be able to argue with
-----------------------------------------------
* **Wilson intervals, not Wald.** The Wald interval on a proportion is symmetric
  and can extend below zero or above one, which for a 0.8% conversion rate is not
  a rounding artefact but a nonsense interval. Wilson is asymmetric, stays in
  bounds, and behaves at small ``n`` where these experiments actually live.
* **Pooled variance for the test, unpooled for the intervals.** The z-test assumes
  the null — that both arms share one rate — so it pools. The intervals describe
  each arm as it is, so they do not. Using one variance for both would be
  internally consistent and wrong in one direction or the other.
* **Two-sided by default.** A one-sided test is a way to halve a p-value after the
  fact. A variant that loses is a finding worth reporting, so the test looks both
  ways.
* **Practical significance is reported separately from statistical significance.**
  With enough traffic a 0.01pp lift becomes significant and remains worthless.
  :attr:`ProportionTest.is_significant` answers only "is this distinguishable from
  noise"; the caller decides whether the size of the effect justifies anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
import math
from typing import Final

#: Default two-sided significance level.
DEFAULT_ALPHA: Final[float] = 0.05

#: Default power target for sample-size and MDE calculations. 0.8 is convention,
#: not law, and is exposed as an argument for that reason.
DEFAULT_POWER: Final[float] = 0.80

#: Below this many users per arm, a normal approximation is not trustworthy and no
#: verdict is reported. The usual textbook rule is at least five expected successes
#: and five expected failures per arm; this is the cruder guard that catches the
#: case where an experiment simply has not run long enough.
MIN_ARM_SIZE: Final[int] = 30

#: Convergence tolerance and iteration ceiling for the continued fraction.
_EPS: Final[float] = 3.0e-16
_MAX_ITER: Final[int] = 300

#: Guard against division by a denominator that has underflowed to zero.
_TINY: Final[float] = 1.0e-30

#: Bracket width at which :func:`normal_quantile` stops bisecting and hands over to
#: Newton. Loose on purpose — Newton converges quadratically from here, so tightening
#: it would only spend iterations doing the slower method's job.
_BISECT_TOLERANCE: Final[float] = 1.0e-6

#: Newton step size below which :func:`normal_quantile` is at machine precision and
#: further iterations would only move the last bit back and forth.
_NEWTON_TOLERANCE: Final[float] = 1.0e-15

#: Fewest observations per arm Welch's t-test can accept. Its degrees of freedom
#: divide by ``n - 1`` in both arms, so one observation gives no variance estimate.
_MIN_MEANS_ARM_SIZE: Final[int] = 2


# ---------------------------------------------------------------------------
# Distribution functions
# ---------------------------------------------------------------------------


def normal_cdf(z: float) -> float:
    """Return the standard normal cumulative probability at ``z``.

    Args:
        z: Standard score.

    Returns:
        ``P(Z <= z)``, in ``[0, 1]``.
    """
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _normal_pdf(z: float) -> float:
    """Return the standard normal density at ``z``.

    Args:
        z: Standard score.

    Returns:
        The density, used as the derivative in Newton's method.
    """
    return math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)


@lru_cache(maxsize=64)
def normal_quantile(p: float) -> float:
    """Return the standard normal quantile: the ``z`` with ``P(Z <= z) == p``.

    Bisection to bracket, then Newton's method against :func:`normal_cdf` to
    converge. Cached because callers ask for the same handful of levels — 0.975 for
    a 95% two-sided test, 0.80 for conventional power — over and over.

    Args:
        p: Cumulative probability, strictly between 0 and 1.

    Returns:
        The quantile, accurate to roughly machine precision.

    Raises:
        ValueError: If ``p`` is not strictly inside ``(0, 1)``. Infinite quantiles
            are a programming error here, never a meaningful result.
    """
    if not 0.0 < p < 1.0:
        raise ValueError(f"normal_quantile requires 0 < p < 1, got {p!r}.")

    # ±40 standard deviations brackets any probability representable in a float:
    # normal_cdf(-40) underflows to 0.0 and normal_cdf(40) rounds to 1.0.
    low, high = -40.0, 40.0
    for _ in range(64):
        middle = 0.5 * (low + high)
        if normal_cdf(middle) < p:
            low = middle
        else:
            high = middle
        if high - low < _BISECT_TOLERANCE:
            break

    z = 0.5 * (low + high)
    # Newton polish. The density can underflow in the far tail, so the step is
    # skipped rather than dividing by zero; bisection has already delivered ample
    # accuracy by that point.
    for _ in range(6):
        density = _normal_pdf(z)
        if density < _TINY:
            break
        step = (normal_cdf(z) - p) / density
        z -= step
        if abs(step) < _NEWTON_TOLERANCE:
            break

    return z


def _log_beta(a: float, b: float) -> float:
    """Return the natural log of the beta function ``B(a, b)``.

    Args:
        a: First shape parameter, positive.
        b: Second shape parameter, positive.

    Returns:
        ``ln B(a, b)``, computed through log-gamma so that large shapes do not
        overflow.
    """
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    """Evaluate the continued fraction for the incomplete beta function.

    Modified Lentz's method. Only converges quickly for ``x`` below
    ``(a + 1) / (a + b + 2)``; :func:`_incomplete_beta` applies the symmetry
    transform to guarantee that.

    Args:
        a: First shape parameter.
        b: Second shape parameter.
        x: Evaluation point in ``(0, 1)``.

    Returns:
        The continued fraction's value.
    """
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0

    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _TINY:
        d = _TINY
    d = 1.0 / d
    result = d

    for m in range(1, _MAX_ITER + 1):
        m2 = 2 * m

        # Even step.
        numerator = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + numerator * d
        if abs(d) < _TINY:
            d = _TINY
        c = 1.0 + numerator / c
        if abs(c) < _TINY:
            c = _TINY
        d = 1.0 / d
        result *= d * c

        # Odd step.
        numerator = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + numerator * d
        if abs(d) < _TINY:
            d = _TINY
        c = 1.0 + numerator / c
        if abs(c) < _TINY:
            c = _TINY
        d = 1.0 / d
        delta = d * c
        result *= delta

        if abs(delta - 1.0) < _EPS:
            break

    return result


def _incomplete_beta(a: float, b: float, x: float) -> float:
    """Return the regularised incomplete beta function ``I_x(a, b)``.

    Args:
        a: First shape parameter, positive.
        b: Second shape parameter, positive.
        x: Evaluation point in ``[0, 1]``.

    Returns:
        ``I_x(a, b)``, in ``[0, 1]``.
    """
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0

    log_prefix = a * math.log(x) + b * math.log1p(-x) - _log_beta(a, b)

    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(log_prefix) * _beta_continued_fraction(a, b, x) / a

    # Symmetry: I_x(a, b) = 1 - I_{1-x}(b, a). The log prefix is unchanged because
    # ln B is symmetric in its arguments.
    return 1.0 - math.exp(log_prefix) * _beta_continued_fraction(b, a, 1.0 - x) / b


def student_t_cdf(t: float, df: float) -> float:
    """Return the Student *t* cumulative probability at ``t``.

    Args:
        t: Test statistic.
        df: Degrees of freedom, positive. Fractional values are expected — Welch's
            correction rarely produces an integer.

    Returns:
        ``P(T <= t)``, in ``[0, 1]``.

    Raises:
        ValueError: If ``df`` is not positive.
    """
    if df <= 0.0:
        raise ValueError(f"student_t_cdf requires df > 0, got {df!r}.")

    tail = 0.5 * _incomplete_beta(0.5 * df, 0.5, df / (df + t * t))
    return 1.0 - tail if t > 0.0 else tail


# ---------------------------------------------------------------------------
# Two-sided tail probabilities
# ---------------------------------------------------------------------------
#
# Both are computed directly rather than as ``2 * (1 - cdf(|statistic|))``, and the
# difference is not cosmetic. A CDF near 1 has absolute precision around 1e-16, so
# subtracting it from 1 discards every digit below that: at ``z = 14.9`` the true
# p-value is about 3e-50 and the subtraction yields exactly 0.0. A p-value is never
# zero, and one printed as such invites a reader to trust it as exact.


#: Floor for a reported p-value. Beyond roughly ``z = 38`` the true tail probability
#: is smaller than the smallest positive double and any computation of it underflows
#: to exactly 0.0. Reporting that floor instead keeps the invariant these functions
#: exist to defend — a p-value is never zero — and it costs nothing in meaning,
#: since every value at this magnitude says the same thing. The floor is a floor and
#: not a measurement: a result equal to it means "smaller than a double can hold",
#: which is why it is named rather than inlined.
MIN_P_VALUE: Final[float] = 1.0e-308


def two_sided_normal_p(z: float) -> float:
    """Return the two-sided normal p-value for a z-statistic.

    Mathematically ``2 * (1 - normal_cdf(|z|))``, evaluated as a complementary
    error function so no cancellation occurs — ``erfc`` computes the tail itself
    rather than reconstructing it from its complement.

    Args:
        z: Standard score.

    Returns:
        ``P(|Z| >= |z|)``, in ``(0, 1]``. Full relative accuracy down to about
        1e-300; below that, and for ``|z|`` beyond roughly 38 where even ``erfc``
        underflows, :data:`MIN_P_VALUE` is returned.
    """
    tail = math.erfc(abs(z) / math.sqrt(2.0))
    return tail if tail > MIN_P_VALUE else MIN_P_VALUE


def two_sided_t_p(t: float, df: float) -> float:
    """Return the two-sided Student *t* p-value.

    The two-sided p-value is exactly the regularised incomplete beta that
    :func:`student_t_cdf` computes and then subtracts from one, so calling it
    directly is both simpler and free of the cancellation described above.

    Args:
        t: Test statistic.
        df: Degrees of freedom, positive.

    Note that this exceeds :func:`two_sided_normal_p` at the same statistic, and by
    a wide margin in the tail: at ``t = 14.9`` with 200,000 degrees of freedom the
    *t* tail is some 6% fatter. That is not an inaccuracy to be reconciled — heavier
    tails at finite degrees of freedom is the entire reason to prefer a *t*-test
    over a z-test on a sample.

    Args:
        t: Test statistic.
        df: Degrees of freedom, positive.

    Returns:
        ``P(|T| >= |t|)``, in ``(0, 1]``, floored at :data:`MIN_P_VALUE` for the
        same reason as :func:`two_sided_normal_p`.

    Raises:
        ValueError: If ``df`` is not positive.
    """
    if df <= 0.0:
        raise ValueError(f"two_sided_t_p requires df > 0, got {df!r}.")

    tail = _incomplete_beta(0.5 * df, 0.5, df / (df + t * t))
    return tail if tail > MIN_P_VALUE else MIN_P_VALUE


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class Verdict(StrEnum):
    """The conclusion of a comparison, as a label the frontend can render directly.

    Attributes:
        WINNER: The variant beat control at the chosen significance level.
        LOSER: The variant lost significantly. Reported rather than buried; one of
            the seeded experiments is genuinely negative.
        INCONCLUSIVE: The difference is not distinguishable from noise. This is a
            real result, not a failure — a flat test tells you to stop spending on
            the idea.
        UNDERPOWERED: Too few users per arm for the normal approximation to hold.
            No verdict is offered, because the honest answer is "wait".
    """

    WINNER = "winner"
    LOSER = "loser"
    INCONCLUSIVE = "inconclusive"
    UNDERPOWERED = "underpowered"


@dataclass(frozen=True, slots=True)
class Interval:
    """A confidence interval on a proportion, in percentage points.

    Attributes:
        low: Lower bound, clamped to 0.
        high: Upper bound, clamped to 100.
        confidence: The level, e.g. 0.95.
    """

    low: float
    high: float
    confidence: float

    @property
    def width(self) -> float:
        """Return the interval's width in percentage points.

        A useful proxy for precision: a 40-point-wide interval on a conversion rate
        means the experiment has not yet measured anything.
        """
        return self.high - self.low


@dataclass(frozen=True, slots=True)
class ProportionTest:
    """The outcome of a two-proportion comparison between one arm and control.

    Attributes:
        control_n: Users in control.
        control_successes: Converting users in control.
        control_rate_pct: Control conversion rate, in percent.
        control_interval: Wilson interval on the control rate.
        variant: Variant label.
        variant_n: Users in the variant.
        variant_successes: Converting users in the variant.
        variant_rate_pct: Variant conversion rate, in percent.
        variant_interval: Wilson interval on the variant rate.
        absolute_lift_pp: Variant rate minus control rate, in percentage points.
        relative_lift_pct: The same difference as a percentage of the control rate.
            ``None`` when control converted nobody, where the ratio is undefined
            rather than infinite.
        z_statistic: The pooled two-proportion z. ``None`` when the pooled variance
            is zero, which happens when both arms sit at exactly 0% or exactly 100%
            — there is no evidence of a difference, and also no test to run.
        p_value: Two-sided p-value. 1.0 when no statistic could be computed.
        alpha: The significance level used.
        is_significant: Whether ``p_value < alpha`` *and* both arms met
            :data:`MIN_ARM_SIZE`.
        verdict: The overall conclusion.
        observed_power: Post-hoc power at the observed effect size. Reported for
            inconclusive tests to separate "no effect" from "not enough data", and
            deliberately not used to decide significance — post-hoc power is a
            transform of the p-value, so testing against it would be circular.
    """

    control_n: int
    control_successes: int
    control_rate_pct: float
    control_interval: Interval
    variant: str
    variant_n: int
    variant_successes: int
    variant_rate_pct: float
    variant_interval: Interval
    absolute_lift_pp: float
    relative_lift_pct: float | None
    z_statistic: float | None
    p_value: float
    alpha: float
    is_significant: bool
    verdict: Verdict
    observed_power: float

    @property
    def intervals_overlap(self) -> bool:
        """Return whether the two arms' confidence intervals overlap.

        A visual sanity check for the chart, not a test. Non-overlapping intervals
        imply significance, but overlapping ones do not imply its absence — the
        difference has its own, narrower interval.
        """
        return (
            self.control_interval.low <= self.variant_interval.high
            and self.variant_interval.low <= self.control_interval.high
        )


@dataclass(frozen=True, slots=True)
class MeansTest:
    """The outcome of a Welch two-sample *t*-test.

    Present for the metrics the experiment query binarises — sessions per user,
    session duration — so that a caller holding real summary statistics can test
    them properly instead. Nothing in the current query set produces the standard
    deviations this needs, so it is unused by the 48 queries and exists for the
    analysis write-up in phase 12.

    Attributes:
        control_n: Control sample size.
        control_mean: Control mean.
        control_sd: Control sample standard deviation.
        variant_n: Variant sample size.
        variant_mean: Variant mean.
        variant_sd: Variant sample standard deviation.
        difference: Variant mean minus control mean.
        t_statistic: Welch's t. ``None`` when both arms have zero variance.
        degrees_freedom: Welch-Satterthwaite degrees of freedom.
        p_value: Two-sided p-value.
        alpha: The significance level used.
        is_significant: Whether ``p_value < alpha``.
    """

    control_n: int
    control_mean: float
    control_sd: float
    variant_n: int
    variant_mean: float
    variant_sd: float
    difference: float
    t_statistic: float | None
    degrees_freedom: float
    p_value: float
    alpha: float
    is_significant: bool


# ---------------------------------------------------------------------------
# Proportions
# ---------------------------------------------------------------------------


def wilson_interval(
    successes: int,
    n: int,
    confidence: float = 1.0 - DEFAULT_ALPHA,
) -> Interval:
    """Return the Wilson score interval on a proportion, in percentage points.

    Preferred over the Wald interval because it stays inside ``[0, 1]`` and remains
    sensible when the rate is near an extreme — which is where a subscription
    conversion rate of one or two percent lives.

    Args:
        successes: Number of successes.
        n: Sample size.
        confidence: Confidence level, strictly between 0 and 1.

    Returns:
        The interval, in percent. ``(0, 100)`` when ``n`` is zero, which is the
        widest honest statement about a sample that does not exist.

    Raises:
        ValueError: If ``successes`` is negative or exceeds ``n``, or if
            ``confidence`` is out of range.
    """
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be strictly between 0 and 1, got {confidence!r}.")
    if successes < 0 or n < 0:
        raise ValueError(f"successes and n must be non-negative, got {successes!r}, {n!r}.")
    if successes > n:
        raise ValueError(f"successes ({successes}) cannot exceed n ({n}).")

    if n == 0:
        return Interval(low=0.0, high=100.0, confidence=confidence)

    z = normal_quantile(1.0 - (1.0 - confidence) / 2.0)
    p = successes / n
    z_squared_over_n = z * z / n

    denominator = 1.0 + z_squared_over_n
    centre = (p + z_squared_over_n / 2.0) / denominator
    spread = (z / denominator) * math.sqrt(p * (1.0 - p) / n + z_squared_over_n / (4.0 * n))

    # Clamping is belt-and-braces: Wilson cannot leave [0, 1] analytically, but
    # floating-point can put a bound at 1.0000000000000002, which renders as a
    # 100.00000000000001% conversion rate.
    return Interval(
        low=max(0.0, (centre - spread) * 100.0),
        high=min(100.0, (centre + spread) * 100.0),
        confidence=confidence,
    )


def compare_proportions(
    control_successes: int,
    control_n: int,
    variant_successes: int,
    variant_n: int,
    *,
    variant: str = "variant",
    alpha: float = DEFAULT_ALPHA,
    min_arm_size: int = MIN_ARM_SIZE,
) -> ProportionTest:
    """Compare a variant's conversion rate against control.

    A two-sided, pooled two-proportion z-test with Wilson intervals on each arm.

    Args:
        control_successes: Converting users in control.
        control_n: Users in control.
        variant_successes: Converting users in the variant.
        variant_n: Users in the variant.
        variant: Variant label, carried through to the result.
        alpha: Two-sided significance level.
        min_arm_size: Below this many users in either arm the result is reported as
            :attr:`Verdict.UNDERPOWERED` and never as significant.

    Returns:
        The full comparison.

    Raises:
        ValueError: If either arm has negative counts, more successes than users,
            or if ``alpha`` is not strictly inside ``(0, 1)``.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be strictly between 0 and 1, got {alpha!r}.")
    for label, successes, n in (
        ("control", control_successes, control_n),
        (variant, variant_successes, variant_n),
    ):
        if successes < 0 or n < 0:
            raise ValueError(f"{label} counts must be non-negative, got {successes}/{n}.")
        if successes > n:
            raise ValueError(f"{label} successes ({successes}) cannot exceed n ({n}).")

    confidence = 1.0 - alpha
    control_rate = control_successes / control_n if control_n else 0.0
    variant_rate = variant_successes / variant_n if variant_n else 0.0

    absolute_lift_pp = (variant_rate - control_rate) * 100.0
    relative_lift_pct = (
        (variant_rate - control_rate) / control_rate * 100.0 if control_rate > 0.0 else None
    )

    # Pooled proportion: the null hypothesis is that both arms share one rate, so
    # the variance under the null is estimated from the combined data.
    z_statistic: float | None = None
    p_value = 1.0
    total_n = control_n + variant_n
    if total_n > 0:
        pooled = (control_successes + variant_successes) / total_n
        if 0.0 < pooled < 1.0 and control_n > 0 and variant_n > 0:
            standard_error = math.sqrt(
                pooled * (1.0 - pooled) * (1.0 / control_n + 1.0 / variant_n)
            )
            if standard_error > 0.0:
                z_statistic = (variant_rate - control_rate) / standard_error
                p_value = two_sided_normal_p(z_statistic)

    underpowered = control_n < min_arm_size or variant_n < min_arm_size
    is_significant = not underpowered and z_statistic is not None and p_value < alpha

    if underpowered:
        verdict = Verdict.UNDERPOWERED
    elif not is_significant:
        verdict = Verdict.INCONCLUSIVE
    elif absolute_lift_pp > 0.0:
        verdict = Verdict.WINNER
    else:
        verdict = Verdict.LOSER

    return ProportionTest(
        control_n=control_n,
        control_successes=control_successes,
        control_rate_pct=control_rate * 100.0,
        control_interval=wilson_interval(control_successes, control_n, confidence),
        variant=variant,
        variant_n=variant_n,
        variant_successes=variant_successes,
        variant_rate_pct=variant_rate * 100.0,
        variant_interval=wilson_interval(variant_successes, variant_n, confidence),
        absolute_lift_pp=absolute_lift_pp,
        relative_lift_pct=relative_lift_pct,
        z_statistic=z_statistic,
        p_value=p_value,
        alpha=alpha,
        is_significant=is_significant,
        verdict=verdict,
        observed_power=observed_power(
            control_rate, variant_rate, control_n, variant_n, alpha=alpha
        ),
    )


def observed_power(
    control_rate: float,
    variant_rate: float,
    control_n: int,
    variant_n: int,
    *,
    alpha: float = DEFAULT_ALPHA,
) -> float:
    """Return post-hoc power at the observed effect size.

    Answers "if the true effect were the size we just measured, how often would a
    test this size detect it". Useful for telling a genuinely flat result apart
    from one that has not gathered enough data, and reported alongside an
    inconclusive verdict for that reason.

    It is not used to decide significance. Post-hoc power is a monotone transform
    of the observed p-value, so using it as a second criterion would be the same
    test counted twice.

    Args:
        control_rate: Observed control proportion, in ``[0, 1]``.
        variant_rate: Observed variant proportion, in ``[0, 1]``.
        control_n: Users in control.
        variant_n: Users in the variant.
        alpha: Two-sided significance level.

    Returns:
        Power in ``[0, 1]``. Zero when either arm is empty or the arms are
        identical, where there is no effect to have power against.
    """
    if control_n <= 0 or variant_n <= 0:
        return 0.0

    effect = abs(variant_rate - control_rate)
    if effect == 0.0:
        return 0.0

    unpooled_variance = (
        control_rate * (1.0 - control_rate) / control_n
        + variant_rate * (1.0 - variant_rate) / variant_n
    )
    if unpooled_variance <= 0.0:
        return 0.0

    critical = normal_quantile(1.0 - alpha / 2.0)
    z_effect = effect / math.sqrt(unpooled_variance)
    # One tail only. The opposite-tail contribution is negligible for any effect
    # worth reporting, and including it would inflate power for a null effect.
    return max(0.0, min(1.0, normal_cdf(z_effect - critical)))


def required_sample_size(
    baseline_rate: float,
    absolute_lift: float,
    *,
    alpha: float = DEFAULT_ALPHA,
    power: float = DEFAULT_POWER,
) -> int:
    """Return the users needed *per arm* to detect a given absolute lift.

    Args:
        baseline_rate: Expected control proportion, in ``(0, 1)``.
        absolute_lift: Lift to detect, as a proportion — ``0.01`` is one percentage
            point, not one percent. Sign is ignored; the test is two-sided.
        alpha: Two-sided significance level.
        power: Probability of detecting the effect if it is real.

    Returns:
        Users per arm, rounded up.

    Raises:
        ValueError: If ``baseline_rate`` is not in ``(0, 1)``, if the implied
            variant rate falls outside ``[0, 1]``, if ``absolute_lift`` is zero, or
            if ``alpha`` or ``power`` are out of range.
    """
    if not 0.0 < baseline_rate < 1.0:
        raise ValueError(f"baseline_rate must be strictly between 0 and 1, got {baseline_rate!r}.")
    if absolute_lift == 0.0:
        raise ValueError("absolute_lift must be non-zero; detecting no effect needs no sample.")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be strictly between 0 and 1, got {alpha!r}.")
    if not 0.0 < power < 1.0:
        raise ValueError(f"power must be strictly between 0 and 1, got {power!r}.")

    variant_rate = baseline_rate + abs(absolute_lift)
    if not 0.0 <= variant_rate <= 1.0:
        raise ValueError(
            f"baseline_rate {baseline_rate!r} plus lift {absolute_lift!r} leaves the "
            "[0, 1] range; no sample size can detect an impossible rate."
        )

    z_alpha = normal_quantile(1.0 - alpha / 2.0)
    z_power = normal_quantile(power)
    variance = baseline_rate * (1.0 - baseline_rate) + variant_rate * (1.0 - variant_rate)

    return math.ceil((z_alpha + z_power) ** 2 * variance / (absolute_lift**2))


def minimum_detectable_effect(
    baseline_rate: float,
    n_per_arm: int,
    *,
    alpha: float = DEFAULT_ALPHA,
    power: float = DEFAULT_POWER,
) -> float:
    """Return the smallest absolute lift a given sample can detect.

    The inverse of :func:`required_sample_size`, and the number that makes an
    inconclusive result interpretable: "this test could not have detected anything
    smaller than 2.4 percentage points" is a conclusion, where "not significant"
    on its own is not.

    Assumes equally sized arms and estimates the variance at the baseline rate, so
    it is an approximation — exact inversion would require solving for a lift that
    appears in the variance term as well as the numerator.

    Args:
        baseline_rate: Expected control proportion, in ``(0, 1)``.
        n_per_arm: Users per arm.
        alpha: Two-sided significance level.
        power: Desired power.

    Returns:
        The minimum detectable absolute lift, as a proportion. Multiply by 100 for
        percentage points.

    Raises:
        ValueError: If ``baseline_rate`` is not in ``(0, 1)``, ``n_per_arm`` is not
            positive, or ``alpha`` or ``power`` are out of range.
    """
    if not 0.0 < baseline_rate < 1.0:
        raise ValueError(f"baseline_rate must be strictly between 0 and 1, got {baseline_rate!r}.")
    if n_per_arm <= 0:
        raise ValueError(f"n_per_arm must be positive, got {n_per_arm!r}.")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be strictly between 0 and 1, got {alpha!r}.")
    if not 0.0 < power < 1.0:
        raise ValueError(f"power must be strictly between 0 and 1, got {power!r}.")

    z_alpha = normal_quantile(1.0 - alpha / 2.0)
    z_power = normal_quantile(power)
    return (z_alpha + z_power) * math.sqrt(2.0 * baseline_rate * (1.0 - baseline_rate) / n_per_arm)


# ---------------------------------------------------------------------------
# Means
# ---------------------------------------------------------------------------


def compare_means(
    control_mean: float,
    control_sd: float,
    control_n: int,
    variant_mean: float,
    variant_sd: float,
    variant_n: int,
    *,
    alpha: float = DEFAULT_ALPHA,
) -> MeansTest:
    """Compare two means with Welch's *t*-test.

    Welch rather than Student because equal variances is an assumption nobody
    checks and engagement metrics routinely violate: a treatment that helps heavy
    users changes the spread as well as the centre.

    Args:
        control_mean: Control sample mean.
        control_sd: Control sample standard deviation.
        control_n: Control sample size, at least 2.
        variant_mean: Variant sample mean.
        variant_sd: Variant sample standard deviation.
        variant_n: Variant sample size, at least 2.
        alpha: Two-sided significance level.

    Returns:
        The comparison. When both arms have zero variance the statistic is ``None``
        and the p-value 1.0 — two constant samples carry no evidence about spread,
        even when their means differ.

    Raises:
        ValueError: If either standard deviation is negative, either sample size is
            below 2, or ``alpha`` is out of range.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be strictly between 0 and 1, got {alpha!r}.")
    if control_sd < 0.0 or variant_sd < 0.0:
        raise ValueError(
            f"standard deviations must be non-negative, got {control_sd!r}, {variant_sd!r}."
        )
    if control_n < _MIN_MEANS_ARM_SIZE or variant_n < _MIN_MEANS_ARM_SIZE:
        raise ValueError(
            f"Welch's t-test needs at least {_MIN_MEANS_ARM_SIZE} observations per "
            f"arm, got {control_n} and {variant_n}."
        )

    control_term = control_sd**2 / control_n
    variant_term = variant_sd**2 / variant_n
    combined = control_term + variant_term
    difference = variant_mean - control_mean

    if combined <= 0.0:
        return MeansTest(
            control_n=control_n,
            control_mean=control_mean,
            control_sd=control_sd,
            variant_n=variant_n,
            variant_mean=variant_mean,
            variant_sd=variant_sd,
            difference=difference,
            t_statistic=None,
            degrees_freedom=0.0,
            p_value=1.0,
            alpha=alpha,
            is_significant=False,
        )

    t_statistic = difference / math.sqrt(combined)
    degrees_freedom = combined**2 / (
        control_term**2 / (control_n - 1) + variant_term**2 / (variant_n - 1)
    )
    p_value = two_sided_t_p(t_statistic, degrees_freedom)

    return MeansTest(
        control_n=control_n,
        control_mean=control_mean,
        control_sd=control_sd,
        variant_n=variant_n,
        variant_mean=variant_mean,
        variant_sd=variant_sd,
        difference=difference,
        t_statistic=t_statistic,
        degrees_freedom=degrees_freedom,
        p_value=p_value,
        alpha=alpha,
        is_significant=p_value < alpha,
    )


__all__ = [
    "DEFAULT_ALPHA",
    "DEFAULT_POWER",
    "MIN_ARM_SIZE",
    "Interval",
    "MeansTest",
    "ProportionTest",
    "Verdict",
    "compare_means",
    "compare_proportions",
    "minimum_detectable_effect",
    "normal_cdf",
    "normal_quantile",
    "observed_power",
    "required_sample_size",
    "student_t_cdf",
    "two_sided_normal_p",
    "two_sided_t_p",
    "wilson_interval",
]

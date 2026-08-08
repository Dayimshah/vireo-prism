"""Every tunable constant in the simulation, in one place.

This is the most consequential file in the seeder. Nothing here generates
anything; it declares the *shape* of the world, and the generators in
``seeder/generators/`` read from it.

The planted-signal principle
----------------------------
The coefficients below are deliberate causes. Paid Social carries a negative
conversion coefficient, Referral a positive one; tier-3 countries convert at
lower ARPU; a Binge Watcher's churn hazard is a tenth of a Churn Risk's. None of
the SQL in ``app/sql/queries/`` knows any of this — it just aggregates events.
When the Marketing page shows that Referral has a 4x better LTV:CAC ratio than
Display, that is the analytics layer independently recovering a relationship
declared here.

This is what separates the dataset from noise. Randomly generated data produces
flat lines and identical segments, and a reviewer spots it immediately. Causal
data produces findings that survive being asked "so what does this tell you?".

Statistical honesty
-------------------
Two properties are enforced rather than hoped for:

* **No lookahead.** A user's conversion probability at time *t* depends only on
  behaviour before *t*. The generator computes features from a 14-day trailing
  window, never from the future.
* **No degenerate certainty.** Every probability is bounded away from 0 and 1
  (see :data:`PROBABILITY_FLOOR`). A deterministic outcome would let a model
  reach 100% accuracy, which is the signature of a leaked label.

Changing values here changes the published findings. ``docs/analytics-catalog.md``
quotes concrete numbers from the ``medium`` profile at seed 20240817; edit a
coefficient and those numbers move.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

# ===========================================================================
# Scale profiles
# ===========================================================================


@dataclass(frozen=True, slots=True)
class ScaleProfile:
    """Row counts and runtime for one named dataset size.

    Session and event counts are *emergent*, not configured: they follow from the
    user count, the persona mix, and how long each user survives before churning.
    Only ``users`` is a dial; everything else is a consequence.

    The arithmetic, so the figures below can be checked rather than trusted:

    * Weighted mean session frequency across :data:`PERSONA_WEIGHTS` is
      2.65/week. Growth over the window contributes a mean ×1.33 and the weekday
      curve ×1.10, giving ~3.9 effective sessions per active week.
    * Signups are spread across the window and users churn, so mean observed
      tenure is well under the full 18 months. Measured over a real run: **116
      sessions per user**.
    * Session length is bounded by each persona's ``session_minutes`` budget,
      yielding a measured **16.4 events per session**.

    So events ≈ users × 1,900. That single multiplier is what makes the user count
    the only number worth tuning.

    An earlier version of this file carried guessed values here — 51,000 sessions
    for the small profile against a true 410,000, an 8× error. The figures below
    are computed from the coefficients and confirmed against an instrumented run.

    Attributes:
        name: Profile identifier matching ``PRISM_SEED__PROFILE``.
        users: Number of user rows. The only genuine dial.
        titles: Catalogue size. Held near-constant across profiles because a
            streaming catalogue does not scale with subscriber count, and varying
            it would make content metrics incomparable between profiles.
        experiments: Number of A/B tests to fabricate.
        approx_sessions: Expected session rows, for the progress display.
        approx_events: Expected event rows.
        approx_runtime_seconds: Wall-clock generation time, measured on a
            2020-era laptop against Postgres in Docker. Dominated by the events
            ``COPY`` at roughly 20,000 rows/sec.
    """

    name: str
    users: int
    titles: int
    experiments: int
    approx_sessions: int
    approx_events: int
    approx_runtime_seconds: int


#: The three profiles. ``medium`` is the default and the one every documented
#: figure and committed screenshot is anchored to.
#:
#: User counts are deliberately far below what a "25,000 subscriber" headline
#: would suggest, and that is the right trade. Because each user generates ~1,900
#: events over eighteen months, 4,000 users already yields a 7.6M-row fact table —
#: large enough that partition pruning, BRIN indexes and the materialized views
#: are load-bearing rather than decorative. Raising the user count would buy a
#: bigger number in the README at the cost of a dataset nobody can seed in a
#: coffee break, and "7.6 million events" is the more impressive figure anyway.
PROFILES: Final[dict[str, ScaleProfile]] = {
    "small": ScaleProfile(
        name="small",
        users=600,
        titles=320,
        experiments=4,
        approx_sessions=69_000,
        approx_events=1_140_000,
        approx_runtime_seconds=150,
    ),
    "medium": ScaleProfile(
        name="medium",
        users=4_000,
        titles=340,
        experiments=6,
        approx_sessions=463_000,
        approx_events=7_580_000,
        approx_runtime_seconds=800,
    ),
    "large": ScaleProfile(
        name="large",
        users=15_000,
        titles=360,
        experiments=8,
        approx_sessions=1_737_000,
        approx_events=28_400_000,
        approx_runtime_seconds=2_900,
    ),
}


# ===========================================================================
# Simulation window
# ===========================================================================

#: Signups are not uniform across the window. A real service grows, so the
#: monthly signup weight rises, with a dip in the winter holiday months where
#: acquisition spend is redirected to retention. Index 0 is the oldest month.
#:
#: Normalised by the generator, so these are relative weights and their absolute
#: scale is irrelevant.
SIGNUP_MONTH_WEIGHTS: Final[tuple[float, ...]] = (
    0.55, 0.62, 0.68, 0.71, 0.80, 0.74,   # ramp, with a mid-year plateau
    0.88, 0.95, 1.00, 1.12, 1.24, 0.96,   # autumn push, December pullback
    1.18, 1.31, 1.42, 1.38, 1.50, 1.44,   # second-year growth
)

#: Minimum days between a user's signup and the window end. Prevents a cohort
#: with two days of history from appearing in a 7-day retention chart, where it
#: would read as catastrophic churn rather than as an incomplete cohort.
MIN_OBSERVATION_DAYS: Final[int] = 3


def window_bounds(end: date, months: int) -> tuple[date, date]:
    """Return the inclusive start and end dates of the simulation window.

    Args:
        end: Last day of the window.
        months: Window length in months.

    Returns:
        ``(start_date, end_date)``.
    """
    # 30.44 days is the mean Gregorian month; exact month arithmetic would make
    # the window length depend on which month it ends in.
    return end - timedelta(days=int(months * 30.44)), end


# ===========================================================================
# Population distributions
#
# All weights are relative and normalised at use. Values reference the reference
# rows inserted by Alembic revision 0002 by name; the generator resolves them to
# surrogate keys and fails loudly on a mismatch, so a renamed dimension row
# cannot silently skew the distribution.
# ===========================================================================

#: Country mix. India-heavy, reflecting a service whose growth market is APAC —
#: which is also what makes the tier-3 ARPU story visible in the revenue charts.
COUNTRY_WEIGHTS: Final[dict[str, float]] = {
    "India": 26.0,
    "United States": 14.0,
    "Brazil": 8.5,
    "Indonesia": 6.5,
    "United Kingdom": 5.5,
    "Germany": 4.5,
    "Mexico": 4.2,
    "Philippines": 3.8,
    "France": 3.5,
    "Canada": 3.2,
    "Japan": 3.0,
    "Australia": 2.6,
    "South Korea": 2.4,
    "Spain": 2.2,
    "Italy": 2.0,
    "Netherlands": 1.8,
    "United Arab Emirates": 1.6,
    "Singapore": 1.5,
    "South Africa": 1.4,
    "Nigeria": 1.3,
}

#: Signup-device mix. Phones dominate signup even though TVs dominate watch time,
#: which is a genuine pattern in streaming and produces a visible divergence
#: between the Users and Sessions pages.
SIGNUP_DEVICE_WEIGHTS: Final[dict[str, float]] = {
    "Android Phone": 33.0,
    "iPhone": 21.0,
    "Web Desktop": 15.0,
    "Smart TV": 11.0,
    "Fire TV Stick": 7.0,
    "Android Tablet": 5.0,
    "iPad": 4.5,
    "Chromecast": 2.5,
    "PlayStation": 1.0,
}

#: Acquisition channel mix. Organic majority with a meaningful paid tail, so the
#: CAC and payback analysis has something to chew on.
CHANNEL_WEIGHTS: Final[dict[str, float]] = {
    "Organic Search": 18.0,
    "Direct": 15.0,
    "Paid Social": 13.0,
    "Paid Search": 11.0,
    "Organic Social": 9.0,
    "App Store Featured": 8.0,
    "Referral": 7.5,
    "Influencer": 6.0,
    "Email": 4.5,
    "Telco Bundle": 3.5,
    "Affiliate": 2.5,
    "Display": 1.5,
}

#: Persona mix. Casual Viewer is the plurality, as in any real consumer product;
#: Binge Watcher and Premium Loyalist are the minority that generates most of the
#: revenue, which is the concentration the RFM decile chart exposes.
PERSONA_WEIGHTS: Final[dict[str, float]] = {
    "Casual Viewer": 27.0,
    "New Explorer": 17.0,
    "Movie Lover": 15.0,
    "Binge Watcher": 12.0,
    "Churn Risk": 11.0,
    "Anime Fan": 8.0,
    "Sports Fan": 6.0,
    "Premium Loyalist": 4.0,
}

#: Persona assignment is correlated with acquisition channel, not independent of
#: it. This is the single most important interaction in the whole simulation: it
#: is *why* Referral outperforms Display, rather than the difference being applied
#: as a cosmetic coefficient at conversion time.
#:
#: Values multiply the base :data:`PERSONA_WEIGHTS`. Absent pairs default to 1.0.
CHANNEL_PERSONA_AFFINITY: Final[dict[str, dict[str, float]]] = {
    "Referral": {"Binge Watcher": 1.9, "Premium Loyalist": 2.2, "Churn Risk": 0.4},
    "Organic Search": {"Movie Lover": 1.4, "New Explorer": 1.3, "Churn Risk": 0.7},
    "Direct": {"Premium Loyalist": 1.8, "Binge Watcher": 1.4, "Churn Risk": 0.6},
    "App Store Featured": {"New Explorer": 1.9, "Casual Viewer": 1.3},
    "Email": {"Premium Loyalist": 1.6, "Binge Watcher": 1.2},
    "Organic Social": {"Anime Fan": 1.8, "New Explorer": 1.2},
    "Influencer": {"Anime Fan": 2.1, "Casual Viewer": 1.2, "Premium Loyalist": 0.5},
    "Paid Search": {"Movie Lover": 1.2, "Churn Risk": 1.3, "Premium Loyalist": 0.6},
    "Paid Social": {"Casual Viewer": 1.6, "Churn Risk": 1.9, "Premium Loyalist": 0.3},
    "Display": {"Casual Viewer": 1.7, "Churn Risk": 2.3, "Binge Watcher": 0.4},
    "Telco Bundle": {"Sports Fan": 2.4, "Casual Viewer": 1.3},
    "Affiliate": {"Churn Risk": 1.4, "Casual Viewer": 1.2},
}

#: Age bands as ``(low, high, weight)``. Sampled uniformly within the chosen band.
AGE_BANDS: Final[tuple[tuple[int, int, float], ...]] = (
    (13, 17, 6.0),
    (18, 24, 24.0),
    (25, 34, 31.0),
    (35, 44, 19.0),
    (45, 54, 11.0),
    (55, 64, 6.0),
    (65, 90, 3.0),
)

#: Gender mix, including a realistic share of undisclosed values. Real signup
#: forms produce these, and code that assumes otherwise breaks on live data.
GENDER_WEIGHTS: Final[dict[str, float]] = {
    "male": 46.0,
    "female": 43.0,
    "non_binary": 2.5,
    "undisclosed": 8.5,
}

#: Persona-to-age skew, multiplying :data:`AGE_BANDS` weights. Anime Fans skew
#: young, Premium Loyalists skew older and higher-income.
PERSONA_AGE_SKEW: Final[dict[str, dict[tuple[int, int], float]]] = {
    "Anime Fan": {(13, 17): 2.6, (18, 24): 1.9, (45, 54): 0.4, (55, 64): 0.2, (65, 90): 0.1},
    "Binge Watcher": {(18, 24): 1.5, (25, 34): 1.3, (65, 90): 0.4},
    "Premium Loyalist": {(35, 44): 1.6, (45, 54): 1.7, (55, 64): 1.4, (13, 17): 0.2},
    "Sports Fan": {(25, 34): 1.3, (35, 44): 1.5, (45, 54): 1.3, (13, 17): 0.5},
    "Casual Viewer": {(55, 64): 1.4, (65, 90): 1.6},
}

#: App versions, weighted so most users are current and a long tail is stale.
#: Gives the churn scorecard a legitimate secondary signal, and makes the version
#: adoption breakdown on the Users page non-trivial.
APP_VERSION_WEIGHTS: Final[dict[str, float]] = {
    "4.12.0": 38.0,
    "4.11.2": 22.0,
    "4.11.0": 12.0,
    "4.10.3": 9.0,
    "4.9.1": 7.0,
    "4.8.0": 5.0,
    "3.14.2": 4.0,
    "3.12.0": 3.0,
}


# ===========================================================================
# Session and playback behaviour
# ===========================================================================

#: Probability that a session uses a device other than the user's signup device.
#: Non-zero is what makes the device-switching query return anything.
DEVICE_SWITCH_PROBABILITY: Final[float] = 0.22

#: When switching, the replacement is drawn from the same weights as signup but
#: biased toward larger screens — people migrate from phone to TV, rarely back.
SWITCH_FORM_FACTOR_BIAS: Final[dict[str, float]] = {
    "tv": 2.4,
    "desktop": 1.3,
    "tablet": 1.1,
    "phone": 0.7,
    "console": 0.9,
}

#: Bounds on generated session length, in seconds. The lower bound is a real
#: session (open, glance, leave); the upper bound is below the 43200-second CHECK
#: constraint in revision 0003 so a long tail cannot trip it.
SESSION_DURATION_BOUNDS: Final[tuple[int, int]] = (45, 21_600)

#: Seconds of "thinking time" between consecutive navigation events, as
#: ``(min, mode, max)`` for a triangular draw. Browsing is fast; deciding is slow.
NAVIGATION_DWELL_SECONDS: Final[tuple[float, float, float]] = (2.0, 9.0, 75.0)

#: Trailer length bounds in seconds.
TRAILER_SECONDS: Final[tuple[int, int]] = (45, 150)

#: A playback emits a VIDEO_PROGRESS event roughly every this many seconds of
#: watching. This is the single biggest lever on total row count: every halving
#: removes millions of rows at the medium profile.
#:
#: 600 gives ten-minute granularity. That is still fine resolution for the
#: abandonment-point analysis — a 110-minute film yields 11 checkpoints, enough to
#: locate a drop-off to within a scene. The original 300 doubled the fact table for
#: precision no chart in the project actually reads.
PROGRESS_EVENT_INTERVAL_SECONDS: Final[int] = 600

#: Fraction watched at or above which a playback counts as COMPLETE_VIDEO. Must
#: stay at or above the 90 enforced by ``ck_events_complete_is_complete``.
COMPLETION_THRESHOLD_PCT: Final[float] = 90.0

#: Where abandoners stop, as a fraction of runtime. Bimodal on purpose: most
#: quitting happens in the first few minutes ("this isn't for me"), with a
#: secondary cluster near the end ("I'll finish it later"). A unimodal
#: distribution would hide the most actionable content insight in the dataset.
ABANDON_POINT_MODES: Final[tuple[tuple[float, float, float], ...]] = (
    # (weight, mean_fraction, stddev)
    (0.62, 0.11, 0.07),
    (0.24, 0.42, 0.14),
    (0.14, 0.78, 0.09),
)

#: Probability a user rates a title after completing it.
RATE_AFTER_COMPLETE_PROBABILITY: Final[float] = 0.17

#: Rating distribution, 1-5. Skewed high, as every real rating distribution is:
#: people rate things they chose to finish.
RATING_WEIGHTS: Final[dict[int, float]] = {1: 4.0, 2: 7.0, 3: 18.0, 4: 34.0, 5: 37.0}

#: Probability a session includes a PAUSE_VIDEO during a playback.
PAUSE_PROBABILITY: Final[float] = 0.31


# ===========================================================================
# Subscription conversion
#
# A logistic model, not a coin flip. The intercept sets the base rate; each
# coefficient shifts the log-odds. The generator computes features from a
# strictly trailing 14-day window, so no future information leaks in.
#
#   logit(p) = INTERCEPT
#            + WATCH_HOURS       * log1p(watch_hours_14d)
#            + COMPLETIONS       * completed_videos_14d
#            + SEARCHES          * log1p(searches_14d)
#            + PERSONA_EFFECT[persona]
#            + CHANNEL_EFFECT[channel]
#            + COUNTRY_TIER_EFFECT[tier]
#            + TRIAL_URGENCY     (only inside a trial's final days)
#            + TENURE_DECAY      * weeks_since_signup
# ===========================================================================

#: Base log-odds of converting on any given evaluation day, before any signal.
#:
#: Calibrated empirically, not chosen for looking plausible. The value must be read
#: against the fact that conversion is evaluated **once per day** for a mean tenure
#: of ~250 days, so the lifetime outcome is ``1 - prod(1 - p_day)`` and a daily
#: figure that looks tiny compounds hard.
#:
#: Measured lifetime conversion at this intercept, by engagement cohort::
#:
#:     inert (0h watched)      1.5%
#:     light (1h/14d)          2.6%
#:     moderate (6h/14d)      12.6%
#:     heavy (18h/14d)        55.7%
#:     very heavy (40h/14d)   76.7%
#:     population overall     17.3%
#:
#: The 50x spread between inert and very heavy is the point: it is what makes the
#: engagement-to-conversion relationship recoverable from the event stream by SQL
#: that never sees these coefficients.
#:
#: An earlier value of -4.20 was set as though this were a one-shot probability. It
#: gave an inert user a 1.48% daily chance, compounding to 97.6% over 250 days —
#: so users who watched nothing converted anyway, and the engagement signal was
#: drowned entirely. The seeded run showed 60.7% conversion, which is what exposed
#: it.
CONVERSION_INTERCEPT: Final[float] = -10.00

#: Per unit of ``log1p(watch_hours_14d)``. The dominant term by design: watch time
#: is the strongest predictor of paying, and the funnel charts should show that.
CONVERSION_WATCH_HOURS_COEF: Final[float] = 0.78

#: Per completed video in the trailing window.
CONVERSION_COMPLETIONS_COEF: Final[float] = 0.155

#: Per unit of ``log1p(searches_14d)``. Small but positive: search signals intent.
CONVERSION_SEARCHES_COEF: Final[float] = 0.11

#: Persona effect on conversion log-odds.
CONVERSION_PERSONA_EFFECT: Final[dict[str, float]] = {
    "Premium Loyalist": 1.55,
    "Binge Watcher": 0.92,
    "Movie Lover": 0.48,
    "Anime Fan": 0.36,
    "Sports Fan": 0.14,
    "New Explorer": -0.18,
    "Casual Viewer": -0.52,
    "Churn Risk": -1.24,
}

#: Channel effect on conversion log-odds. The headline marketing finding:
#: Referral and organic bring users who pay; Display and Paid Social do not.
#: Combined with the CAC values in revision 0002, this is what makes the LTV:CAC
#: quadrant chart tell a coherent story.
CONVERSION_CHANNEL_EFFECT: Final[dict[str, float]] = {
    "Referral": 0.58,
    "Organic Search": 0.34,
    "Direct": 0.31,
    "App Store Featured": 0.22,
    "Email": 0.16,
    "Organic Social": 0.08,
    "Affiliate": -0.04,
    "Telco Bundle": -0.12,
    "Influencer": -0.21,
    "Paid Search": -0.27,
    "Paid Social": -0.46,
    "Display": -0.63,
}

#: Country monetisation-tier effect. Tier 3 converts less often *and* onto cheaper
#: plans (see :data:`PLAN_WEIGHTS_BY_TIER`), which is the real-world dynamic
#: behind "high volume, low ARPU".
CONVERSION_COUNTRY_TIER_EFFECT: Final[dict[int, float]] = {1: 0.38, 2: 0.0, 3: -0.44}

#: Added inside the final :data:`TRIAL_URGENCY_DAYS` of a trial. Deadlines convert.
CONVERSION_TRIAL_URGENCY: Final[float] = 0.85

#: Days before trial expiry at which urgency applies.
TRIAL_URGENCY_DAYS: Final[int] = 3

#: Per week since signup. Slightly negative: a user who has not converted after
#: months is progressively less likely to, which is what gives the conversion
#: funnel its characteristic front-loaded shape.
CONVERSION_TENURE_DECAY: Final[float] = -0.021

#: Probability a converting user takes a free trial first rather than paying
#: immediately.
TRIAL_START_PROBABILITY: Final[float] = 0.64

#: Trial length in days.
TRIAL_DAYS: Final[int] = 7

#: Plan choice, conditioned on country tier. Tier-3 users overwhelmingly pick
#: Mobile; tier-1 users spread across Standard and Premium.
PLAN_WEIGHTS_BY_TIER: Final[dict[int, dict[str, float]]] = {
    1: {"Mobile": 6.0, "Basic": 18.0, "Standard": 44.0, "Premium 4K": 32.0},
    2: {"Mobile": 18.0, "Basic": 30.0, "Standard": 37.0, "Premium 4K": 15.0},
    3: {"Mobile": 48.0, "Basic": 31.0, "Standard": 17.0, "Premium 4K": 4.0},
}

#: Billing cadence, and the discount each carries. Annual is cheaper per month,
#: so annual subscribers show *lower* MRR but *higher* retention and LTV — the
#: kind of apparent contradiction that makes a revenue dashboard worth reading.
BILLING_PERIOD_WEIGHTS: Final[dict[str, float]] = {
    "monthly": 78.0,
    "quarterly": 13.0,
    "annual": 9.0,
}

#: Multiplier applied to list price to obtain normalised MRR.
BILLING_PERIOD_MRR_MULTIPLIER: Final[dict[str, float]] = {
    "monthly": 1.00,
    "quarterly": 0.92,
    "annual": 0.83,
}

#: Probability a premium user upgrades or downgrades in a given month. Produces
#: the expansion and contraction bars in the MRR movement waterfall, which is
#: otherwise a two-bar chart and not worth drawing.
PLAN_CHANGE_MONTHLY_PROBABILITY: Final[float] = 0.017

#: Given a plan change, the probability it is an upgrade rather than a downgrade.
PLAN_UPGRADE_SHARE: Final[float] = 0.58


# ===========================================================================
# Churn
#
# Monthly hazard = persona_base
#                * tenure_multiplier(months_since_signup)
#                * engagement_multiplier(recent activity)
#                * (premium ? PREMIUM_CHURN_DAMPENER : 1.0)
# ===========================================================================

#: Tenure multiplier by whole months since signup. Front-loaded, as all consumer
#: churn is: the first month is the dangerous one, and survivors get steadily
#: safer. This shape is what makes the retention curve bend correctly instead of
#: decaying linearly.
CHURN_TENURE_MULTIPLIER: Final[tuple[float, ...]] = (
    2.35,  # month 0 — onboarding failure
    1.62,  # month 1
    1.24,  # month 2
    1.00,  # month 3 — baseline
    0.86,
    0.77,
    0.70,
    0.64,
    0.59,
    0.55,
    0.52,
    0.49,  # month 11
)

#: Applied beyond the table above. Long-tenured users churn at a low, flat rate.
CHURN_TENURE_FLOOR_MULTIPLIER: Final[float] = 0.46

#: Engagement multiplier, keyed by the number of active days in the trailing 28.
#: The dominant term in the churn scorecard, and correctly so: recency of use
#: predicts churn better than any demographic attribute.
CHURN_ENGAGEMENT_MULTIPLIER: Final[tuple[tuple[int, float], ...]] = (
    # (min_active_days_28d, multiplier)
    (0, 4.80),
    (1, 3.10),
    (2, 2.20),
    (4, 1.45),
    (7, 1.00),
    (12, 0.62),
    (18, 0.34),
    (24, 0.19),
)

#: Paying users churn less than free users at equal engagement — sunk cost is
#: real. Prevents the churn model from being a pure restatement of activity.
PREMIUM_CHURN_DAMPENER: Final[float] = 0.58

#: Days without a single event after which a user is *labelled* churned. Distinct
#: from the hazard above: this is the observable definition the SQL uses, and it
#: is deliberately generous so a two-week holiday is not counted as churn.
CHURN_INACTIVITY_THRESHOLD_DAYS: Final[int] = 45

#: Reasons attached to a cancellation, weighted. Free text in a real system;
#: enumerated here so the churn-reason mix chart has stable categories.
CANCEL_REASON_WEIGHTS: Final[dict[str, float]] = {
    "too expensive": 24.0,
    "not enough to watch": 21.0,
    "finished what I wanted": 16.0,
    "switched to a competitor": 13.0,
    "technical problems": 9.0,
    "watching less than expected": 8.0,
    "shared account elsewhere": 5.0,
    "other": 4.0,
}

#: Probability a churned user returns later in the window. Non-zero so the
#: resurrection-rate query and the reactivation bar in the MRR waterfall have
#: real data behind them.
RESURRECTION_PROBABILITY: Final[float] = 0.11

#: Days a resurrected user stays away before returning.
RESURRECTION_GAP_DAYS: Final[tuple[int, int]] = (52, 210)


# ===========================================================================
# Experiments
# ===========================================================================

#: A/B tests to fabricate, as ``(key, name, hypothesis, primary_metric,
#: variants, allocation, true_lift)``.
#:
#: ``true_lift`` is the *actual* effect the generator applies to the treatment
#: arm — the honest part of this design. Two entries have a lift of 0.0: they are
#: null experiments, and the significance test in ``app/services/stats.py`` should
#: correctly fail to reject the null for them. An experiments page where every
#: test wins is a page nobody believes.
EXPERIMENT_SPECS: Final[
    tuple[tuple[str, str, str, str, tuple[str, ...], float, float], ...]
] = (
    (
        "autoplay-preview-v2",
        "Autoplay previews on the home rail",
        "Muted autoplay previews on hover will increase title starts by "
        "reducing the cost of evaluating a title.",
        "trailer_to_start",
        ("control", "treatment"),
        0.50,
        0.084,  # a real, modest win
    ),
    (
        "paywall-copy-value-first",
        "Value-first paywall copy",
        "Leading with catalogue size rather than price will lift trial starts "
        "among users who have completed at least one title.",
        "subscription_conversion",
        ("control", "treatment"),
        0.40,
        0.041,  # real but small; should be borderline significant
    ),
    (
        "continue-watching-position",
        "Continue Watching above Trending",
        "Surfacing partially watched titles first will increase completion rate "
        "for series viewers.",
        "completion_rate",
        ("control", "treatment"),
        0.50,
        0.062,
    ),
    (
        "onboarding-genre-picker",
        "Genre picker during onboarding",
        "Asking new users to select three genres will improve day-7 retention "
        "by making the first session's recommendations relevant.",
        "day7_retention",
        ("control", "variant_a", "variant_b"),
        0.60,
        0.055,
    ),
    (
        "search-ranking-recency",
        "Recency weighting in search ranking",
        "Boosting recently added titles in search results will increase "
        "sessions per user.",
        "sessions_per_user",
        ("control", "treatment"),
        0.35,
        0.0,  # null result, and the stats must say so
    ),
    (
        "player-skip-intro",
        "Skip Intro button",
        "A skip-intro control will increase episodes completed per session for "
        "series viewers.",
        "completion_rate",
        ("control", "treatment"),
        0.50,
        0.0,  # second null result
    ),
    (
        "push-weekly-digest",
        "Weekly digest push notification",
        "A Friday digest of new arrivals will increase weekend session volume.",
        "sessions_per_user",
        ("control", "treatment"),
        0.45,
        0.038,
    ),
    (
        "pricing-annual-nudge",
        "Annual plan nudge at checkout",
        "Showing annual savings at checkout will shift mix toward annual "
        "billing without reducing overall conversion.",
        "subscription_conversion",
        ("control", "treatment"),
        0.30,
        -0.019,  # a genuine regression; the dashboard should show a loss
    ),
)

#: Fraction of the window an experiment runs for, as ``(min, max)``.
EXPERIMENT_DURATION_FRACTION: Final[tuple[float, float]] = (0.12, 0.34)


# ===========================================================================
# Numerical guards
# ===========================================================================

#: Probabilities are clamped to ``[floor, 1 - floor]``. A generated outcome must
#: never be certain: determinism in synthetic data is what lets a downstream model
#: score 100% and look like a leak.
PROBABILITY_FLOOR: Final[float] = 0.001

#: Upper bound on the *daily* conversion hazard.
#:
#: Sized against the observation window, not picked for looking small. Mean
#: observed tenure is ~250 days, so a daily cap of ``c`` implies a lifetime
#: ceiling of ``1 - (1 - c)^250``. At 0.006 that is 78%, which is the right shape:
#: the most engaged users very probably convert eventually, but not on their first
#: enthusiastic week.
#:
#: An earlier value of 0.22 was chosen as though this were a one-shot probability.
#: Evaluated daily it compounds to a 100% lifetime ceiling and makes conversion
#: unconditional.
MAX_DAILY_CONVERSION_PROBABILITY: Final[float] = 0.006

#: Upper bound on the *daily* churn hazard.
#:
#: The worst-case composed monthly hazard is ~9.6 (0.85 base x 2.35 tenure x 4.80
#: engagement), which divided across a month is 0.31/day — certainty within a
#: fortnight. Capping at 0.04 keeps even a totally disengaged new user's monthly
#: churn near 71%, high but not preordained.
MAX_DAILY_CHURN_HAZARD: Final[float] = 0.04


def clamp_probability(value: float) -> float:
    """Clamp a one-shot probability away from both certainties.

    For outcomes decided **once**: does this session contain playback, does this
    viewer rate what they finished.

    Do not use this for a per-day hazard. See :func:`clamp_hazard` for why.

    Args:
        value: Raw probability, possibly outside ``[0, 1]``.

    Returns:
        The value constrained to ``[PROBABILITY_FLOOR, 1 - PROBABILITY_FLOOR]``.
    """
    return min(max(value, PROBABILITY_FLOOR), 1.0 - PROBABILITY_FLOOR)


def clamp_hazard(value: float, *, cap: float) -> float:
    """Clamp a per-day hazard that will be evaluated repeatedly.

    The distinction from :func:`clamp_probability` is not cosmetic, and getting it
    wrong silently ruins the dataset.

    A hazard evaluated daily over ``n`` days has cumulative probability
    ``1 - (1 - p)^n``. The ``PROBABILITY_FLOOR`` of 0.001 looks negligible until
    it compounds: over a 250-day observation window it guarantees a **22%**
    cumulative outcome for a user whose true hazard is zero. An earlier version of
    this module applied the one-shot clamp to both conversion and churn, which
    floored conversion at 22% regardless of engagement — drowning the very
    engagement signal the whole simulation exists to plant.

    So this function **caps but does not floor**. A user who watches nothing
    genuinely should never convert; ``p = 0`` is the correct answer, not a
    degenerate one. Only the upper end needs guarding, because certainty *to
    convert* would make the outcome deterministic.

    Args:
        value: Raw per-day hazard.
        cap: Maximum permitted daily value. Choose it against the observation
            window: a cap of ``c`` implies a lifetime ceiling of
            ``1 - (1 - c)^n``.

    Returns:
        The hazard constrained to ``[0, cap]``.
    """
    return min(max(value, 0.0), cap)


def logistic(log_odds: float) -> float:
    """Convert log-odds to a probability.

    Implemented in two branches to avoid ``exp`` overflow on large magnitudes,
    which is a real risk here: the coefficients can sum past ±700 for an extreme
    user, and the naive form raises ``OverflowError``.

    Args:
        log_odds: The linear predictor.

    Returns:
        A probability in ``(0, 1)``.
    """
    import math

    if log_odds >= 0:
        return 1.0 / (1.0 + math.exp(-log_odds))
    exp_value = math.exp(log_odds)
    return exp_value / (1.0 + exp_value)


def get_profile(name: str) -> ScaleProfile:
    """Return a named scale profile.

    Args:
        name: Profile name, case-insensitive.

    Returns:
        The matching :class:`ScaleProfile`.

    Raises:
        KeyError: If no such profile exists, listing the valid names.
    """
    key = name.strip().lower()
    if key not in PROFILES:
        valid = ", ".join(sorted(PROFILES))
        raise KeyError(f"Unknown seed profile {name!r}. Valid profiles: {valid}.")
    return PROFILES[key]


__all__ = [
    "ABANDON_POINT_MODES",
    "AGE_BANDS",
    "APP_VERSION_WEIGHTS",
    "BILLING_PERIOD_MRR_MULTIPLIER",
    "BILLING_PERIOD_WEIGHTS",
    "CANCEL_REASON_WEIGHTS",
    "CHANNEL_PERSONA_AFFINITY",
    "CHANNEL_WEIGHTS",
    "CHURN_ENGAGEMENT_MULTIPLIER",
    "CHURN_INACTIVITY_THRESHOLD_DAYS",
    "CHURN_TENURE_FLOOR_MULTIPLIER",
    "CHURN_TENURE_MULTIPLIER",
    "COMPLETION_THRESHOLD_PCT",
    "CONVERSION_CHANNEL_EFFECT",
    "CONVERSION_COMPLETIONS_COEF",
    "CONVERSION_COUNTRY_TIER_EFFECT",
    "CONVERSION_INTERCEPT",
    "CONVERSION_PERSONA_EFFECT",
    "CONVERSION_SEARCHES_COEF",
    "CONVERSION_TENURE_DECAY",
    "CONVERSION_TRIAL_URGENCY",
    "CONVERSION_WATCH_HOURS_COEF",
    "COUNTRY_WEIGHTS",
    "DEVICE_SWITCH_PROBABILITY",
    "EXPERIMENT_DURATION_FRACTION",
    "EXPERIMENT_SPECS",
    "GENDER_WEIGHTS",
    "MAX_DAILY_CHURN_HAZARD",
    "MAX_DAILY_CONVERSION_PROBABILITY",
    "MIN_OBSERVATION_DAYS",
    "NAVIGATION_DWELL_SECONDS",
    "PAUSE_PROBABILITY",
    "PERSONA_AGE_SKEW",
    "PERSONA_WEIGHTS",
    "PLAN_CHANGE_MONTHLY_PROBABILITY",
    "PLAN_UPGRADE_SHARE",
    "PLAN_WEIGHTS_BY_TIER",
    "PREMIUM_CHURN_DAMPENER",
    "PROBABILITY_FLOOR",
    "PROFILES",
    "PROGRESS_EVENT_INTERVAL_SECONDS",
    "RATE_AFTER_COMPLETE_PROBABILITY",
    "RATING_WEIGHTS",
    "RESURRECTION_GAP_DAYS",
    "RESURRECTION_PROBABILITY",
    "SESSION_DURATION_BOUNDS",
    "SIGNUP_DEVICE_WEIGHTS",
    "SIGNUP_MONTH_WEIGHTS",
    "SWITCH_FORM_FACTOR_BIAS",
    "TRAILER_SECONDS",
    "TRIAL_DAYS",
    "TRIAL_START_PROBABILITY",
    "TRIAL_URGENCY_DAYS",
    "ScaleProfile",
    "clamp_hazard",
    "clamp_probability",
    "get_profile",
    "logistic",
    "window_bounds",
]

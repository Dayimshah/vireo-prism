"""Subscription conversion and lifecycle.

Two responsibilities, kept separate.

**The conversion model** (:func:`conversion_log_odds`) is a pure function: given a
user's trailing behaviour and attributes, return log-odds of converting today. It
reads only the coefficients in :mod:`seeder.config` and only backward-looking
features, so it cannot leak the future. Every caller passes features it has
already computed, which is what makes the no-lookahead property checkable rather
than assumed.

**The lifecycle** (:class:`SubscriptionLifecycle`) is stateful: it walks a single
user's subscription history forward, opening trials, converting or expiring them,
applying plan changes, and closing terms on churn. It emits one
``core.subscriptions`` row per term, so a user who subscribed, cancelled and
returned produces three rows — which is what makes MRR movement computable rather
than approximated.

Why a logistic model rather than a probability table
----------------------------------------------------
A table of "persona → conversion rate" would produce the same headline numbers and
nothing else. A logistic model produces *interactions*: a Casual Viewer who
happens to watch heavily converts better than a Binge Watcher who does not, and
the effect of acquisition channel survives controlling for engagement. Those are
the relationships an analyst looks for, and they only exist if the generator
composes its causes additively in log-odds space.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
import math
from typing import TYPE_CHECKING

from seeder import config

if TYPE_CHECKING:
    from random import Random


@dataclass(slots=True)
class TrailingFeatures:
    """Backward-looking behaviour used by the conversion model.

    Every field is computed from events strictly before the evaluation date. The
    dataclass exists so the no-lookahead contract is visible in the type signature
    rather than living in a comment.

    Attributes:
        watch_hours_14d: Hours watched in the trailing 14 days.
        completed_videos_14d: Titles or episodes completed in the trailing 14 days.
        searches_14d: Searches issued in the trailing 14 days.
        weeks_since_signup: Tenure in weeks, for the decay term.
        trial_days_remaining: Days left in an active trial, or ``None``.
    """

    watch_hours_14d: float
    completed_videos_14d: int
    searches_14d: int
    weeks_since_signup: float
    trial_days_remaining: int | None = None


def conversion_log_odds(
    features: TrailingFeatures,
    *,
    persona: str,
    channel: str,
    country_tier: int,
) -> float:
    """Return log-odds that a user converts to paid on the evaluation day.

    Implements the model documented in :mod:`seeder.config`::

        logit(p) = INTERCEPT
                 + WATCH_HOURS  * log1p(watch_hours_14d)
                 + COMPLETIONS  * completed_videos_14d
                 + SEARCHES     * log1p(searches_14d)
                 + PERSONA_EFFECT[persona]
                 + CHANNEL_EFFECT[channel]
                 + COUNTRY_TIER_EFFECT[tier]
                 + TRIAL_URGENCY  (inside a trial's final days)
                 + TENURE_DECAY * weeks_since_signup

    Args:
        features: Trailing behavioural features.
        persona: The user's persona name.
        channel: The user's acquisition channel name.
        country_tier: Monetisation band, 1-3.

    Returns:
        The linear predictor. Convert with :func:`seeder.config.logistic`.
    """
    log_odds = config.CONVERSION_INTERCEPT

    # log1p rather than the raw value: the marginal effect of the tenth hour
    # watched is smaller than the first, which is how engagement actually behaves.
    log_odds += config.CONVERSION_WATCH_HOURS_COEF * math.log1p(
        max(features.watch_hours_14d, 0.0)
    )
    log_odds += config.CONVERSION_COMPLETIONS_COEF * features.completed_videos_14d
    log_odds += config.CONVERSION_SEARCHES_COEF * math.log1p(max(features.searches_14d, 0))

    log_odds += config.CONVERSION_PERSONA_EFFECT.get(persona, 0.0)
    log_odds += config.CONVERSION_CHANNEL_EFFECT.get(channel, 0.0)
    log_odds += config.CONVERSION_COUNTRY_TIER_EFFECT.get(country_tier, 0.0)

    if (
        features.trial_days_remaining is not None
        and 0 <= features.trial_days_remaining <= config.TRIAL_URGENCY_DAYS
    ):
        log_odds += config.CONVERSION_TRIAL_URGENCY

    log_odds += config.CONVERSION_TENURE_DECAY * features.weeks_since_signup

    return log_odds


def conversion_probability(
    features: TrailingFeatures,
    *,
    persona: str,
    channel: str,
    country_tier: int,
) -> float:
    """Return the bounded daily probability of converting to paid.

    Args:
        features: Trailing behavioural features.
        persona: The user's persona name.
        channel: The user's acquisition channel name.
        country_tier: Monetisation band, 1-3.

    Returns:
        A daily hazard in ``[0, MAX_DAILY_CONVERSION_PROBABILITY]``. The ceiling
        keeps conversion a process rather than an instant certainty, so even a
        heavily engaged user takes some days to convert.

        Note this is deliberately **not floored**. This value is evaluated once per
        day for the user's whole tenure, and a floor compounds: 0.001/day reaches
        22% over 250 days, which would make a user who watches nothing convert
        anyway. Zero is the correct hazard for zero engagement. See
        :func:`seeder.config.clamp_hazard`.
    """
    raw = config.logistic(
        conversion_log_odds(
            features, persona=persona, channel=channel, country_tier=country_tier
        )
    )
    return config.clamp_hazard(raw, cap=config.MAX_DAILY_CONVERSION_PROBABILITY)


@dataclass(slots=True)
class SubscriptionRow:
    """One ``core.subscriptions`` row.

    Field order matches :data:`seeder.loaders.SUBSCRIPTION_COLUMNS`.

    Attributes:
        subscription_id: Surrogate key, assigned by the caller.
        user_id: Owning user.
        plan_id: Foreign key into ``core.subscription_plans``.
        started_on: First day of the term.
        ended_on: Last day, or ``None`` while open.
        status: A ``core.sub_status`` label.
        billing_period: A ``core.billing_period`` label.
        mrr_usd: Normalised monthly recurring revenue.
        cancel_reason: Reason text, only when cancelled or expired.
        is_trial_conversion: Whether this paid term followed a converting trial.
    """

    subscription_id: int
    user_id: int
    plan_id: int
    started_on: date
    ended_on: date | None
    status: str
    billing_period: str
    mrr_usd: float
    cancel_reason: str | None
    is_trial_conversion: bool

    def as_row(self) -> tuple[object, ...]:
        """Render as a tuple for binary ``COPY``.

        Returns:
            Values in :data:`seeder.loaders.SUBSCRIPTION_COLUMNS` order.
        """
        return (
            self.subscription_id,
            self.user_id,
            self.plan_id,
            self.started_on,
            self.ended_on,
            self.status,
            self.billing_period,
            round(self.mrr_usd, 2),
            self.cancel_reason,
            self.is_trial_conversion,
        )


class SubscriptionLifecycle:
    """Walks one user's subscription history forward in time.

    Driven day by day by the timeline walk in
    :mod:`seeder.generators.sessions`. Holds the currently open term, if any, and
    emits a completed :class:`SubscriptionRow` whenever a term closes.

    Status transitions permitted, matching ``ck_subs_status_matches_open_state``::

        (none) → trialing → active → cancelled
        (none) → active   → cancelled
                 active   → expired      (payment lapse)
                 active   → active       (plan change closes and reopens)
        trialing → expired               (trial ran out unconverted)
    """

    __slots__ = (
        "_country_tier",
        "_next_id",
        "_open",
        "_rng",
        "_rows",
        "_trial_ends_on",
        "_user_id",
    )

    def __init__(self, rng: Random, *, user_id: int, country_tier: int) -> None:
        """Initialise an empty history.

        Args:
            rng: Seeded random source.
            user_id: The user this history belongs to.
            country_tier: Monetisation band, used for plan selection.
        """
        self._rng = rng
        self._user_id = user_id
        self._country_tier = country_tier
        self._rows: list[SubscriptionRow] = []
        self._open: SubscriptionRow | None = None
        self._trial_ends_on: date | None = None
        self._next_id = 0

    # -- state ---------------------------------------------------------------

    @property
    def is_paying(self) -> bool:
        """Return whether a paid (non-trial) term is currently open."""
        return self._open is not None and self._open.status == "active"

    @property
    def has_open_term(self) -> bool:
        """Return whether any term is open, trial included."""
        return self._open is not None

    @property
    def in_trial(self) -> bool:
        """Return whether a trial is currently open."""
        return self._open is not None and self._open.status == "trialing"

    def trial_days_remaining(self, today: date) -> int | None:
        """Return days left in the open trial.

        Args:
            today: The simulation date.

        Returns:
            Remaining days, or ``None`` when not in a trial.
        """
        if self._trial_ends_on is None or not self.in_trial:
            return None
        return (self._trial_ends_on - today).days

    def ever_subscribed(self) -> bool:
        """Return whether this user has ever held any term."""
        return bool(self._rows) or self._open is not None

    # -- plan selection ------------------------------------------------------

    def _draw_plan(self, plan_ids: dict[str, int], *, upgrade_from: str | None = None) -> str:
        """Draw a plan name for this user's country tier.

        Args:
            plan_ids: Plan name to ``plan_id``, used to filter to plans that exist.
            upgrade_from: Current plan name when changing plan, so the draw can be
                restricted to strictly higher or lower tiers.

        Returns:
            The selected plan name.
        """
        weights = dict(config.PLAN_WEIGHTS_BY_TIER[self._country_tier])
        # Never offer a plan the database does not have.
        weights = {name: weight for name, weight in weights.items() if name in plan_ids}

        if upgrade_from is not None:
            order = list(config.PLAN_WEIGHTS_BY_TIER[self._country_tier])
            if upgrade_from in order:
                current = order.index(upgrade_from)
                going_up = self._rng.random() < config.PLAN_UPGRADE_SHARE
                candidates = order[current + 1 :] if going_up else order[:current]
                filtered = {
                    name: weights[name] for name in candidates if name in weights
                }
                if filtered:
                    weights = filtered

        names = list(weights)
        return self._rng.choices(names, weights=[weights[n] for n in names], k=1)[0]

    def _mrr_for(self, plan_price: float, billing_period: str) -> float:
        """Return normalised MRR for a plan and cadence.

        Args:
            plan_price: List monthly price.
            billing_period: A ``core.billing_period`` label.

        Returns:
            Monthly recurring revenue after the cadence discount.
        """
        return plan_price * config.BILLING_PERIOD_MRR_MULTIPLIER[billing_period]

    def _allocate_id(self, offset: int) -> int:
        """Return the next subscription id for this user.

        Args:
            offset: Base id reserved for this user by the caller.

        Returns:
            A unique subscription id.
        """
        self._next_id += 1
        return offset + self._next_id

    # -- transitions ---------------------------------------------------------

    def start_trial(
        self,
        today: date,
        *,
        id_offset: int,
        plan_ids: dict[str, int],
        plan_prices: dict[str, float],
    ) -> None:
        """Open a trial term.

        Args:
            today: First day of the trial.
            id_offset: Base subscription id reserved for this user.
            plan_ids: Plan name to ``plan_id``.
            plan_prices: Plan name to list monthly price.
        """
        if self.has_open_term:
            return

        plan_name = self._draw_plan(plan_ids)
        billing = "monthly"  # trials always convert onto a monthly cadence first

        self._open = SubscriptionRow(
            subscription_id=self._allocate_id(id_offset),
            user_id=self._user_id,
            plan_id=plan_ids[plan_name],
            started_on=today,
            ended_on=None,
            status="trialing",
            billing_period=billing,
            # A trial earns nothing. Recording zero rather than the plan price is
            # what keeps MRR honest: trialists are not revenue.
            mrr_usd=0.0,
            cancel_reason=None,
            is_trial_conversion=False,
        )
        self._trial_ends_on = today + timedelta(days=config.TRIAL_DAYS)

    def start_paid(
        self,
        today: date,
        *,
        id_offset: int,
        plan_ids: dict[str, int],
        plan_prices: dict[str, float],
        from_trial: bool = False,
    ) -> None:
        """Open a paid term, closing an open trial if present.

        Args:
            today: First day of the paid term.
            id_offset: Base subscription id reserved for this user.
            plan_ids: Plan name to ``plan_id``.
            plan_prices: Plan name to list monthly price.
            from_trial: Whether this follows a converting trial.
        """
        if self.in_trial and self._open is not None:
            # Close the trial the day before the paid term opens, so the two do
            # not overlap and cumulative-revenue queries cannot double-count.
            self._open.ended_on = max(self._open.started_on, today - timedelta(days=1))
            self._open.status = "cancelled"
            self._open.cancel_reason = "converted to paid"
            self._rows.append(self._open)
            self._open = None
            self._trial_ends_on = None
        elif self.has_open_term:
            return

        plan_name = self._draw_plan(plan_ids)
        billing = self._rng.choices(
            list(config.BILLING_PERIOD_WEIGHTS),
            weights=list(config.BILLING_PERIOD_WEIGHTS.values()),
            k=1,
        )[0]

        self._open = SubscriptionRow(
            subscription_id=self._allocate_id(id_offset),
            user_id=self._user_id,
            plan_id=plan_ids[plan_name],
            started_on=today,
            ended_on=None,
            status="active",
            billing_period=billing,
            mrr_usd=self._mrr_for(plan_prices[plan_name], billing),
            cancel_reason=None,
            is_trial_conversion=from_trial,
        )

    def expire_trial(self, today: date) -> None:
        """Close an unconverted trial.

        Args:
            today: The day the trial lapsed.
        """
        if not self.in_trial or self._open is None:
            return

        self._open.ended_on = today
        self._open.status = "expired"
        self._open.cancel_reason = "trial ended"
        self._rows.append(self._open)
        self._open = None
        self._trial_ends_on = None

    def change_plan(
        self,
        today: date,
        *,
        id_offset: int,
        plan_ids: dict[str, int],
        plan_prices: dict[str, float],
        plan_names: dict[int, str],
    ) -> None:
        """Close the current paid term and open one on a different plan.

        Modelled as two terms rather than an in-place update, which is what makes
        the expansion and contraction bars in the MRR waterfall derivable: the
        movement is visible as one term ending and another beginning at a different
        MRR.

        Args:
            today: Effective date of the change.
            id_offset: Base subscription id reserved for this user.
            plan_ids: Plan name to ``plan_id``.
            plan_prices: Plan name to list monthly price.
            plan_names: ``plan_id`` to plan name, to identify the current plan.
        """
        if not self.is_paying or self._open is None:
            return

        current_name = plan_names.get(self._open.plan_id)
        new_name = self._draw_plan(plan_ids, upgrade_from=current_name)
        if new_name == current_name:
            return

        previous = self._open
        previous.ended_on = max(previous.started_on, today - timedelta(days=1))
        previous.status = "cancelled"
        previous.cancel_reason = "plan changed"
        self._rows.append(previous)

        self._open = SubscriptionRow(
            subscription_id=self._allocate_id(id_offset),
            user_id=self._user_id,
            plan_id=plan_ids[new_name],
            started_on=today,
            ended_on=None,
            status="active",
            billing_period=previous.billing_period,
            mrr_usd=self._mrr_for(plan_prices[new_name], previous.billing_period),
            cancel_reason=None,
            is_trial_conversion=previous.is_trial_conversion,
        )

    def cancel(self, today: date, *, involuntary: bool = False) -> None:
        """Close the open term.

        Args:
            today: The day the term ended.
            involuntary: When true the term is marked ``expired`` (payment lapse)
                rather than ``cancelled`` (user opted out). The distinction drives
                the churn-reason mix, where involuntary churn is a different
                problem with a different fix.
        """
        if self._open is None:
            return

        if self.in_trial:
            self.expire_trial(today)
            return

        self._open.ended_on = max(self._open.started_on, today)
        self._open.status = "expired" if involuntary else "cancelled"
        self._open.cancel_reason = (
            "payment failed"
            if involuntary
            else self._rng.choices(
                list(config.CANCEL_REASON_WEIGHTS),
                weights=list(config.CANCEL_REASON_WEIGHTS.values()),
                k=1,
            )[0]
        )
        self._rows.append(self._open)
        self._open = None

    def finish(self) -> list[SubscriptionRow]:
        """Return every term, leaving any still-open term open.

        Called at the end of the window. An open term is correct: real datasets
        contain active subscriptions, and closing them all would make churn look
        total.

        Returns:
            All terms for this user, ordered by start date.
        """
        rows = list(self._rows)
        if self._open is not None:
            rows.append(self._open)
        rows.sort(key=lambda row: (row.started_on, row.subscription_id))
        return rows


#: Subscription ids reserved per user. A user cannot plausibly exceed this many
#: terms in eighteen months, and reserving a fixed block lets each user's history
#: be generated independently without a shared counter.
ID_BLOCK_PER_USER = 24


__all__ = [
    "ID_BLOCK_PER_USER",
    "SubscriptionLifecycle",
    "SubscriptionRow",
    "TrailingFeatures",
    "conversion_log_odds",
    "conversion_probability",
]

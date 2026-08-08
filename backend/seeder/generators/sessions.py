"""The timeline walk: one user's life, simulated forward day by day.

This is the orchestrator, and the place where the project's central claim is
either true or false.

Why forward, one day at a time
------------------------------
Generating sessions, then subscriptions, then churn labels in three independent
passes would be several times faster. It would also be circular. Whether a user
converts on day 40 depends on how much they watched in the fourteen days *before*
day 40; whether they churn in month three depends on their activity in the
twenty-eight days *before* it. Both features must be computed from data that
already exists at the moment of the decision.

So this module walks each user from signup to the window end, maintaining trailing
windows as it goes, and asks each question using only the past. That is what makes
the churn scorecard and the conversion funnel honest: the features the SQL later
computes are the same features the generator used, in the same direction of time.

The consequence a reviewer should look for: nothing in this file reads a date later
than the day being simulated. There is no second pass that goes back and adjusts
history.

Performance
-----------
25,000 users over 550 days is 13.75 million day-iterations, so the inner loop
matters. Two things keep it tolerable:

* Per-country daily multipliers are precomputed once for the whole window
  (:func:`_build_intensity_table`) — 20 countries by 550 days rather than a
  holiday lookup per user per day.
* Most days produce no session, and the early exit for those is a single
  comparison.

Everything expensive — content selection, journey planning — happens only on days
that actually generate a session.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final

from seeder import config
from seeder.generators.events import (
    ContentSelector,
    EventRow,
    make_search_terms,
    materialise,
    watch_seconds_of,
)
from seeder.generators.subscriptions import (
    ID_BLOCK_PER_USER,
    SubscriptionLifecycle,
    SubscriptionRow,
    TrailingFeatures,
    conversion_probability,
)
from seeder.journeys import plan_session, validate_plan
from seeder.seasonality import (
    active_holidays,
    draw_session_start,
    growth_multiplier,
    holiday_multiplier,
    local_date_of,
    utc_offset,
    weekday_multiplier,
)

if TYPE_CHECKING:
    from random import Random

    from seeder.catalog import ContentRow
    from seeder.generators.experiments import EffectResolver
    from seeder.generators.users import UserSpec

#: Trailing window for conversion features, in days.
CONVERSION_WINDOW_DAYS: Final[int] = 14

#: Trailing window for churn engagement, in days.
CHURN_WINDOW_DAYS: Final[int] = 28

#: Maximum sessions one user may generate in a single day. A Poisson draw has an
#: unbounded tail; without a cap one extreme draw could emit thousands of events
#: for one user on one day and visibly distort the sessions-per-user histogram.
MAX_SESSIONS_PER_DAY: Final[int] = 9

#: Fraction of session plans put through :func:`~seeder.journeys.validate_plan`.
#: Full validation roughly doubles generation time, so a sample is checked on every
#: run and the full suite is exercised by ``tests/test_seeder.py``.
VALIDATION_SAMPLE_RATE: Final[float] = 0.02


@dataclass(slots=True)
class SessionRow:
    """One ``core.sessions`` row.

    Field order matches :data:`seeder.loaders.SESSION_COLUMNS`.

    Attributes:
        session_id: Surrogate key.
        user_id: Owning user.
        device_id: Device for this session, which may differ from signup.
        session_start: UTC timestamp of the first event.
        session_end: UTC timestamp of the last event.
        duration_seconds: Wall-clock length.
        event_count: Number of events in this session.
        watch_seconds: Playback seconds, summed from the events.
        is_first_session: Whether this is the user's first ever session.
        entry_screen: Screen of the first event.
        exit_screen: Screen of the last event.
    """

    session_id: int
    user_id: int
    device_id: int
    session_start: datetime
    session_end: datetime
    duration_seconds: int
    event_count: int
    watch_seconds: int
    is_first_session: bool
    entry_screen: str
    exit_screen: str

    def as_row(self) -> tuple[object, ...]:
        """Render as a tuple for binary ``COPY``.

        Returns:
            Values in :data:`seeder.loaders.SESSION_COLUMNS` order.
        """
        return (
            self.session_id,
            self.user_id,
            self.device_id,
            self.session_start,
            self.session_end,
            self.duration_seconds,
            self.event_count,
            self.watch_seconds,
            self.is_first_session,
            self.entry_screen,
            self.exit_screen,
        )


@dataclass(slots=True)
class UserTimeline:
    """Everything one user's simulated life produced.

    Attributes:
        sessions: Session rows in chronological order.
        events: Event rows in chronological order.
        subscriptions: Subscription terms.
        last_seen_at: Timestamp of the final event, or ``None`` if never active.
        churned_at: Churn date, or ``None`` if still active.
        is_premium: Whether a paid term is open at the window end.
    """

    sessions: list[SessionRow] = field(default_factory=list)
    events: list[EventRow] = field(default_factory=list)
    subscriptions: list[SubscriptionRow] = field(default_factory=list)
    last_seen_at: datetime | None = None
    churned_at: date | None = None
    is_premium: bool = False


def _build_intensity_table(
    countries: set[str],
    window_start: date,
    window_end: date,
) -> dict[str, list[float]]:
    """Precompute daily volume multipliers per country.

    Weekday, holiday and growth effects depend only on the date and the country, not
    on the user, so computing them once for the window removes a holiday scan from
    the inner loop. At 20 countries and 550 days this is 11,000 floats.

    Args:
        countries: Country names appearing in the population.
        window_start: First day of the window.
        window_end: Last day of the window.

    Returns:
        Mapping of country name to a list indexed by days since ``window_start``.
    """
    span = (window_end - window_start).days + 1
    table: dict[str, list[float]] = {}

    for country in countries:
        offset = utc_offset(country)
        values: list[float] = []
        for day_index in range(span):
            # The user's *local* date decides weekday and holiday, not the UTC
            # date. See the timezone note in seeder/seasonality.py.
            utc_day = window_start + timedelta(days=day_index)
            local_day = (
                datetime.combine(utc_day, datetime.min.time()) + offset
            ).date()
            values.append(
                weekday_multiplier(local_day.weekday())
                * holiday_multiplier(local_day, country)
                * growth_multiplier(day_index)
            )
        table[country] = values

    return table


def _churn_engagement_multiplier(active_days_28d: int) -> float:
    """Return the churn multiplier for a trailing activity level.

    Args:
        active_days_28d: Days with at least one event in the trailing 28.

    Returns:
        The multiplier from :data:`~seeder.config.CHURN_ENGAGEMENT_MULTIPLIER`.
    """
    multiplier = config.CHURN_ENGAGEMENT_MULTIPLIER[0][1]
    for threshold, value in config.CHURN_ENGAGEMENT_MULTIPLIER:
        if active_days_28d >= threshold:
            multiplier = value
        else:
            break
    return multiplier


def _churn_tenure_multiplier(months_since_signup: int) -> float:
    """Return the churn multiplier for account age.

    Args:
        months_since_signup: Whole months since signup.

    Returns:
        The multiplier, flattening past the end of the table.
    """
    table = config.CHURN_TENURE_MULTIPLIER
    if months_since_signup < len(table):
        return table[months_since_signup]
    return config.CHURN_TENURE_FLOOR_MULTIPLIER


def _pick_device(rng: Random, spec: UserSpec, device_form_factors: dict[int, str]) -> int:
    """Pick the device for one session.

    Args:
        rng: Seeded random source.
        spec: The user.
        device_form_factors: ``device_id`` to form factor, for the switch bias.

    Returns:
        A ``device_id``.
    """
    if rng.random() >= config.DEVICE_SWITCH_PROBABILITY:
        return spec.device_id

    # Switching favours larger screens: people migrate phone to TV, rarely back.
    candidates = [
        device_id for device_id in device_form_factors if device_id != spec.device_id
    ]
    if not candidates:
        return spec.device_id

    weights = [
        config.SWITCH_FORM_FACTOR_BIAS.get(device_form_factors[device_id], 1.0)
        for device_id in candidates
    ]
    return rng.choices(candidates, weights=weights, k=1)[0]


def simulate_user(  # noqa: PLR0912, PLR0915 - a timeline walk is inherently sequential
    rng: Random,
    spec: UserSpec,
    *,
    window_start: date,
    window_end: date,
    window_end_utc: datetime,
    selector: ContentSelector,
    intensity: list[float],
    availability: dict[int, list[ContentRow]],
    genre_names: tuple[str, ...],
    device_form_factors: dict[int, str],
    plan_ids: dict[str, int],
    plan_prices: dict[str, float],
    plan_names: dict[int, str],
    effects: EffectResolver | None,
    session_id_start: int,
    validate: bool = False,
) -> tuple[UserTimeline, int]:
    """Simulate one user's entire life in the window.

    Args:
        rng: Seeded random source.
        spec: The user to simulate.
        window_start: First day of the window.
        window_end: Last day of the window.
        window_end_utc: Window end as a UTC timestamp, the event-time ceiling.
        selector: Content selector shared across users.
        intensity: Precomputed daily multipliers for this user's country.
        availability: Cached availability lists keyed by days since window start.
        genre_names: Genre names for ``BROWSE_GENRE`` payloads.
        device_form_factors: ``device_id`` to form factor.
        plan_ids: Plan name to ``plan_id``.
        plan_prices: Plan name to list monthly price.
        plan_names: ``plan_id`` to plan name.
        effects: Experiment effect resolver, or ``None`` when no experiments exist.
        session_id_start: First session id available to this user.
        validate: Run journey invariant checks on sampled plans.

    Returns:
        ``(timeline, next_session_id)``.
    """
    timeline = UserTimeline()
    lifecycle = SubscriptionLifecycle(
        rng, user_id=spec.user_id, country_tier=spec.country_tier
    )
    subscription_id_offset = (spec.user_id - 1) * ID_BLOCK_PER_USER

    session_id = session_id_start

    # Trailing windows. Deques of (date, value) evicted from the left as the
    # cursor advances, so every feature is strictly backward-looking by
    # construction rather than by a filter that could be written wrong.
    watch_history: deque[tuple[date, int]] = deque()
    completion_history: deque[tuple[date, int]] = deque()
    search_history: deque[tuple[date, int]] = deque()
    active_days: deque[date] = deque()

    watch_14d = 0
    completions_14d = 0
    searches_14d = 0

    churned = False
    resurrect_on: date | None = None
    first_session_done = False

    cursor = spec.signup_date
    while cursor <= window_end:
        day_index = (cursor - window_start).days

        # ---------------------------------------------------------------
        # Trailing-window maintenance. Done before any decision on this day, so
        # every feature reflects the past only.
        # ---------------------------------------------------------------
        conversion_cutoff = cursor - timedelta(days=CONVERSION_WINDOW_DAYS)
        while watch_history and watch_history[0][0] < conversion_cutoff:
            watch_14d -= watch_history.popleft()[1]
        while completion_history and completion_history[0][0] < conversion_cutoff:
            completions_14d -= completion_history.popleft()[1]
        while search_history and search_history[0][0] < conversion_cutoff:
            searches_14d -= search_history.popleft()[1]

        churn_cutoff = cursor - timedelta(days=CHURN_WINDOW_DAYS)
        while active_days and active_days[0] < churn_cutoff:
            active_days.popleft()

        # ---------------------------------------------------------------
        # Resurrection
        # ---------------------------------------------------------------
        if churned and resurrect_on is not None and cursor >= resurrect_on:
            churned = False
            resurrect_on = None
            timeline.churned_at = None

        if churned:
            cursor += timedelta(days=1)
            continue

        # ---------------------------------------------------------------
        # Trial expiry — checked before conversion so a trial that lapses today
        # cannot also convert today.
        # ---------------------------------------------------------------
        remaining = lifecycle.trial_days_remaining(cursor)
        if remaining is not None and remaining < 0:
            lifecycle.expire_trial(cursor)

        # ---------------------------------------------------------------
        # Conversion. Evaluated daily from trailing features only.
        # ---------------------------------------------------------------
        if not lifecycle.is_paying:
            features = TrailingFeatures(
                watch_hours_14d=watch_14d / 3600.0,
                completed_videos_14d=completions_14d,
                searches_14d=searches_14d,
                weeks_since_signup=(cursor - spec.signup_date).days / 7.0,
                trial_days_remaining=lifecycle.trial_days_remaining(cursor),
            )
            probability = conversion_probability(
                features,
                persona=spec.profile.persona,
                channel=spec.channel_name,
                country_tier=spec.country_tier,
            )

            # An experiment on subscription_conversion shifts log-odds additively,
            # which is the only correct way to apply a relative lift to a logistic
            # model.
            if effects is not None:
                shift = effects.additive(spec.user_id, "subscription_conversion", cursor)
                if shift:
                    import math

                    # Odds-ratio adjustment. Safe at probability == 0: odds is 0,
                    # so the result is 0 and a zero-engagement user stays at zero
                    # rather than being lifted by an experiment they cannot benefit
                    # from. Clamped as a hazard, not floored — see clamp_hazard.
                    odds = probability / (1.0 - probability)
                    adjusted = odds * math.exp(shift)
                    probability = config.clamp_hazard(
                        adjusted / (1.0 + adjusted),
                        cap=config.MAX_DAILY_CONVERSION_PROBABILITY,
                    )

            if rng.random() < probability:
                if lifecycle.in_trial:
                    lifecycle.start_paid(
                        cursor,
                        id_offset=subscription_id_offset,
                        plan_ids=plan_ids,
                        plan_prices=plan_prices,
                        from_trial=True,
                    )
                elif rng.random() < config.TRIAL_START_PROBABILITY:
                    lifecycle.start_trial(
                        cursor,
                        id_offset=subscription_id_offset,
                        plan_ids=plan_ids,
                        plan_prices=plan_prices,
                    )
                else:
                    lifecycle.start_paid(
                        cursor,
                        id_offset=subscription_id_offset,
                        plan_ids=plan_ids,
                        plan_prices=plan_prices,
                    )

        # ---------------------------------------------------------------
        # Plan changes, evaluated monthly via a daily-equivalent probability.
        # ---------------------------------------------------------------
        if lifecycle.is_paying and rng.random() < (
            config.PLAN_CHANGE_MONTHLY_PROBABILITY / 30.44
        ):
            lifecycle.change_plan(
                cursor,
                id_offset=subscription_id_offset,
                plan_ids=plan_ids,
                plan_prices=plan_prices,
                plan_names=plan_names,
            )

        # ---------------------------------------------------------------
        # Sessions for today
        # ---------------------------------------------------------------
        days_since_signup = (cursor - spec.signup_date).days
        frequency = spec.profile.frequency_at(days_since_signup)

        if effects is not None:
            frequency *= effects.multiplier(spec.user_id, "sessions_per_user", cursor)
            if days_since_signup <= 7:
                frequency *= effects.multiplier(spec.user_id, "day7_retention", cursor)

        expected = (frequency / 7.0) * intensity[min(day_index, len(intensity) - 1)]
        # Poisson via its exponential-interarrival characterisation, so no numpy
        # dependency and the draw stays inside the seeded rng.
        session_count = 0
        if expected > 0.0:
            accumulated = 0.0
            while session_count < MAX_SESSIONS_PER_DAY:
                accumulated += rng.expovariate(expected)
                if accumulated > 1.0:
                    break
                session_count += 1

        if session_count:
            candidates = availability.get(day_index)
            if candidates is None:
                candidates = selector.available_on(cursor)
                availability[day_index] = candidates

            day_watch = 0
            day_completions = 0
            day_searches = 0

            for _ in range(session_count):
                started_at = draw_session_start(
                    rng, cursor, spec.country_name, not_after=window_end_utc
                )

                # Choose titles first: the plan needs their runtimes to decide how
                # long playback lasts.
                titles = selector.choose(
                    rng,
                    profile=spec.profile,
                    days_since_signup=days_since_signup,
                    day=cursor,
                    count=8,
                    candidates=candidates,
                )
                if not titles:
                    continue

                slot_runtimes = [
                    (row.runtime_minutes * 60, row.content_type == "series")
                    for row in titles
                ]

                plan = plan_session(
                    rng,
                    profile=spec.profile,
                    days_since_signup=days_since_signup,
                    is_premium=lifecycle.is_paying,
                    slot_runtimes=slot_runtimes,
                    search_terms=make_search_terms(
                        rng, selector, titles=titles, count=4
                    ),
                    genre_names=genre_names,
                )

                if validate and rng.random() < VALIDATION_SAMPLE_RATE:
                    validate_plan(plan)

                rows, ended_at = materialise(
                    plan,
                    session_id=session_id,
                    user_id=spec.user_id,
                    started_at=started_at,
                    slot_content_ids=[row.content_id for row in titles],
                    ceiling=window_end_utc,
                )
                if len(rows) < 2:
                    continue

                session_watch = watch_seconds_of(rows)
                duration = max(int((ended_at - started_at).total_seconds()), 0)
                low, high = config.SESSION_DURATION_BOUNDS
                duration = min(max(duration, low if session_watch else 1), high)
                # ck_sessions_watch_within_duration: playback cannot exceed
                # wall-clock time.
                session_watch = min(session_watch, duration)

                timeline.sessions.append(
                    SessionRow(
                        session_id=session_id,
                        user_id=spec.user_id,
                        device_id=_pick_device(rng, spec, device_form_factors),
                        session_start=started_at,
                        session_end=started_at + timedelta(seconds=duration),
                        duration_seconds=duration,
                        event_count=min(len(rows), 32_767),
                        watch_seconds=session_watch,
                        is_first_session=not first_session_done,
                        entry_screen=rows[0].screen,
                        exit_screen=rows[-1].screen,
                    )
                )
                timeline.events.extend(rows)
                first_session_done = True
                session_id += 1

                day_watch += session_watch
                day_completions += len(plan.completed_slots)
                day_searches += sum(
                    1 for row in rows if row.event_name == "SEARCH"
                )

                if timeline.last_seen_at is None or ended_at > timeline.last_seen_at:
                    timeline.last_seen_at = ended_at

            if timeline.sessions:
                active_days.append(cursor)
                watch_history.append((cursor, day_watch))
                completion_history.append((cursor, day_completions))
                search_history.append((cursor, day_searches))
                watch_14d += day_watch
                completions_14d += day_completions
                searches_14d += day_searches

        # ---------------------------------------------------------------
        # Churn hazard, evaluated from trailing activity only.
        # ---------------------------------------------------------------
        months = days_since_signup // 30
        hazard = (
            spec.profile.churn_propensity
            * _churn_tenure_multiplier(months)
            * _churn_engagement_multiplier(len(active_days))
        )
        if lifecycle.is_paying:
            hazard *= config.PREMIUM_CHURN_DAMPENER

        # Capped, not floored: this is evaluated every day of the user's tenure, so
        # a floor would compound into guaranteed churn for users whose true hazard
        # is negligible. See seeder.config.clamp_hazard.
        daily_hazard = config.clamp_hazard(
            hazard / 30.44, cap=config.MAX_DAILY_CHURN_HAZARD
        )

        if rng.random() < daily_hazard:
            churned = True
            timeline.churned_at = cursor
            if lifecycle.has_open_term:
                # A small share of paid churn is involuntary (payment failure),
                # which is a different problem with a different fix and worth
                # separating in the churn-reason mix.
                lifecycle.cancel(cursor, involuntary=rng.random() < 0.14)

            if rng.random() < config.RESURRECTION_PROBABILITY:
                low, high = config.RESURRECTION_GAP_DAYS
                resurrect_on = cursor + timedelta(days=rng.randint(low, high))

        cursor += timedelta(days=1)

    # A user inactive for longer than the threshold at the window end is labelled
    # churned even if the hazard never fired. This is the *observable* definition
    # the SQL uses, and it must agree with what the data shows.
    if timeline.churned_at is None and timeline.last_seen_at is not None:
        idle_days = (window_end - timeline.last_seen_at.date()).days
        if idle_days > config.CHURN_INACTIVITY_THRESHOLD_DAYS:
            timeline.churned_at = timeline.last_seen_at.date() + timedelta(
                days=config.CHURN_INACTIVITY_THRESHOLD_DAYS
            )

    timeline.subscriptions = lifecycle.finish()
    timeline.is_premium = lifecycle.is_paying

    return timeline, session_id


def build_intensity_tables(
    users: list[UserSpec],
    window_start: date,
    window_end: date,
) -> dict[str, list[float]]:
    """Precompute per-country daily intensity for the whole window.

    Args:
        users: The population, to determine which countries appear.
        window_start: First day of the window.
        window_end: Last day of the window.

    Returns:
        Mapping of country name to daily multipliers indexed from
        ``window_start``.
    """
    return _build_intensity_table(
        {spec.country_name for spec in users}, window_start, window_end
    )


def describe_seasonality(window_start: date, window_end: date, country: str) -> list[dict[str, object]]:
    """Return per-day seasonality detail for the data-quality report.

    Args:
        window_start: First day of the window.
        window_end: Last day of the window.
        country: Country to describe.

    Returns:
        One entry per day with its multipliers and any active holidays, so the
        report can annotate the volume chart and a reader can confirm the December
        spike was intentional.
    """
    offset = utc_offset(country)
    rows: list[dict[str, object]] = []
    span = (window_end - window_start).days + 1

    for day_index in range(span):
        utc_day = window_start + timedelta(days=day_index)
        # Midnight UTC shifted into local time. Explicitly UTC-anchored rather
        # than using astimezone(), which would read the generating machine's
        # timezone and make the report non-reproducible.
        local_day = (
            datetime.combine(utc_day, datetime.min.time(), tzinfo=UTC) + offset
        ).date()
        rows.append(
            {
                "date": utc_day.isoformat(),
                "weekday": weekday_multiplier(local_day.weekday()),
                "holiday": holiday_multiplier(local_day, country),
                "growth": growth_multiplier(day_index),
                "holidays": active_holidays(local_day, country),
            }
        )

    return rows


__all__ = [
    "CHURN_WINDOW_DAYS",
    "CONVERSION_WINDOW_DAYS",
    "MAX_SESSIONS_PER_DAY",
    "SessionRow",
    "UserTimeline",
    "build_intensity_tables",
    "describe_seasonality",
    "simulate_user",
]

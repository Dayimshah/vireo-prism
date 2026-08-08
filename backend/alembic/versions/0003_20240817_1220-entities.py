"""Create the entity tables: users, content, sessions, subscriptions, experiments.

Revision 0003 of 6. These six tables hold generated data and are populated
exclusively by ``python -m seeder``.

``core.events`` is deliberately absent — it is range-partitioned and gets its own
revision (0004) because partitioned DDL does not fit the same shape.

Constraint philosophy
---------------------
Every business invariant that *can* be expressed as a ``CHECK`` is expressed as a
``CHECK``, rather than being left to the generator to honour politely. Two
reasons this is worth the verbosity:

1. A generator bug becomes a failed insert instead of a plausible-looking chart.
   ``ck_sessions_no_future_start`` is the clearest example: "no session may be
   timestamped in the future" is a requirement, so the database enforces it.
2. The constraints document the domain. A reviewer reading
   ``ck_content_series_has_episodes`` learns that ``season_count`` is
   non-nullable precisely when ``content_type = 'series'`` without opening the
   seeder.

Denormalised columns
--------------------
``sessions.duration_seconds``, ``sessions.event_count`` and
``sessions.watch_seconds`` are derivable from ``core.events`` by aggregation, and
are stored anyway. That is a considered trade, not an oversight: the session
analytics page needs percentiles over 420k sessions, and recomputing them from
3.4M event rows on every request would turn a 40ms query into a 4s one. The
values are written once by the seeder inside the same transaction as the events
they summarise, and ``tests/test_seeder.py`` asserts they agree with a
recomputation from ``core.events``.

Revision ID: 0003
Revises: 0002
Created: 2024-08-17 12:20:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Enum types created in revision 0001.
#
# `create_type=False` is essential: without it, SQLAlchemy emits CREATE TYPE
# again and the migration fails on a duplicate-object error.
# ---------------------------------------------------------------------------
CONTENT_TYPE = postgresql.ENUM(name="content_type", schema="core", create_type=False)
SUB_STATUS = postgresql.ENUM(name="sub_status", schema="core", create_type=False)
BILLING_PERIOD = postgresql.ENUM(name="billing_period", schema="core", create_type=False)
EXP_STATUS = postgresql.ENUM(name="exp_status", schema="core", create_type=False)

#: Screen names a session may start or end on. Kept as a CHECK rather than an
#: enum because the set is small, unordered, and only ever compared for
#: equality — an enum would buy nothing over a constrained varchar here.
SCREENS = (
    "splash",
    "home",
    "search",
    "browse",
    "detail",
    "player",
    "watchlist",
    "profile",
    "settings",
    "paywall",
)

_SCREEN_LIST = ", ".join(f"'{screen}'" for screen in SCREENS)


def upgrade() -> None:
    """Create the six entity tables."""
    # =======================================================================
    # core.users
    #
    # `is_premium` and `churned_at` are current-state columns maintained by the
    # seeder as the simulation advances. They duplicate information recoverable
    # from core.subscriptions, and exist because "how many premium users do we
    # have right now" is asked on every page load and should not require a
    # correlated subquery over subscription history.
    # =======================================================================
    op.create_table(
        "users",
        sa.Column("user_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("signup_date", sa.Date, nullable=False),
        sa.Column("country_id", sa.SmallInteger, nullable=False),
        sa.Column(
            "device_id",
            sa.SmallInteger,
            nullable=False,
            comment="Device used at signup. Individual sessions carry their own device_id.",
        ),
        sa.Column("channel_id", sa.SmallInteger, nullable=False),
        sa.Column("persona_id", sa.SmallInteger, nullable=False),
        sa.Column(
            "is_premium",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
            comment="Current paid state. Denormalised from core.subscriptions for read speed.",
        ),
        sa.Column("gender", sa.String(16), nullable=False),
        sa.Column("age", sa.SmallInteger, nullable=False),
        sa.Column("app_version", sa.String(12), nullable=False),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp of the user's most recent event. NULL until first session.",
        ),
        sa.Column(
            "churned_at",
            sa.Date,
            nullable=True,
            comment="Date the user was classified as churned. NULL means active.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["country_id"], ["core.countries.country_id"], name="fk_users_country", onupdate="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["device_id"], ["core.devices.device_id"], name="fk_users_device", onupdate="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["core.marketing_channels.channel_id"],
            name="fk_users_channel",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["persona_id"], ["core.personas.persona_id"], name="fk_users_persona", onupdate="CASCADE"
        ),
        sa.CheckConstraint(
            "gender IN ('male', 'female', 'non_binary', 'undisclosed')",
            name="ck_users_gender",
        ),
        sa.CheckConstraint("age BETWEEN 13 AND 90", name="ck_users_age_range"),
        # The simulation must never produce a user who signed up tomorrow.
        sa.CheckConstraint(
            "signup_date <= CURRENT_DATE",
            name="ck_users_no_future_signup",
        ),
        sa.CheckConstraint(
            "churned_at IS NULL OR churned_at >= signup_date",
            name="ck_users_churn_after_signup",
        ),
        sa.CheckConstraint(
            "app_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'",
            name="ck_users_app_version_semver",
        ),
        schema="core",
        comment="Vireo subscriber base. One row per registered account.",
    )

    # =======================================================================
    # core.content
    #
    # popularity_score is an editorial 0-100 signal that drives selection weight
    # during generation. It is an *input* to the simulation, which makes the
    # "popularity vs completion rate" scatter on the Content page meaningful:
    # the two axes are genuinely independent measures.
    # =======================================================================
    op.create_table(
        "content",
        sa.Column("content_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("genre_id", sa.SmallInteger, nullable=False),
        sa.Column("content_type", CONTENT_TYPE, nullable=False),
        sa.Column(
            "runtime_minutes",
            sa.SmallInteger,
            nullable=False,
            comment="Per-episode runtime for series, total runtime for films.",
        ),
        sa.Column("release_year", sa.SmallInteger, nullable=False),
        sa.Column("language", sa.String(32), nullable=False),
        sa.Column("age_rating", sa.String(8), nullable=False),
        sa.Column(
            "popularity_score",
            sa.Numeric(5, 2),
            nullable=False,
            comment="Editorial 0-100 signal. Drives title selection weight during generation.",
        ),
        sa.Column("season_count", sa.SmallInteger, nullable=True),
        sa.Column("episode_count", sa.SmallInteger, nullable=True),
        sa.Column("is_original", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("added_on", sa.Date, nullable=False),
        sa.ForeignKeyConstraint(
            ["genre_id"], ["core.genres.genre_id"], name="fk_content_genre", onupdate="CASCADE"
        ),
        sa.UniqueConstraint("title", "release_year", name="uq_content_title_year"),
        sa.CheckConstraint("runtime_minutes BETWEEN 1 AND 400", name="ck_content_runtime_range"),
        sa.CheckConstraint("release_year BETWEEN 1950 AND 2030", name="ck_content_year_range"),
        sa.CheckConstraint(
            "popularity_score BETWEEN 0 AND 100", name="ck_content_popularity_range"
        ),
        sa.CheckConstraint(
            "age_rating IN ('U', 'U/A 7+', 'U/A 13+', 'U/A 16+', 'A')",
            name="ck_content_age_rating",
        ),
        # Episodic and non-episodic content are structurally different. Enforcing
        # it here means the runtime maths in the watch-time queries can trust
        # that episode_count is present exactly when it is needed.
        sa.CheckConstraint(
            "(content_type = 'series') = (season_count IS NOT NULL)",
            name="ck_content_series_has_seasons",
        ),
        sa.CheckConstraint(
            "(content_type = 'series') = (episode_count IS NOT NULL)",
            name="ck_content_series_has_episodes",
        ),
        sa.CheckConstraint(
            "season_count IS NULL OR season_count BETWEEN 1 AND 20",
            name="ck_content_season_range",
        ),
        sa.CheckConstraint(
            "episode_count IS NULL OR episode_count BETWEEN 1 AND 400",
            name="ck_content_episode_range",
        ),
        schema="core",
        comment="Vireo catalogue. Fictional titles; see docs/seeder-design.md for generation.",
    )

    # =======================================================================
    # core.sessions
    #
    # device_id is repeated from core.users on purpose: a real user watches on a
    # phone at lunch and a TV at night, and the device-switching query depends on
    # the per-session value. users.device_id is the signup device only.
    # =======================================================================
    op.create_table(
        "sessions",
        sa.Column("session_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column(
            "device_id",
            sa.SmallInteger,
            nullable=False,
            comment="Device for THIS session, which may differ from the signup device.",
        ),
        sa.Column("session_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "duration_seconds",
            sa.Integer,
            nullable=False,
            comment="Denormalised from session_end - session_start for percentile queries.",
        ),
        sa.Column(
            "event_count",
            sa.SmallInteger,
            nullable=False,
            comment="Denormalised count of core.events rows for this session.",
        ),
        sa.Column(
            "watch_seconds",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
            comment="Denormalised sum of playback seconds within this session.",
        ),
        sa.Column("is_first_session", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("entry_screen", sa.String(32), nullable=False),
        sa.Column("exit_screen", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["core.users.user_id"], name="fk_sessions_user", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["device_id"], ["core.devices.device_id"], name="fk_sessions_device", onupdate="CASCADE"
        ),
        sa.CheckConstraint("session_end >= session_start", name="ck_sessions_end_after_start"),
        # The stated requirement "no future sessions", enforced rather than trusted.
        sa.CheckConstraint("session_start <= now()", name="ck_sessions_no_future_start"),
        sa.CheckConstraint("duration_seconds >= 0", name="ck_sessions_duration_non_negative"),
        # A 12-hour session is a data bug, not a binge. Bounded so a broken
        # generator cannot silently skew the duration percentiles.
        sa.CheckConstraint("duration_seconds <= 43200", name="ck_sessions_duration_sane"),
        sa.CheckConstraint("event_count >= 2", name="ck_sessions_min_events"),
        sa.CheckConstraint("watch_seconds >= 0", name="ck_sessions_watch_non_negative"),
        # Playback cannot exceed wall-clock time in the session.
        sa.CheckConstraint(
            "watch_seconds <= duration_seconds",
            name="ck_sessions_watch_within_duration",
        ),
        sa.CheckConstraint(f"entry_screen IN ({_SCREEN_LIST})", name="ck_sessions_entry_screen"),
        sa.CheckConstraint(f"exit_screen IN ({_SCREEN_LIST})", name="ck_sessions_exit_screen"),
        schema="core",
        comment="One row per app-open to app-close. Parent of core.events.",
    )

    # =======================================================================
    # core.subscriptions
    #
    # Full history, not current state: a user who subscribed, cancelled and
    # returned has three rows. This is what makes MRR movement (new / expansion /
    # contraction / churn / reactivation) computable rather than approximated.
    # =======================================================================
    op.create_table(
        "subscriptions",
        sa.Column("subscription_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("plan_id", sa.SmallInteger, nullable=False),
        sa.Column("started_on", sa.Date, nullable=False),
        sa.Column(
            "ended_on",
            sa.Date,
            nullable=True,
            comment="NULL means the subscription is still open.",
        ),
        sa.Column("status", SUB_STATUS, nullable=False),
        sa.Column("billing_period", BILLING_PERIOD, nullable=False),
        sa.Column(
            "mrr_usd",
            sa.Numeric(8, 2),
            nullable=False,
            comment="Monthly recurring revenue after the billing-period discount.",
        ),
        sa.Column("cancel_reason", sa.String(48), nullable=True),
        sa.Column(
            "is_trial_conversion",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
            comment="True when this paid subscription followed a trial that converted.",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["core.users.user_id"], name="fk_subscriptions_user", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["core.subscription_plans.plan_id"],
            name="fk_subscriptions_plan",
            onupdate="CASCADE",
        ),
        sa.CheckConstraint(
            "ended_on IS NULL OR ended_on >= started_on", name="ck_subs_end_after_start"
        ),
        sa.CheckConstraint("started_on <= CURRENT_DATE", name="ck_subs_no_future_start"),
        sa.CheckConstraint("mrr_usd >= 0", name="ck_subs_mrr_non_negative"),
        # An open subscription is active, trialing or paused; a closed one is
        # cancelled or expired. Any other pairing is contradictory.
        sa.CheckConstraint(
            "(ended_on IS NULL) = (status IN ('trialing', 'active', 'paused'))",
            name="ck_subs_status_matches_open_state",
        ),
        # A cancellation reason only makes sense on a cancelled subscription.
        sa.CheckConstraint(
            "cancel_reason IS NULL OR status IN ('cancelled', 'expired')",
            name="ck_subs_reason_requires_cancel",
        ),
        schema="core",
        comment="Full subscription history. Multiple rows per user across their lifetime.",
    )

    # =======================================================================
    # core.experiments
    # =======================================================================
    op.create_table(
        "experiments",
        sa.Column("experiment_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "key",
            sa.String(64),
            nullable=False,
            comment="Stable slug used in URLs and by the API, e.g. 'autoplay-preview-v2'.",
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("hypothesis", sa.Text, nullable=False),
        sa.Column(
            "primary_metric",
            sa.String(48),
            nullable=False,
            comment="The single metric the experiment is judged on.",
        ),
        sa.Column(
            "variants",
            postgresql.JSONB,
            nullable=False,
            comment='Ordered variant names, control first: ["control", "variant_a"].',
        ),
        sa.Column(
            "traffic_allocation",
            sa.Numeric(3, 2),
            nullable=False,
            comment="Fraction of eligible users enrolled, 0-1.",
        ),
        sa.Column("started_on", sa.Date, nullable=False),
        sa.Column("ended_on", sa.Date, nullable=True),
        sa.Column("status", EXP_STATUS, nullable=False),
        sa.UniqueConstraint("key", name="uq_experiments_key"),
        sa.CheckConstraint("key ~ '^[a-z0-9-]+$'", name="ck_experiments_key_slug"),
        sa.CheckConstraint(
            "traffic_allocation > 0 AND traffic_allocation <= 1",
            name="ck_experiments_allocation_range",
        ),
        sa.CheckConstraint(
            "ended_on IS NULL OR ended_on >= started_on", name="ck_experiments_end_after_start"
        ),
        sa.CheckConstraint(
            "(status = 'running') = (ended_on IS NULL)",
            name="ck_experiments_running_has_no_end",
        ),
        # Two or more variants, control included. A one-armed test is not a test.
        sa.CheckConstraint(
            "jsonb_typeof(variants) = 'array' AND jsonb_array_length(variants) >= 2",
            name="ck_experiments_min_two_variants",
        ),
        sa.CheckConstraint(
            "primary_metric IN ("
            "'subscription_conversion', 'completion_rate', 'session_duration', "
            "'sessions_per_user', 'trailer_to_start', 'day7_retention'"
            ")",
            name="ck_experiments_primary_metric",
        ),
        schema="core",
        comment="A/B test registry. Results are computed in SQL, not stored.",
    )

    # =======================================================================
    # core.experiment_assignments
    #
    # The bridge table the original nine-table sketch was missing: user-to-
    # experiment is many-to-many, and the composite primary key is what
    # guarantees a user cannot be enrolled twice in the same experiment, which
    # would double-count them in every significance test.
    # =======================================================================
    op.create_table(
        "experiment_assignments",
        sa.Column("experiment_id", sa.Integer, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("variant", sa.String(32), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("experiment_id", "user_id", name="pk_experiment_assignments"),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["core.experiments.experiment_id"],
            name="fk_assignments_experiment",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["core.users.user_id"], name="fk_assignments_user", ondelete="CASCADE"
        ),
        sa.CheckConstraint("assigned_at <= now()", name="ck_assignments_no_future_assignment"),
        schema="core",
        comment="Which user saw which variant. Composite PK prevents double enrolment.",
    )


def downgrade() -> None:
    """Drop the entity tables in reverse dependency order."""
    for table in (
        "experiment_assignments",
        "experiments",
        "subscriptions",
        "sessions",
        "content",
        "users",
    ):
        op.drop_table(table, schema="core")

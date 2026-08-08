"""Create the six dimension tables and populate their reference rows.

Revision 0002 of 6.

Why the rows live in the migration, not the seeder
--------------------------------------------------
These six tables are *reference data*, not generated data. Three reasons they
belong here:

1. The API validates every incoming filter value against these tables. A
   migrated-but-unseeded database would therefore serve a broken ``/users``
   endpoint, which makes the schema and the reference rows one indivisible unit.
2. They carry coefficients the simulation *reads* rather than invents —
   ``personas.base_churn_propensity`` and ``marketing_channels.cac_usd`` are
   inputs to generation, so they must exist before the seeder starts.
3. They are small, fixed, and hand-curated. Regenerating them randomly on each
   seed would make published CAC and LTV figures move between runs.

The seeder owns ``users``, ``sessions``, ``events``, ``subscriptions``,
``experiments`` and ``experiment_assignments``. Nothing else.

Persona coefficients are the load-bearing part of this file. They are the
planted signal that the analytics layer later rediscovers independently: a
Binge Watcher really does open the app more often and finish more of what they
start, and the retention curves in the dashboard are true consequences of the
numbers below rather than decoration.

Revision ID: 0002
Revises: 0001
Created: 2024-08-17 12:10:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ===========================================================================
# Reference data
# ===========================================================================

#: Markets Vireo operates in.
#:
#: ``tier`` is a pricing and monetisation band, not a judgement about the
#: country: 1 = high ARPU / high CAC, 2 = mid, 3 = high volume / low ARPU. The
#: seeder uses it to modulate subscription probability and plan mix, so the
#: "India converts at lower ARPU but higher volume" pattern visible in the
#: dashboard originates here.
COUNTRIES: Final[tuple[dict[str, Any], ...]] = (
    {"iso_code": "IN", "name": "India", "region": "APAC", "tier": 3},
    {"iso_code": "US", "name": "United States", "region": "North America", "tier": 1},
    {"iso_code": "GB", "name": "United Kingdom", "region": "Europe", "tier": 1},
    {"iso_code": "CA", "name": "Canada", "region": "North America", "tier": 1},
    {"iso_code": "AU", "name": "Australia", "region": "APAC", "tier": 1},
    {"iso_code": "DE", "name": "Germany", "region": "Europe", "tier": 1},
    {"iso_code": "FR", "name": "France", "region": "Europe", "tier": 1},
    {"iso_code": "JP", "name": "Japan", "region": "APAC", "tier": 1},
    {"iso_code": "KR", "name": "South Korea", "region": "APAC", "tier": 2},
    {"iso_code": "SG", "name": "Singapore", "region": "APAC", "tier": 1},
    {"iso_code": "AE", "name": "United Arab Emirates", "region": "MEA", "tier": 2},
    {"iso_code": "BR", "name": "Brazil", "region": "LATAM", "tier": 3},
    {"iso_code": "MX", "name": "Mexico", "region": "LATAM", "tier": 3},
    {"iso_code": "ES", "name": "Spain", "region": "Europe", "tier": 2},
    {"iso_code": "IT", "name": "Italy", "region": "Europe", "tier": 2},
    {"iso_code": "NL", "name": "Netherlands", "region": "Europe", "tier": 1},
    {"iso_code": "ZA", "name": "South Africa", "region": "MEA", "tier": 3},
    {"iso_code": "ID", "name": "Indonesia", "region": "APAC", "tier": 3},
    {"iso_code": "PH", "name": "Philippines", "region": "APAC", "tier": 3},
    {"iso_code": "NG", "name": "Nigeria", "region": "MEA", "tier": 3},
)

#: Playback surfaces.
#:
#: ``form_factor`` is what the analytics actually groups by — a phone session
#: behaves like a phone session whether it is iOS or Android, while a TV session
#: is three times longer. Keeping platform and form factor as separate columns
#: lets the dashboard slice either way without a CASE expression in the SQL.
DEVICES: Final[tuple[dict[str, Any], ...]] = (
    {"name": "iPhone", "platform": "iOS", "form_factor": "phone"},
    {"name": "Android Phone", "platform": "Android", "form_factor": "phone"},
    {"name": "iPad", "platform": "iOS", "form_factor": "tablet"},
    {"name": "Android Tablet", "platform": "Android", "form_factor": "tablet"},
    {"name": "Web Desktop", "platform": "Web", "form_factor": "desktop"},
    {"name": "Smart TV", "platform": "TV", "form_factor": "tv"},
    {"name": "Fire TV Stick", "platform": "TV", "form_factor": "tv"},
    {"name": "Chromecast", "platform": "TV", "form_factor": "tv"},
    {"name": "PlayStation", "platform": "Console", "form_factor": "console"},
)

#: Acquisition sources.
#:
#: ``cac_usd`` is blended customer acquisition cost. The spread is deliberate and
#: is the second planted signal in the dataset: paid social is expensive and
#: brings low-intent users, referral is cheap and brings users who stay. The
#: LTV:CAC quadrant chart on the Marketing page is the payoff.
MARKETING_CHANNELS: Final[tuple[dict[str, Any], ...]] = (
    {"name": "Organic Search", "channel_group": "Organic", "is_paid": False, "cac_usd": 0.00},
    {"name": "Direct", "channel_group": "Organic", "is_paid": False, "cac_usd": 0.00},
    {"name": "Organic Social", "channel_group": "Organic", "is_paid": False, "cac_usd": 0.00},
    {"name": "Referral", "channel_group": "Referral", "is_paid": True, "cac_usd": 4.20},
    {"name": "Email", "channel_group": "Owned", "is_paid": False, "cac_usd": 0.75},
    {"name": "App Store Featured", "channel_group": "Organic", "is_paid": False, "cac_usd": 0.00},
    {"name": "Paid Search", "channel_group": "Paid", "is_paid": True, "cac_usd": 18.40},
    {"name": "Paid Social", "channel_group": "Paid", "is_paid": True, "cac_usd": 26.90},
    {"name": "Display", "channel_group": "Paid", "is_paid": True, "cac_usd": 31.50},
    {"name": "Influencer", "channel_group": "Paid", "is_paid": True, "cac_usd": 14.10},
    {"name": "Affiliate", "channel_group": "Partner", "is_paid": True, "cac_usd": 9.80},
    {"name": "Telco Bundle", "channel_group": "Partner", "is_paid": True, "cac_usd": 6.50},
)

#: Behavioural archetypes — the engine of the whole simulation.
#:
#: Each row is a set of base rates that ``seeder/personas.py`` perturbs per user
#: and ``seeder/journeys.py`` turns into a transition matrix. The three columns
#: stored here are the ones the *analytics layer* needs in order to explain a
#: chart; richer per-persona parameters (genre affinity, search-vs-browse bias,
#: device preference) live in the seeder because no query reads them.
#:
#: The values are internally consistent on purpose. Binge Watcher has the
#: highest session frequency and completion rate and the lowest churn
#: propensity; Churn Risk is the mirror image. That relationship is what the
#: retention-by-persona chart recovers.
PERSONAS: Final[tuple[dict[str, Any], ...]] = (
    {
        "name": "Binge Watcher",
        "description": (
            "Consumes whole seasons in long evening blocks. Highest engagement "
            "and lowest churn risk; the segment worth protecting."
        ),
        "base_sessions_per_week": 5.40,
        "base_completion_rate": 0.82,
        "base_churn_propensity": 0.04,
    },
    {
        "name": "Movie Lover",
        "description": (
            "Watches one feature-length title per sitting, rarely series. "
            "Moderate frequency, high completion, price-insensitive."
        ),
        "base_sessions_per_week": 2.80,
        "base_completion_rate": 0.74,
        "base_churn_propensity": 0.07,
    },
    {
        "name": "Anime Fan",
        "description": (
            "Narrow genre affinity, very high episode throughput, heavy "
            "watchlist use. Churns when the catalogue stops refreshing."
        ),
        "base_sessions_per_week": 4.60,
        "base_completion_rate": 0.79,
        "base_churn_propensity": 0.09,
    },
    {
        "name": "Sports Fan",
        "description": (
            "Event-driven and bursty. Long sessions clustered around fixtures, "
            "near-zero activity between them."
        ),
        "base_sessions_per_week": 2.10,
        "base_completion_rate": 0.61,
        "base_churn_propensity": 0.14,
    },
    {
        "name": "Casual Viewer",
        "description": (
            "One weekend session, browses more than it watches. The largest "
            "segment by headcount and the hardest to monetise."
        ),
        "base_sessions_per_week": 1.20,
        "base_completion_rate": 0.44,
        "base_churn_propensity": 0.18,
    },
    {
        "name": "Premium Loyalist",
        "description": (
            "Long-tenured paying subscriber, multi-device, predictable cadence. "
            "Highest lifetime value even at moderate engagement."
        ),
        "base_sessions_per_week": 3.90,
        "base_completion_rate": 0.77,
        "base_churn_propensity": 0.03,
    },
    {
        "name": "Churn Risk",
        "description": (
            "Declining session frequency month over month, abandons most "
            "playbacks. Exists to be detectable by the churn scorecard."
        ),
        "base_sessions_per_week": 0.70,
        "base_completion_rate": 0.28,
        "base_churn_propensity": 0.46,
    },
    {
        "name": "New Explorer",
        "description": (
            "Inside the first 30 days. Heavy search and browse, shallow "
            "watching, outcome still undetermined."
        ),
        "base_sessions_per_week": 3.10,
        "base_completion_rate": 0.52,
        "base_churn_propensity": 0.22,
    },
)

#: Catalogue genres. Kept as a table rather than an enum because a streaming
#: business adds genres without shipping code.
GENRES: Final[tuple[str, ...]] = (
    "Action",
    "Anime",
    "Comedy",
    "Crime",
    "Documentary",
    "Drama",
    "Fantasy",
    "Horror",
    "Kids & Family",
    "Mystery",
    "Reality",
    "Romance",
    "Sci-Fi",
    "Sports",
    "Stand-Up",
    "Thriller",
)

#: Commercial plans.
#:
#: ``monthly_price_usd`` is the list price; the seeder derives actual MRR from it
#: after applying the billing-period discount, which is why annual subscribers
#: show a lower MRR but a higher LTV in the revenue queries.
SUBSCRIPTION_PLANS: Final[tuple[dict[str, Any], ...]] = (
    {
        "name": "Free (Ad-Supported)",
        "tier": "free",
        "monthly_price_usd": 0.00,
        "max_streams": 1,
        "has_ads": True,
    },
    {
        "name": "Mobile",
        "tier": "entry",
        "monthly_price_usd": 2.49,
        "max_streams": 1,
        "has_ads": True,
    },
    {
        "name": "Basic",
        "tier": "standard",
        "monthly_price_usd": 5.99,
        "max_streams": 1,
        "has_ads": False,
    },
    {
        "name": "Standard",
        "tier": "standard",
        "monthly_price_usd": 9.99,
        "max_streams": 2,
        "has_ads": False,
    },
    {
        "name": "Premium 4K",
        "tier": "premium",
        "monthly_price_usd": 15.99,
        "max_streams": 4,
        "has_ads": False,
    },
)


# ===========================================================================
# Schema
# ===========================================================================


def upgrade() -> None:
    """Create the dimension tables and insert their reference rows."""
    # -----------------------------------------------------------------------
    # core.countries
    # -----------------------------------------------------------------------
    countries = op.create_table(
        "countries",
        sa.Column("country_id", sa.SmallInteger, primary_key=True, autoincrement=True),
        sa.Column("iso_code", sa.CHAR(2), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("region", sa.String(32), nullable=False),
        sa.Column("tier", sa.SmallInteger, nullable=False),
        sa.UniqueConstraint("iso_code", name="uq_countries_iso_code"),
        sa.UniqueConstraint("name", name="uq_countries_name"),
        sa.CheckConstraint("tier BETWEEN 1 AND 3", name="ck_countries_tier_range"),
        sa.CheckConstraint("iso_code = upper(iso_code)", name="ck_countries_iso_upper"),
        schema="core",
        comment="Markets Vireo operates in. tier is a monetisation band: 1=high ARPU, 3=high volume.",
    )

    # -----------------------------------------------------------------------
    # core.devices
    # -----------------------------------------------------------------------
    devices = op.create_table(
        "devices",
        sa.Column("device_id", sa.SmallInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(48), nullable=False),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("form_factor", sa.String(16), nullable=False),
        sa.UniqueConstraint("name", name="uq_devices_name"),
        sa.CheckConstraint(
            "form_factor IN ('phone', 'tablet', 'desktop', 'tv', 'console')",
            name="ck_devices_form_factor",
        ),
        schema="core",
        comment="Playback surfaces. form_factor is the primary analytical grouping.",
    )

    # -----------------------------------------------------------------------
    # core.marketing_channels
    # -----------------------------------------------------------------------
    channels = op.create_table(
        "marketing_channels",
        sa.Column("channel_id", sa.SmallInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(48), nullable=False),
        sa.Column("channel_group", sa.String(24), nullable=False),
        sa.Column("is_paid", sa.Boolean, nullable=False),
        sa.Column("cac_usd", sa.Numeric(8, 2), nullable=False),
        sa.UniqueConstraint("name", name="uq_channels_name"),
        sa.CheckConstraint("cac_usd >= 0", name="ck_channels_cac_non_negative"),
        # An unpaid channel with a non-zero acquisition cost is a data-entry
        # error, not a business model. Email is the one legitimate exception
        # (owned media has a real send cost), so it is excluded explicitly.
        sa.CheckConstraint(
            "is_paid OR cac_usd = 0 OR name = 'Email'",
            name="ck_channels_unpaid_has_no_cac",
        ),
        schema="core",
        comment="Acquisition sources with blended CAC. Drives LTV:CAC and payback analysis.",
    )

    # -----------------------------------------------------------------------
    # core.personas
    # -----------------------------------------------------------------------
    personas = op.create_table(
        "personas",
        sa.Column("persona_id", sa.SmallInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("base_sessions_per_week", sa.Numeric(4, 2), nullable=False),
        sa.Column("base_completion_rate", sa.Numeric(4, 3), nullable=False),
        sa.Column("base_churn_propensity", sa.Numeric(4, 3), nullable=False),
        sa.UniqueConstraint("name", name="uq_personas_name"),
        sa.CheckConstraint("base_sessions_per_week > 0", name="ck_personas_sessions_positive"),
        sa.CheckConstraint(
            "base_completion_rate BETWEEN 0 AND 1",
            name="ck_personas_completion_is_rate",
        ),
        sa.CheckConstraint(
            "base_churn_propensity BETWEEN 0 AND 1",
            name="ck_personas_churn_is_rate",
        ),
        schema="core",
        comment=(
            "Behavioural archetypes. These coefficients are simulation inputs; "
            "the analytics layer recovers them independently, which is what makes "
            "the dashboard's findings real rather than decorative."
        ),
    )

    # -----------------------------------------------------------------------
    # core.genres
    # -----------------------------------------------------------------------
    genres = op.create_table(
        "genres",
        sa.Column("genre_id", sa.SmallInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(32), nullable=False),
        sa.UniqueConstraint("name", name="uq_genres_name"),
        schema="core",
        comment="Catalogue genres. A table, not an enum: the business adds genres without a deploy.",
    )

    # -----------------------------------------------------------------------
    # core.subscription_plans
    # -----------------------------------------------------------------------
    plans = op.create_table(
        "subscription_plans",
        sa.Column("plan_id", sa.SmallInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("tier", sa.String(16), nullable=False),
        sa.Column("monthly_price_usd", sa.Numeric(6, 2), nullable=False),
        sa.Column("max_streams", sa.SmallInteger, nullable=False),
        sa.Column("has_ads", sa.Boolean, nullable=False),
        sa.UniqueConstraint("name", name="uq_plans_name"),
        sa.CheckConstraint("monthly_price_usd >= 0", name="ck_plans_price_non_negative"),
        sa.CheckConstraint("max_streams BETWEEN 1 AND 8", name="ck_plans_streams_range"),
        sa.CheckConstraint(
            "tier IN ('free', 'entry', 'standard', 'premium')",
            name="ck_plans_tier",
        ),
        # A paid plan must cost money and a free plan must not. Catches the
        # classic pricing-migration mistake of renaming a tier but not its price.
        sa.CheckConstraint(
            "(tier = 'free') = (monthly_price_usd = 0)",
            name="ck_plans_free_tier_is_free",
        ),
        schema="core",
        comment="Commercial plans. List price; actual MRR applies the billing-period discount.",
    )

    # -----------------------------------------------------------------------
    # Reference rows
    #
    # bulk_insert against the table objects returned by create_table keeps the
    # values parameter-bound and type-checked rather than string-interpolated.
    # -----------------------------------------------------------------------
    op.bulk_insert(countries, [dict(row) for row in COUNTRIES])
    op.bulk_insert(devices, [dict(row) for row in DEVICES])
    op.bulk_insert(channels, [dict(row) for row in MARKETING_CHANNELS])
    op.bulk_insert(personas, [dict(row) for row in PERSONAS])
    op.bulk_insert(genres, [{"name": name} for name in GENRES])
    op.bulk_insert(plans, [dict(row) for row in SUBSCRIPTION_PLANS])

    # -----------------------------------------------------------------------
    # Column comments worth carrying into the ER diagram and data dictionary
    # -----------------------------------------------------------------------
    op.execute(
        "COMMENT ON COLUMN core.marketing_channels.cac_usd IS "
        "'Blended customer acquisition cost in USD. Zero for organic channels.'"
    )
    op.execute(
        "COMMENT ON COLUMN core.personas.base_churn_propensity IS "
        "'Monthly churn hazard before per-user perturbation and tenure decay.'"
    )
    op.execute(
        "COMMENT ON COLUMN core.countries.tier IS "
        "'Monetisation band. 1=high ARPU/high CAC, 2=mid, 3=high volume/low ARPU.'"
    )


def downgrade() -> None:
    """Drop the dimension tables in reverse dependency order."""
    for table in (
        "subscription_plans",
        "genres",
        "personas",
        "marketing_channels",
        "devices",
        "countries",
    ):
        op.drop_table(table, schema="core")

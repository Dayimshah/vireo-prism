# Data model

Two schemas. `core` holds the simulated truth — what happened, at the grain it
happened. `analytics` holds four materialized views that pre-aggregate the
expensive joins, and nothing else: no dimension copies, no summary tables, one
function to refresh them.

Everything below was read from the running database rather than from the
migrations, so it describes what is actually there.

## Entity relationships

```mermaid
erDiagram
    countries          ||--o{ users        : "signed up from"
    marketing_channels ||--o{ users        : "acquired via"
    personas           ||--o{ users        : "behaves as"
    devices            ||--o{ users        : "signed up on"
    devices            ||--o{ sessions     : "used for"
    genres             ||--o{ content      : "categorises"
    subscription_plans ||--o{ subscriptions: "priced by"

    users ||--o{ sessions               : "starts"
    users ||--o{ events                 : "emits"
    users ||--o{ subscriptions          : "holds"
    users ||--o{ experiment_assignments : "enrolled in"

    sessions    ||--o{ events : "contains"
    content     ||--o{ events : "watched in"
    experiments ||--o{ experiment_assignments : "assigns"

    users {
        bigint   user_id PK
        date     signup_date
        smallint country_id FK
        smallint device_id FK
        smallint channel_id FK
        smallint persona_id FK
        boolean  is_premium
        smallint age
        string   gender
        string   app_version
        timestamp last_seen_at "nullable"
        date     churned_at "nullable"
    }

    sessions {
        bigint    session_id PK
        bigint    user_id FK
        smallint  device_id FK
        timestamp session_start
        timestamp session_end
        int       duration_seconds
        smallint  event_count
        int       watch_seconds
        boolean   is_first_session
        string    entry_screen
        string    exit_screen
    }

    events {
        bigint    event_id PK
        bigint    session_id FK
        bigint    user_id FK
        bigint    content_id FK "nullable"
        timestamp event_time "partition key"
        enum      event_name
        string    screen
        smallint  step_index
        int       watch_seconds "nullable"
        numeric   progress_pct "nullable"
        jsonb     properties
    }

    content {
        bigint   content_id PK
        string   title
        smallint genre_id FK
        enum     content_type
        smallint runtime_minutes
        smallint release_year
        string   language
        string   age_rating
        numeric  popularity_score
        smallint season_count "nullable"
        smallint episode_count "nullable"
        boolean  is_original
        date     added_on
    }

    subscriptions {
        bigint   subscription_id PK
        bigint   user_id FK
        smallint plan_id FK
        date     started_on
        date     ended_on "nullable"
        enum     status
        enum     billing_period
        numeric  mrr_usd
        string   cancel_reason "nullable"
        boolean  is_trial_conversion
    }

    experiments {
        int     experiment_id PK
        string  key UK
        string  name
        text    hypothesis
        string  primary_metric
        jsonb   variants
        numeric traffic_allocation
        date    started_on
        date    ended_on "nullable"
        enum    status
    }

    experiment_assignments {
        int       experiment_id FK
        bigint    user_id FK
        string    variant
        timestamp assigned_at
    }

    personas {
        smallint persona_id PK
        string   name
        text     description
        numeric  base_sessions_per_week
        numeric  base_completion_rate
        numeric  base_churn_propensity
    }

    marketing_channels {
        smallint channel_id PK
        string   name
        string   channel_group
        boolean  is_paid
        numeric  cac_usd
    }

    countries {
        smallint country_id PK
        char     iso_code
        string   name
        string   region
        smallint tier
    }

    devices {
        smallint device_id PK
        string   name
        string   platform
        string   form_factor
    }

    genres {
        smallint genre_id PK
        string   name
    }

    subscription_plans {
        smallint plan_id PK
        string   name
        string   tier
        numeric  monthly_price_usd
        smallint max_streams
        boolean  has_ads
    }
```

Thirteen tables: six dimensions (`countries`, `devices`, `genres`,
`marketing_channels`, `personas`, `subscription_plans`), six facts (`users`,
`sessions`, `events`, `subscriptions`, `experiments`, `experiment_assignments`),
and `content`, which behaves as both.

## Row counts at the `small` profile

| Table | Rows |
| --- | --- |
| `events` | 1,092,554 |
| `sessions` | 52,798 |
| `users` | 600 |
| `experiment_assignments` | 355 |
| `content` | 320 |
| `subscriptions` | 108 |

Three profiles exist: `small` (600 users), `medium` (4,000), `large` (15,000).
Event volume scales roughly linearly with users.

## Enumerated types

Five, all PostgreSQL native enums rather than lookup tables — the value sets are
fixed by the simulation and a join to read a label would be pure cost.

| Type | Values |
| --- | --- |
| `event_name` | `OPEN_APP`, `HOME`, `BROWSE_GENRE`, `SEARCH`, `VIEW_CONTENT`, `WATCH_TRAILER`, `START_VIDEO`, `VIDEO_PROGRESS`, `PAUSE_VIDEO`, `ABANDON_VIDEO`, `COMPLETE_VIDEO`, `ADD_TO_WATCHLIST`, `RATE`, `SUBSCRIBE_CLICK`, `EXIT` |
| `content_type` | `movie`, `series`, `documentary`, `stand_up` |
| `sub_status` | `trialing`, `active`, `paused`, `cancelled`, `expired` |
| `billing_period` | `monthly`, `quarterly`, `annual` |
| `exp_status` | `running`, `completed`, `stopped` |

The `event_name` ordering is not alphabetical and not arbitrary: it follows the
funnel, so `step_index` and the enum sort order agree and a funnel query can rank
steps without a `CASE`.

## Partitioning

`core.events` is `RANGE`-partitioned on `event_time`, one partition per month,
**65** partitions covering 2021-08 onward, plus an `events_default` catch-all.

Monthly rather than daily: 65 relations is a manageable planning cost, while daily
partitioning over the same span would be ~2,000 and the planner time starts to
show on queries that cannot prune. Every analytical query filters on a date range,
so pruning is the common case.

The catch-all exists so an insert outside the declared range fails a data-quality
check rather than the transaction. It should be empty; if it is not, the generator
produced an event outside its own window.

## The analytics layer

Four materialized views, no ordinary views, one function.

| View | Grain | Columns | What it is for |
| --- | --- | --- | --- |
| `mv_user_daily` | user × day | 14 | The date spine every DAU/WAU/MAU, retention and stickiness query derives from |
| `mv_user_lifetime` | user | 31 | RFM, churn scoring, LTV — anything that needs a user's whole history in one row |
| `mv_funnel_steps` | session | 23 | Per-session booleans and first-touch timestamps for each funnel step |
| `mv_content_daily` | content × day | 13 | Content performance, completion rates, shelf-life decay |

`mv_funnel_steps` is the one that most repays the cost: computing twelve
"did this session ever do X" booleans from 1.1M events with a `bool_or` per step
is expensive once and free thereafter. The `ts_first_*` columns exist so
time-between-steps is a subtraction rather than a self-join over the event table.

### Refreshing

```sql
SELECT analytics.refresh_all(concurrent => true);
```

Or `make refresh`, or `POST /api/v1/admin/refresh-analytics` with the admin key.

`concurrent => true` uses `REFRESH MATERIALIZED VIEW CONCURRENTLY`, which does not
block readers — but it **cannot run inside a transaction block**, which is why the
admin endpoint reaches for an autocommit connection rather than a session. It also
requires each view to carry a unique index; they all do, and that is what makes
concurrent refresh possible at all.

The seeder refreshes on exit unless given `--no-refresh`. A view created
`WITH NO DATA` raises on any read, so a database migrated but never seeded returns
errors rather than empty results — `GET /api/v1/meta/bounds` reports
`analytics_ready` so a client can tell the two apart before it asks for a chart.

## Nullability is meaningful

Nullable columns are nullable because the fact is genuinely absent, not because
the value is unknown-and-probably-zero:

- `events.content_id` — a `SEARCH` or `HOME` event has no content attached.
- `events.watch_seconds`, `events.progress_pct` — only playback events measure these.
- `users.churned_at` — null means still active. Not a date far in the future.
- `users.last_seen_at` — null means a user who signed up and never returned.
- `subscriptions.ended_on` — null means currently running.
- `subscriptions.cancel_reason` — null unless cancelled.
- `content.season_count`, `content.episode_count` — null for a film.

That distinction propagates all the way to the UI: the API returns `null`, and the
dashboard renders an em-dash rather than a zero. A missing figure and a measured
zero are different findings, and the second is a claim about the data.

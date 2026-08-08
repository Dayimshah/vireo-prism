# Power BI

An optional star-schema projection of the warehouse, for reading the same data in
Power BI instead of the React dashboard.

**There is no `.pbix` in this repository, deliberately.** A `.pbix` is an opaque
binary: it cannot be reviewed in a diff, it pins credentials and a refresh history
into a blob, and it goes stale the moment the schema moves. What is here instead is
the part that carries the thinking — the star schema as reviewable SQL and the
measures as reviewable DAX. Building the report from them takes a few minutes and
you can see every decision that went into it.

Nothing in the API or the dashboard reads any of this. It is additive, and
`DROP SCHEMA powerbi CASCADE` removes it completely.

## Build the schema

```bash
make powerbi
```

Or directly:

```bash
docker compose exec -T postgres \
  psql -U prism -d vireo -v ON_ERROR_STOP=1 -q < powerbi/01_star_schema.sql
```

The script drops and recreates the `powerbi` schema, so it is safe to re-run after
a reseed. Verified: 14 relations, one table and thirteen views.

It is **not** an Alembic migration, and that is on purpose. `core` and `analytics`
are the schema the API, the 51 delivered queries and the 330-test suite were all
verified against; adding relations to them for a BI tool's benefit would change the
surface every one of those depends on.

## Connect

| Setting | Value |
| --- | --- |
| Server | `127.0.0.1,5433` |
| Database | `vireo` |
| Data Connectivity mode | **Import** |
| User | `prism` (or `POSTGRES_USER`) |
| Password | `POSTGRES_PASSWORD` — from `.env`, never committed |

**The port is 5433, not 5432.** `docker-compose.yml` publishes
`127.0.0.1:${POSTGRES_HOST_PORT:-5433}:5432` so the container does not collide with
a Postgres already running on the host. Note the comma in Power BI's server field —
it wants `host,port`, not `host:port`.

It is also bound to `127.0.0.1`, so Power BI must run on the same machine as the
container. That is deliberate: an unauthenticated analytics database should not be
listening on a LAN interface.

Import rather than DirectQuery. The dataset is static between reseeds, so
DirectQuery would send a query per visual interaction for data that never changes,
and the two slowest analytical queries already run at ~1 s. Import once and the
model is instant.

Select the `powerbi` schema only. Loading `core` and `analytics` alongside it gives
Power BI two copies of every dimension and an auto-detected relationship maze.

## Model relationships

Power BI's auto-detection gets most of these right and some of them wrong, so set
them explicitly. All are **one-to-many, single direction**, from the dimension to
the fact.

| From | To | Active |
| --- | --- | --- |
| `dim_date[date_key]` | `fact_user_daily[date_key]` | yes |
| `dim_date[date_key]` | `fact_content_daily[date_key]` | yes |
| `dim_date[date_key]` | `fact_session_funnel[date_key]` | yes |
| `dim_date[date_key]` | `fact_subscription[date_key]` | yes |
| `dim_date[date_key]` | `fact_experiment_assignment[date_key]` | yes |
| `dim_date[date_key]` | `fact_subscription[ended_on]` | **no — inactive** |
| `dim_user[user_key]` | `fact_user_daily[user_key]` | yes |
| `dim_user[user_key]` | `fact_session_funnel[user_key]` | yes |
| `dim_user[user_key]` | `fact_subscription[user_key]` | yes |
| `dim_user[user_key]` | `fact_experiment_assignment[user_key]` | yes |
| `dim_content[content_key]` | `fact_content_daily[content_key]` | yes |
| `dim_country[country_key]` | `dim_user[country_key]` | yes |
| `dim_channel[channel_key]` | `dim_user[channel_key]` | yes |
| `dim_persona[persona_key]` | `dim_user[persona_key]` | yes |
| `dim_device[device_key]` | `dim_user[device_key]` | yes |
| `dim_device[device_key]` | `fact_session_funnel[device_key]` | yes |
| `dim_genre[genre_key]` | `dim_content[genre_key]` | yes |
| `dim_plan[plan_key]` | `fact_subscription[plan_key]` | yes |

**The inactive relationship is required, not optional.** `fact_subscription` is
dated by `started_on` on its active relationship, so `Cancelled Subscriptions` and
`Cancelled MRR` use `USERELATIONSHIP` to switch to `ended_on`. Without the second
relationship those two measures error — which is the right failure, because both
alternatives return a plausible number for the wrong question: filtering on
`ended_on` with the active relationship live counts contracts that *started* in the
period and ended at any time ever, and `REMOVEFILTERS(dim_date)` ignores the
slicer entirely while still being named "in period".

Keep every relationship single-direction. Bidirectional filtering on a star schema
this size creates ambiguous paths, and Power BI resolves ambiguity silently.

### Mark the date table

Table tools → **Mark as date table** → `dim_date[date_key]`.

Skipping this does not produce an error. It produces wrong windows: `DATESINPERIOD`
and `DATEADD` fall back to Power BI's auto date hierarchy and quietly return the
wrong period, so `WAU`, `MAU` and every period-over-period measure are wrong in a
way no visual reveals.

Then set the sort-by columns, or every axis sorts alphabetically and April comes
before January:

| Column | Sort by |
| --- | --- |
| `month_name` | `month_number` |
| `month_short` | `month_number` |
| `year_month` | `year_month_sort` |
| `iso_year_week` | `year_week_sort` |
| `weekday_short` | `iso_weekday` |

`year_week_sort` is built from `EXTRACT(isoyear ...)`, not the calendar year. ISO
week 1 of 2026 begins on 2025-12-29, so pairing the calendar year with the ISO week
would give that day `202501` — sorting it before 2025's week 52 and putting the
year boundary in the wrong place on every weekly axis. Verified at the boundary:
2025-12-28 → `202552`, 2025-12-29 → `202601`.

## Add the measures

`powerbi/02_measures.dax` holds about 70 measures grouped by home table. Each
section header names the table to create them on. Paste them one at a time — Power
BI Desktop has no bulk measure import.

Two conventions carried over from the rest of the project, and both matter more
than they look.

### Percentages are percentage points

Every `... %` measure returns `51.1` to mean 51.1%, matching the API's `_pct`
columns. Format with a fixed decimal and a literal `"%"` suffix — **not** Power
BI's percentage format, which multiplies by 100 a second time and reports 5,110%.

The single source column using a 0–1 fraction (`mv_user_lifetime.completion_rate`)
is already restated as `completion_rate_pct` in `dim_user`, so the model has one
convention throughout rather than two.

### A blank is an undefined figure, never zero

Every ratio uses `DIVIDE`, which returns `BLANK` on a zero denominator. That is the
wanted behaviour: a day with no sessions has no average session length, and
plotting it as 0 asserts a measurement nobody made.

Do not wrap these in `IFERROR(..., 0)` or `COALESCE(..., 0)`. It looks tidier and
it breaks the distinction the entire stack maintains — the SQL returns `NULL`, the
API serialises `null`, the dashboard renders an em-dash. For a line chart, leave
blanks as gaps rather than filling them; a gap is a gap, not a dip to zero.

The MRR waterfall is the one documented exception, where a null movement genuinely
means "no revenue moved that way". See `analytics-catalog.md`.

### Two measures that are deliberately absent

**No `Current MRR MoM %`.** `Current MRR` sums `dim_user`, and filters in a star
schema flow one-to-many: `dim_date` filters the facts but nothing propagates back
up into `dim_user`. `DATEADD` over it returns the same figure every period, so the
measure would read 0% forever — wrong in the worst way, because it looks like the
finding "MRR is flat" rather than like a broken measure. `Current MRR` is a
population snapshot; for MRR over time use the `fact_subscription` measures, which
are dated by contract start and end.

**No significance testing.** `fact_experiment_assignment` carries exposure counts
and no verdict. The two-proportion z-test, Wilson intervals and observed power live
in `app/services/stats.py`, unit-tested against closed forms. A DAX
reimplementation would drift from that one, and the drift would surface as two
dashboards disagreeing about whether a test won. Call
`GET /api/v1/experiments/{key}/results` for significance.

`Smallest Arm` and `Arm Meets Minimum` are there instead, because at the `small`
profile the arm size is usually the whole explanation: every arm is 12–65 users
against `MIN_ARM_SIZE = 30`.

## Verify the load

Row counts should match the source exactly — every view is an unfiltered
projection. Measured after a `small` seed:

| Relation | Rows | Source |
| --- | --- | --- |
| `dim_user` | 600 | `core.users` |
| `dim_content` | 320 | `core.content` |
| `dim_date` | 730 | generated, 2025-01-01 → 2026-12-31 |
| `fact_user_daily` | 33,503 | `mv_user_daily` |
| `fact_content_daily` | 43,331 | `mv_content_daily` |
| `fact_session_funnel` | 52,798 | `mv_funnel_steps` |
| `fact_subscription` | 108 | `core.subscriptions` |
| `fact_experiment_assignment` | 355 | `core.experiment_assignments` |

`dim_date` is padded to whole years, so it spans 730 contiguous days against 546
days of actual activity. That is intentional — a year-over-year visual needs
complete endpoints, and a spine with gaps makes time intelligence miscompute
silently rather than fail.

A sanity check worth running once the model loads: `DAU` on the last day of the
window should agree with
`GET /api/v1/kpi/dau?date_from=...&date_to=...`. If it does not, the date table is
almost certainly unmarked.

## Reading the numbers honestly

Two things a Power BI reader should know before treating a chart as a finding, both
measured and both properties of the `small` profile rather than of the model:

- **Seven personas, not eight.** `New Explorer` is transient — users graduate into
  another persona after 30 days, and the stored `persona_id` records where they
  ended up. A missing New Explorer bar is correct.
- **Channel ranking is directional, not a ranking.** Spearman ρ between the planted
  channel coefficients and observed conversion is 0.71 across twelve channels; the
  smallest channel has 7 users, one coin flip from looking like the best in the
  dataset. Read `LTV to CAC` as a quadrant, not a league table.

Full arithmetic for both in `analytics-catalog.md`.

## Refreshing after a reseed

```bash
make seed-small     # or make seed / make seed-large
make refresh        # rebuild the analytics matviews
make powerbi        # rebuild the star schema
```

Then refresh in Power BI Desktop. The views are plain views rather than
materialized ones, so `make refresh` is immediately visible to the next Power BI
refresh with no second rebuild to forget. `dim_date` is a table and *is* rebuilt by
`make powerbi`, which matters if the new window extends past the old spine.

## Removing it

```sql
DROP SCHEMA powerbi CASCADE;
```

Nothing else in the project references it.

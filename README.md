# Prism — product analytics for Vireo

A read-only analytics platform over a simulated streaming service: PostgreSQL
warehouse, FastAPI serving 55 operations, React dashboard of eleven pages, and a
generator that produces 1.1M events of behaviour with **planted causal structure**
the analytics layer has to rediscover.

The last part is the point. The seeder declares that Referral brings users who pay
and Display does not, that a Binge Watcher's churn hazard is a tenth of a Churn
Risk's, that watch time drives conversion. None of the 51 SQL queries knows any of
it — they aggregate events. When the Marketing page shows Referral outperforming
Display, that is recovery, not a lookup.

```
React 18 · Vite · Recharts          11 pages · 64 TS modules
        │  GET /api/v1/...
FastAPI (:8010)                     55 operations · 4 layers
        │  asyncpg
PostgreSQL 16                       13 tables · 4 matviews · 65 partitions
```

## Quickstart

Docker and Docker Compose are the only prerequisites.

```bash
make env          # create .env from .env.example
make up           # build, start, apply migrations
make seed-small   # 600 users, ~1.1M events, ~2.5 min
```

Then:

| | |
| --- | --- |
| Dashboard | <http://localhost:5173> |
| API docs | <http://localhost:8010/docs> |
| Health | <http://localhost:8010/api/v1/health> |

`make help` lists every target.

**The API is on 8010, not 8000**, and Postgres on **5433**, not 5432 — both to
avoid colliding with something already running on the host. Postgres and Redis bind
to `127.0.0.1` only.

### First API call

The window parameters have **no defaults**, deliberately: a "last 30 days" default
would open every chart empty on a repository cloned months after its dataset was
generated, and an empty chart reads as a broken service. So ask what the data
covers first.

```bash
curl -s http://localhost:8010/api/v1/meta/bounds | jq .data
```

```json
{
  "first_activity_date": "2025-02-07",
  "last_activity_date": "2026-08-06",
  "days": 546,
  "users": 600,
  "events": 1092554,
  "is_seeded": true
}
```

Then pick a window inside those bounds:

```bash
curl -s "http://localhost:8010/api/v1/kpi/dau?date_from=2026-07-01&date_to=2026-08-06" | jq
```

## What is in here

| | |
| --- | --- |
| **Warehouse** | 13 tables in `core`, 4 materialized views in `analytics`, 5 native enums, `events` range-partitioned monthly across 65 partitions (311 MB seeded) |
| **API** | 55 operations — 54 GET, 1 POST. `{data, meta}` envelope, RFC 7807 errors, cache-aside with Redis or an in-process LRU, token-bucket rate limiting, 8 uniform filters |
| **SQL** | 51 `.sql` files across 15 namespaces plus 3 spliced fragments. No ORM in the read path — window functions, `PERCENTILE_CONT` and lateral joins are the whole point |
| **Dashboard** | 11 pages, 64 TypeScript modules, Recharts, TanStack Query, light/dark |
| **Seeder** | Per-persona Markov navigation with atomic playback blocks, local-time-to-UTC timestamps, planted coefficients, three scale profiles |
| **Tests** | 330 — 167 unit (no database) and 163 integration (live seeded Postgres) |
| **Power BI** | Optional star-schema projection plus ~70 DAX measures |

Stack: Python 3.12 · FastAPI 0.115 · PostgreSQL 16.6 · SQLAlchemy 2.0 · asyncpg +
psycopg3 · Redis 7 (optional) · React 18.3 · TypeScript 5.6 · Vite 5.4 · Tailwind
3.4

## Documentation

Written from live probes against a running database rather than from the
migrations, so each describes what is actually there.

| Document | What it covers |
| --- | --- |
| [architecture.md](docs/architecture.md) | Layers, caching, middleware, the envelope, why SQL lives in files |
| [data-model.md](docs/data-model.md) | Mermaid ER diagram, partitioning, the analytics layer, why nullability is meaningful |
| [api.md](docs/api.md) | Every route, the filter surface, the window asymmetry, errors, rate limits |
| [analytics-catalog.md](docs/analytics-catalog.md) | Statistics, percentage conventions, and what the seeded data can and cannot show |
| [decisions.md](docs/decisions.md) | Eleven trade-offs with what each one cost |
| [seeder-design.md](docs/seeder-design.md) | The generation model, journey invariants, determinism |
| [powerbi.md](docs/powerbi.md) | Star schema, model relationships, DAX conventions |

## Three things that will bite you

**A `null` is an undefined figure, never zero.** The SQL returns `NULL`, the API
serialises `null`, the dashboard renders an em-dash, and line charts leave gaps
(`connectNulls={false}`). A missing figure and a measured zero are different
findings. The MRR waterfall is the single documented exception.

**Three percentage conventions.** Columns ending `_pct` are pre-multiplied (`51.1`
means 51.1%). `completion_rate` and `avg_completion_rate` are 0–1 fractions. So are
`traffic_allocation` and `observed_power`. Mixing them is a factor-of-100 bug.

**An undeclared query parameter is a 422, not ignored.** Silently dropping
`date_form` would return a cheerful 200 for the wrong window. The consequence is
that the window surface is not uniform: 34 routes take `date_from`+`date_to`, 10 add
`observation_end`, 7 take no window, `/geo/country-ranking` takes `date_to` only,
and the two experiment routes take `observation_end` only.

## Development

```bash
make test         # 330 tests
make check        # lint + types + tests
make web-build    # type-check and build the frontend
make report       # data-quality report: 12 invariant checks + 8 charts
make fresh        # nuke, rebuild, reseed
```

Backend tests need a seeded database and opt in with `PRISM_TEST_DB=1`. Any truthy
non-DSN value means "use the database this process is already configured for", so
no credential is restated on a command line.

### What the gates actually claim

Verified at the close of phase 12:

| Gate | Result |
| --- | --- |
| `pytest` | **330 passed** in 116 s, also under the CI environment with Redis off |
| `tsc --build` | exit 0 across all 64 modules |
| `eslint` | exit 0, 7 documented `react-refresh` warnings |
| `vite build` | exit 0 in 8.1 s |
| `ruff check .` | ~100 findings, all in files delivered in phases 1–6 |
| `mypy` | 15 findings, same |
| `prettier --check` | 34 files, pure line-wrapping |

The last three are reported by a non-blocking `advisory` CI job rather than either
fixed or deleted. Fixing them means a large mechanical diff across working,
reviewed code; deleting the gates would hide the debt behind a green badge. **A
green tick means the tests pass, the new code is clean, and the frontend builds. It
does not mean the tree is lint-clean.**

TypeScript must be compiled with `tsc --build`, never a bare `tsc --noEmit`: the
root `tsconfig.json` is `{"files": [], "references": [...]}` and a bare invocation
does not follow project references — it checks zero files and exits 0. That
produced a run of vacuous green results while two real type errors sat in the tree.

## Honest limitations

Measured, not guessed. All are properties of the `small` profile or of documented
design choices, and none is a bug hiding behind a caveat.

- **Every A/B verdict at `small` is `underpowered` or `inconclusive`.** Arms hold
  12–65 users against `MIN_ARM_SIZE = 30`; detecting the planted 4–8-point lifts
  needs 268–986 per arm. Two of four come out with the wrong sign. Seed `medium` or
  `large` to see the effects recovered — the arithmetic is in
  [analytics-catalog.md](docs/analytics-catalog.md).
- **`day7_retention` reads 0% in all three arms** of
  `onboarding-genre-picker`. Two individually correct decisions interact badly: the
  metric query counts only post-assignment activity (right, for a causal
  comparison) while the seeder assigns users 0–315 days after signup, so a user
  enrolled on day 9 has their day-7 row filtered out. Only 4 of 169 assigned users
  have day 7 on or after assignment. Recorded rather than patched, because the fix
  is a design decision rather than a tweak.
- **Only 4 of 8 declared experiments seed at `small`** (6 at `medium`, 8 at
  `large`), and because the slice is in declaration order, the deliberately-null and
  deliberately-negative specs are the ones that never appear. A `loser` verdict is
  unreachable below `large`.
- **Seven personas, not eight.** `New Explorer` is transient by design — users
  graduate into another persona after 30 days and the stored `persona_id` records
  where they ended up. A missing New Explorer bar is correct.
- **Channel ranking is directional, not a league table.** Spearman ρ between the
  planted coefficients and observed conversion is 0.71 across twelve channels; the
  extremes recover, the middle is noise, and the smallest channel has 7 users.
- **Rate limiting is per worker, not per cluster.** Under `--workers N` the
  effective limit is N times the configured one. It bounds accidental load, not a
  distributed attacker.
- **Read endpoints are unauthenticated.** Deliberate: every row is synthetic, and a
  login wall would stop the reader this project exists for. Only
  `POST /admin/refresh-analytics` is protected, because refreshing 1.1M events is
  expensive enough to be a denial-of-service lever.
- **Measured session count runs 23% below the estimate** in `config.py`
  (52,798 against ~69,000). Events land within 4%. The scale claim holds; the
  session estimate is optimistic.

## Repository layout

```
backend/
  app/            FastAPI: routers → services → repositories → sql/
  seeder/         dataset generator + data-quality report
  alembic/        migrations
  tests/          167 unit · 163 integration
frontend/         React dashboard, 11 pages
docs/             7 documents
powerbi/          star-schema SQL + DAX measures
.github/          CI
```

Vireo is a fictional streaming service. Every user, title, session and event is
generated; no row describes a real person and no title is a real film or series.

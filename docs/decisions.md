# Decisions

A register of choices that cost something, with what they cost. Six places in the
delivered code point here — `alembic/versions/0001`, `app/core/cache.py`,
`app/core/exceptions.py`, `app/core/security.py`, `seeder/loaders.py` and
`seeder/seasonality.py` — and each of those citations is answered below.

`architecture.md` describes what the system does. This file records why it does it
that way and what was given up, so a reviewer does not have to infer a rationale
or assume an oversight.

---

## 1. Read endpoints have no authentication

*Cited from `app/core/security.py` and `app/core/exceptions.py`.*

**Context.** Prism is a portfolio analytics service over entirely synthetic data.
Its purpose is that someone can open the dashboard and click through eleven pages
of analysis.

**Decision.** All 54 GET routes are open. Exactly one operation is protected —
`POST /api/v1/admin/refresh-analytics` — behind an `X-API-Key` header compared
with `hmac.compare_digest`.

**Why the one endpoint is protected.** Not on principle. `REFRESH MATERIALIZED
VIEW` over 1.1M events takes seconds of server time, so an open endpoint would
also be a denial-of-service lever.

**Consequence.** Anyone who reaches the host can read every chart. Since every row
is generated and no row describes a real person, the disclosure risk is zero and
the access friction would be total: a login wall would stop the exact reader the
project exists for.

**What would change this.** Real user data. At that point the trade inverts
completely, and nothing about the current design should be read as a
recommendation for that case.

---

## 2. `LocalCache` stores live Python objects

*Cited from `app/core/cache.py`.*

**Context.** Redis is optional. When it is unreachable the service needs a cache
that still works, and it must not change the *types* a caller receives.

**Decision.** A bounded in-process LRU holding live Python objects rather than
serialised bytes, with per-entry TTL. It is faster than Redis for a single process
and useless across several.

**Consequence.** Under `--workers N` there are N independent caches, so the same
request can miss N times before every worker is warm. Accepted for a fallback:
the alternative is requiring Redis, which would make one `docker compose up`
insufficient to run the project.

**The part that needed real work.** The two backends do not behave alike.
`RedisCache` serialises with `json.dumps(..., default=str)`, so a `Decimal`
returns as a string; `LocalCache` returns the `Decimal`. Left alone, every
repository's type guarantee would hold on a miss and break on a hit — and hold
locally while breaking under Redis, which is the worst possible failure
distribution.

`app/core/cache.py` is frozen, so `services/base.py` wraps both paths in a tagged
codec: `Decimal("12.34")` encodes as `{"__t": "dec", "v": "12.34"}`. It runs when
encoding a **freshly computed** result too, which looks redundant and is the
point — a codec bug surfaces on the first request rather than on the first cache
hit, and no caller can come to depend on richer types that only a miss returns.

`LocalCache` is also deliberately not thread-safe. FastAPI runs handlers on one
event loop; a lock around a dict operation would cost more than it protects.

---

## 3. `COPY` in text format, not binary

*Cited from `seeder/loaders.py`.*

**Context.** The seeder loads ~1.1M events. The original architecture specified
binary `COPY` for throughput.

**Decision.** Text format. The architecture was wrong for this schema.

**Why.** Binary requires the client to send a type OID for every column, and five
columns are PostgreSQL enums (`event_name`, `content_type`, `sub_status`,
`billing_period`, `exp_status`) whose OIDs are assigned at migration time and
differ between databases. Making binary work would mean querying `pg_type` at
startup and registering five custom dumpers — real, fragile complexity for a few
per cent. Text sends each value as a literal for the server to parse, which
handles enums natively.

**Consequence.** Measurably slower per row, immaterial at this scale: ~150 s for
the whole `small` profile, of which loading is a minority. The code is
substantially harder to get subtly wrong, and a wrong OID is a corruption bug
rather than an error.

**Related trap, same file.** Most tables load with explicit surrogate keys, which
leaves their `BIGSERIAL` sequences at zero and makes any later `INSERT` collide on
the primary key. `reset_sequences` advances each past the loaded maximum.
`core.events` is the exception — its `event_id` comes from the sequence, so there
is nothing to reset.

---

## 4. Timestamps are generated in local time and stored in UTC

*Cited from `seeder/seasonality.py`.*

**Context.** People watch television in *their* evening. A Mumbai user peaks at
21:00 IST, which is 15:30 UTC; a Los Angeles user peaks at 21:00 PDT, which is
04:00 UTC the following day.

**Decision.** Generate every timestamp in the user's local time, then convert to
UTC for storage.

**Why.** Drawing the peak directly in UTC is one line shorter and produces a
dataset where every country on earth watches at the same instant. That artefact is
immediately visible on an hour-of-day heatmap and is the single most common tell in
a synthetic clickstream.

**Measured, `small` profile** — peak UTC hour of session starts, by country:

| Country | Offset | Peak UTC hour | Local time at peak |
| --- | --- | --- | --- |
| Brazil | −3 | 00 | 21:00 |
| United States | −6 | 03 | 21:00 |
| Australia | +10 | 10 | 20:00 |
| Japan | +9 | 11 | 20:00 |
| India | +5.5 | 15 | 20:30 |
| France | +1 | 19 | 20:00 |
| Germany | +1 | 20 | 21:00 |
| United Kingdom | 0 | 21 | 21:00 |

The peak spans the entire clock in UTC and collapses onto 20:00–21:00 local. That
is the effect working.

**Consequence, and it is a real one.** `analytics.mv_user_daily` buckets by UTC
date while users behave on local dates, so a late-night session in India lands on
the following UTC day. The US row above shows it plainly: local evening viewing is
recorded on the *next* UTC date. Every DAU/retention figure inherits that
boundary.

This is exactly what a real warehouse does, and it is recorded rather than
papered over. Bucketing by local date would require carrying an offset into every
aggregate and would make cross-country daily totals not sum.

**Fixed offsets, not IANA zones** (`docs/seeder-design.md`). `zoneinfo` would make
the dataset depend on the tzdata version installed on the generating machine, and
a DST transition would silently shift an hour of history between two
contributors' runs. Reproducibility beats DST fidelity for synthetic data.

---

## 5. `pg_stat_statements` is installed

*Cited from `alembic/versions/0001_20240817_1200-schemas_and_enums.py`.*

**Context.** Query-performance claims should be measurable in the running system
rather than asserted in prose.

**Decision.** The container preloads it via `shared_preload_libraries`, and
migration 0001 creates the extension row — both are needed, since preloading alone
does not create the view.

**Verified live:** `pg_stat_statements` 1.10 present, preloaded, view queryable.
Alongside it `pg_trgm` 1.6, which backs the trigram index behind `/search`.

**What it shows** — mean and max execution time over a full route sweep:

| Query | Mean | Max | Calls |
| --- | --- | --- | --- |
| `monetization/arpu_trend` | 955 ms | 2778 ms | 49 |
| `kpi/stickiness` | 917 ms | 2337 ms | 56 |
| `content/top_watch_time` | 290 ms | 2432 ms | 27 |
| `funnel/time_between_steps` | 273 ms | 627 ms | 27 |
| `kpi/wau` | 214 ms | 954 ms | 61 |

Storage, for scale: `core.events` is **311 MB across 65 partitions**, of which 19
hold rows; the four matviews total ~23 MB (`mv_funnel_steps` 8.1 MB,
`mv_content_daily` 7.7 MB, `mv_user_daily` 6.5 MB, `mv_user_lifetime` 288 kB);
the database is 359 MB.

**Consequence.** The two slowest queries are ~1 s at the mean over an
unfiltered 546-day window, which is why the cache is not decoration. Sub-25 MB of
materialized views standing in front of a 311 MB fact table is the trade the
analytics layer exists to make.

`events_default` holds **0 rows**, as it should — it exists so an insert outside
the declared range fails a data-quality check rather than the transaction. A
non-zero count there means the generator produced an event outside its own window.

---

## 6. Two database drivers

**Context.** The API is entirely async reads. Alembic and the seeder are
synchronous by nature, and the seeder needs `COPY`.

**Decision.** `asyncpg` for the API read path, `psycopg3` for Alembic and the
seeder.

**Consequence.** One real trap: asyncpg is strict where psycopg was lenient.
`CAST(:d AS date)` needs an actual `datetime.date`, and an ISO string raises
`'str' object has no attribute 'toordinal'` from inside the driver. Hence
`_coerce_date` — including its `datetime`-subclass exclusion, because `datetime`
IS-A `date`, so an `isinstance` guard alone would hand a timestamp to a date
column.

Also: **every cast of a bind parameter is `CAST(:p AS type)`, never `:p::type`.**
SQLAlchemy's bind regex ends with a negative lookahead `(?![:\w$])`, so a
colon-prefixed name followed by `::` is not recognised as a parameter and reaches
Postgres unbound. This cost a debugging session, so a test now scans every `.sql`
file and fails the build if the shorthand reappears — and asserts its own pattern
matches, because a scanner with a broken regex reports "no offenders" forever.

---

## 7. An undeclared query parameter is a 422

**Context.** FastAPI's default is to ignore unknown query parameters.

**Decision.** `strict_query` rejects them.

**Why.** Silently ignoring `date_form` returns a cheerful 200 for the wrong
window. Confidently wrong data is worse than a loud error.

**Consequence.** The window surface is not uniform and cannot be treated as such —
34 routes take `date_from`+`date_to`, 10 add `observation_end`, 7 take no window,
`/geo/country-ranking` takes `date_to` only, and the two experiment routes take
`observation_end` only. The frontend needs `windowParamsFor(path, window)` to strip
per path, because spreading one window object everywhere would 422 on twenty
routes. That helper is the cost of this decision, and it is worth it.

---

## 8. The window parameters have no defaults

**Context.** A dataset generated months before someone clones the repository.

**Decision.** No implicit "last 30 days".

**Why.** A default window would open every chart empty on a stale clone, and an
empty chart reads as a broken service rather than a badly chosen window. So the
first call any client makes is `GET /meta/bounds`, which reports the real coverage
plus `is_seeded`.

**Consequence.** Slightly more work for a caller, and one extra round trip on
first load. In exchange, an empty chart always means "no activity in this period"
and never "you asked the wrong question".

---

## 9. Lint debt is reported, not hidden and not mass-fixed

**Context.** At phase 12, `ruff check .` reports ~100 findings and `mypy` 15, all
in files delivered in phases 1–6. `prettier --check` has never passed, with 34
findings that are pure line-wrapping. The 330-test suite passes; the nine test
files added in phase 12 are ruff-clean and formatted.

**Decision.** CI's blocking jobs run the gates that genuinely pass. A
non-blocking `advisory` job reports the whole-tree counts on every push.

**Why not fix them.** It would mean a large mechanical diff across working,
reviewed code, in a working tree with no version-control history to fall back on.
Reformatting delivered files to satisfy a formatter is the churn this project set
out to avoid.

**Why not delete the gates.** That would hide the debt behind a green badge, which
is worse than carrying it visibly.

**Consequence, stated plainly.** A green tick means: the tests pass, the new code
is clean, the frontend type-checks and builds. It does **not** mean the tree is
lint-clean. The advisory job is where that is tracked, and the reasoning is
repeated at the top of `ci.yml` so nobody has to find this file to understand the
badge.

---

## 10. Value assertions check identities, not literals

**Context.** The build plan closed with an open limitation: *"the queries are
verified to EXECUTE and return plausible shapes, not to return CORRECT numbers."*

**Decision.** Twenty-three assertions on relationships — a funnel's stage rates
multiply to its end-to-end rate, an MRR waterfall balances, `risk_score` equals
the sum of its five components, rolling retention is never below classic,
conversion rises with watch time.

**Why not pin figures.** The expected value would have to be copied from the query
under test, which proves only that the query is deterministic, and every reseed
would fail the suite.

**Consequence.** The assertions survive a reseed and a profile change, and they
catch the failures that matter. What they do **not** cover is whether the planted
coefficients are recovered at a given profile — at `small` two of four experiments
come out with the wrong sign. That is a sample-size property of the profile, so it
is documented in `analytics-catalog.md` rather than asserted in a test a larger
profile would fail differently.

---

## 11. Rate limiting is per worker

**Context.** The service must run from one `docker compose up` with no shared
store.

**Decision.** A token bucket per client address in process memory: 60 tokens,
refilling at 240/minute, sized so one page load — a dozen concurrent requests —
cannot rate-limit itself. `/health`, `/docs` and `/openapi.json` are exempt so a
liveness probe never competes with real traffic.

**Consequence.** Under `--workers N` the effective limit is N times the configured
one. It bounds accidental load — a runaway `useEffect`, a scraper, a forgotten
load test — and is **not** a defence against a distributed attacker. Nothing in
the code or docs claims otherwise.

Rejections are *rendered* rather than raised, because a `raise` from middleware
never reaches FastAPI's exception handlers and would degrade to a bare 500.

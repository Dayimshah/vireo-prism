-- Dataset orientation: how much data exists, and whether the analytics views are usable.
--
-- Reads nothing from `analytics.*`, and that is the entire point. A materialized view
-- created WITH NO DATA raises `materialized view has not been populated` on any read,
-- which the repository layer translates into a 503. This query has to survive the
-- unseeded state in order to *report* it, so every column comes from `core` or from the
-- system catalogue.
SELECT
    (SELECT count(*) FROM core.users) AS users,

    -- Approximate, from the planner's own statistics. An exact count over a
    -- 65-partition table means scanning every partition, and this figure exists for
    -- orientation — "is there data here, roughly how much" — not for arithmetic.
    --
    -- Summed across the partitions, never read from the parent: a partitioned table's
    -- own `reltuples` is 0, so reading the parent would report an empty dataset on a
    -- fully seeded database. Measured: the sum matches `count(*)` exactly at 1,092,554.
    --
    -- GREATEST(..., 0) because PostgreSQL 14 and later store -1 for "never analyzed".
    -- Summing that would report a negative event count on a freshly restored database
    -- whose autovacuum has not caught up yet.
    (
        SELECT COALESCE(sum(GREATEST(child.reltuples, 0)), 0)::bigint
        FROM pg_class parent
        JOIN pg_namespace n ON n.oid = parent.relnamespace
        JOIN pg_inherits ON pg_inherits.inhparent = parent.oid
        JOIN pg_class child ON child.oid = pg_inherits.inhrelid
        WHERE n.nspname = 'core'
          AND parent.relname = 'events'
    ) AS approx_events,

    -- `relispopulated` is false for a matview created WITH NO DATA and never refreshed,
    -- which is precisely the migrated-but-unseeded state. The same test
    -- `app.db.session.healthcheck` uses, kept identical so the two cannot disagree.
    (
        SELECT COALESCE(bool_and(c.relispopulated), false)
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'analytics'
          AND c.relkind = 'm'
    ) AS analytics_ready

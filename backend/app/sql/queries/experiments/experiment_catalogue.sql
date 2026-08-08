-- Every experiment defined in the dataset, newest first.
--
-- The one query in this namespace that reads definitions rather than measuring
-- outcomes. It exists because the other two experiment endpoints are keyed by slug and
-- there was previously no way to discover a slug: a client had to already know that
-- `paywall-copy-value-first` exists before it could ask about it. A dashboard cannot
-- offer a picker built on knowledge it has no way to obtain, and hardcoding the four
-- current keys into a frontend would silently break on the next reseed.
--
-- No date range and no filters, deliberately
-- ------------------------------------------
-- This reads `core.experiments`, which holds definitions. A reporting window would
-- describe which experiments to list by a period they were not defined in, and the
-- user-scope filters describe people rather than tests. Both would be meaningless here,
-- so neither is accepted — and the API rejects an undeclared parameter rather than
-- ignoring it, so asking for one is an error rather than a silent no-op.
--
-- `enrolled_users` is a count of assignments, not of the population
-- ----------------------------------------------------------------
-- It answers "is this test big enough to be worth opening", which is the question a
-- picker needs. It is deliberately NOT the denominator of any test: the per-variant
-- endpoint recomputes its own `n` per arm after applying `observation_end`, and a total
-- taken from here would disagree with the sum of the arms whenever an observation
-- cut-off is in force. Sized for orientation, not arithmetic.
--
-- The LEFT JOIN is load-bearing. An experiment that is defined but not yet enrolled is a
-- real state — status `running` with no assignments on its first day — and an inner join
-- would hide it from the picker at exactly the moment someone went looking for it.
SELECT
    e.key                                       AS experiment_key,
    e.name                                      AS experiment_name,
    e.hypothesis,
    e.primary_metric,
    e.status,
    e.started_on,
    e.ended_on,
    ROUND(e.traffic_allocation, 2)              AS traffic_allocation,
    -- The check constraint guarantees a JSON array of at least two, so this is never
    -- null and never below 2. Read from the definition rather than counted from
    -- assignments: an arm with nobody in it is still an arm the test declared.
    JSONB_ARRAY_LENGTH(e.variants)::int         AS variant_count,
    COUNT(a.user_id)::bigint                    AS enrolled_users,
    -- Duration of the test itself, in days, inclusive of both endpoints. Null while an
    -- experiment is still running, which is honest: an unfinished test has no length
    -- yet, and substituting today's date would report a number that changes daily
    -- without anything having happened.
    CASE
        WHEN e.ended_on IS NULL THEN NULL
        ELSE (e.ended_on - e.started_on) + 1
    END                                         AS duration_days
FROM core.experiments AS e
LEFT JOIN core.experiment_assignments AS a USING (experiment_id)
GROUP BY
    e.experiment_id, e.key, e.name, e.hypothesis, e.primary_metric, e.status,
    e.started_on, e.ended_on, e.traffic_allocation, e.variants
-- Running tests first — a live experiment is the one someone checks on — then most
-- recently started. Fixed, not caller-supplied: no query in this project takes a
-- dynamic ORDER BY, so there is no injection surface here to guard.
ORDER BY (e.status = 'running') DESC, e.started_on DESC, e.key

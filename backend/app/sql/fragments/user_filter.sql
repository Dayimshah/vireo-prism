-- Shared user-scope filter. Requires core.users aliased as `u` in scope.
--
-- Every predicate is a no-op when its parameter is NULL, so one block serves any
-- subset of filters and callers never assemble WHERE clauses by hand.
--
-- The ::int[] and ::text[] casts are mandatory, not stylistic: asyncpg cannot
-- infer the type of a bare parameter in an `= ANY(...)` position and raises at
-- execution time without them.
AND (CAST(:country_ids AS int[])    IS NULL OR u.country_id = ANY(CAST(:country_ids AS int[])))
AND (CAST(:channel_ids AS int[])    IS NULL OR u.channel_id = ANY(CAST(:channel_ids AS int[])))
AND (CAST(:persona_ids AS int[])    IS NULL OR u.persona_id = ANY(CAST(:persona_ids AS int[])))
AND (CAST(:signup_device_ids AS int[]) IS NULL OR u.device_id = ANY(CAST(:signup_device_ids AS int[])))
AND (CAST(:is_premium AS boolean)   IS NULL OR u.is_premium = CAST(:is_premium AS boolean))

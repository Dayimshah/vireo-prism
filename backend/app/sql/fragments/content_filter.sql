-- Shared catalogue filter. Requires core.content aliased as `c` in scope.
AND (CAST(:genre_ids AS int[])      IS NULL OR c.genre_id = ANY(CAST(:genre_ids AS int[])))
AND (CAST(:content_types AS text[]) IS NULL OR c.content_type::text = ANY(CAST(:content_types AS text[])))
AND (CAST(:languages AS text[])     IS NULL OR c.language = ANY(CAST(:languages AS text[])))

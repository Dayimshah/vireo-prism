-- A gap-free date series over the requested window.
--
-- Analytics must distinguish "zero activity" from "no row". A LEFT JOIN against
-- this spine puts an explicit 0 on a quiet day, so a chart shows a dip rather than
-- silently closing the gap and implying continuity that was not there.
SELECT generate_series(CAST(:date_from AS date), CAST(:date_to AS date), INTERVAL '1 day')::date AS day

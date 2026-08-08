-- The first and last day the dataset has activity for, and the span between them.
--
-- Why this exists at all: the window parameters in `app/schemas/params.py` deliberately
-- have no defaults. A "last 30 days" default would open every chart empty on a
-- repository cloned months after its data was generated, and an empty chart reads as a
-- broken service rather than as a badly chosen window. This endpoint is the replacement
-- — a client asks what the data covers, then picks a window inside it.
--
-- Reads `analytics.mv_user_daily`, so unlike `meta/dataset_counts` it CANNOT survive the
-- unseeded state: a materialized view created WITH NO DATA raises on any read. The
-- service checks `analytics_ready` from that companion query first and skips this one
-- when the views are unpopulated. Splitting the two is the whole reason there are two
-- files rather than one.
--
-- `mv_user_daily` rather than `core.events`, for two reasons. It is the same relation
-- every analytics query derives its own date spine from, so the bounds reported here are
-- exactly the range those queries can answer for — reading `core.events` directly could
-- report a day the matviews have not been refreshed to cover yet, which would hand a
-- client a window whose charts come back empty. And it is orders of magnitude smaller
-- than the 65-partition event table.
--
-- The day span is inclusive of both ends, matching `DateWindow.days`: a single-day
-- dataset spans 1 day, not 0.
SELECT
    MIN(activity_date)                             AS first_activity_date,
    MAX(activity_date)                             AS last_activity_date,
    (MAX(activity_date) - MIN(activity_date) + 1)  AS days
FROM analytics.mv_user_daily

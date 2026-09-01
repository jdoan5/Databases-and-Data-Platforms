-- ============================================================================
-- Stage 6b — the break-even, and the thing it does not answer
--
-- Constants below are measured, not assumed. Re-measure with 60_matview.sql if
-- the hardware or the dataset changes.
-- ============================================================================

\set ON_ERROR_STOP on
\set view_ms      163.8
\set matview_ms     0.032
\set refresh_ms   137.1

\echo '=== compute break-even: how many reads before the refresh pays for itself ==='
SELECT :view_ms                                    AS view_ms,
       :matview_ms                                 AS matview_ms,
       round((:view_ms - :matview_ms)::numeric, 3) AS saved_per_read_ms,
       :refresh_ms                                 AS refresh_ms,
       round((:refresh_ms / (:view_ms - :matview_ms))::numeric, 2)
                                                   AS reads_to_break_even;

\echo ''
\echo '=== how fast does the underlying data actually change? ==='
SELECT count(*)                                                   AS movements,
       round(count(*)::numeric
             / GREATEST(extract(epoch FROM max(created_at) - min(created_at))
                        / 86400, 1))                              AS per_day,
       round(count(*)::numeric
             / GREATEST(extract(epoch FROM max(created_at) - min(created_at))
                        / 3600, 1))                               AS per_hour
FROM   stock_movements;

\echo ''
\echo '=== how wrong does the number get, per refresh interval? ==='
-- Average absolute valuation impact of one movement, times the movements that
-- land inside the staleness window.
WITH rate AS (
    SELECT count(*)::numeric
           / GREATEST(extract(epoch FROM max(created_at) - min(created_at)) / 3600, 1)
           AS per_hour
    FROM   stock_movements
), impact AS (
    SELECT avg(sm.quantity * p.unit_cost) AS avg_value
    FROM   stock_movements sm
    JOIN   products p ON p.product_id = sm.product_id
    LIMIT  1
)
SELECT w.window_label,
       round(rate.per_hour * w.hours)                          AS movements_missed,
       round(rate.per_hour * w.hours * impact.avg_value, 2)    AS expected_drift_usd,
       round(:refresh_ms / 1000.0 / (w.hours * 3600) * 100, 4) AS pct_of_time_refreshing
FROM   rate, impact,
       (VALUES ('1 minute', 1/60.0), ('5 minutes', 5/60.0),
               ('1 hour', 1.0), ('1 day', 24.0)) AS w(window_label, hours)
ORDER  BY w.hours;

\echo ''
\echo '=== conclusion in one row ==='
SELECT 'refresh every 5 min, concurrently'                        AS policy,
       round(:refresh_ms / 1000.0 * 12, 2) || ' s/hour'           AS server_time_spent,
       round(:refresh_ms / 1000.0 * 12 / 3600 * 100, 3) || '%'    AS duty_cycle,
       round(:view_ms / :matview_ms)                              AS read_speedup;

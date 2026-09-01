-- ============================================================================
-- Stage 6 — materialization: read latency against staleness against refresh cost
--
-- Q02 (v_stock_valuation) is the one query in the set with no index to add. It
-- is a full aggregate over 360,000 stock rows joined to products, collapsing to
-- six warehouse totals. Nothing to prune, nothing to seek -- the only lever left
-- is whether the answer is computed on demand or stored.
--
-- The question a materialized view actually poses is not "is reading faster"
-- (obviously) but "does the refresh cost less than the reads it saves, and can
-- the business tolerate the staleness in between". Both halves get measured.
-- ============================================================================

\set ON_ERROR_STOP on
\timing on

DROP MATERIALIZED VIEW IF EXISTS mv_stock_valuation;

\echo '=== build ==='
CREATE MATERIALIZED VIEW mv_stock_valuation AS
SELECT w.warehouse_id,
       w.code                            AS warehouse_code,
       w.name                            AS warehouse_name,
       count(DISTINCT sl.product_id)     AS distinct_skus,
       sum(sl.quantity)                  AS total_units,
       sum(sl.quantity * p.unit_cost)    AS valuation_at_cost,
       sum(sl.quantity * p.unit_price)   AS valuation_at_retail,
       now()                             AS refreshed_at
FROM   warehouses    w
LEFT   JOIN stock_levels sl ON sl.warehouse_id = w.warehouse_id
LEFT   JOIN products     p  ON p.product_id    = sl.product_id
GROUP  BY w.warehouse_id, w.code, w.name;

-- REFRESH CONCURRENTLY requires a unique index. Without one it fails outright,
-- which is the kind of thing you discover in production at 3am rather than now.
CREATE UNIQUE INDEX mv_stock_valuation_pk
    ON mv_stock_valuation (warehouse_id);

ANALYZE mv_stock_valuation;

SELECT pg_size_pretty(pg_total_relation_size('mv_stock_valuation')) AS matview_size;

\echo ''
\echo '=== read: computed on demand vs stored ==='
\echo '--- the view (warm) ---'
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF, SUMMARY ON)
SELECT warehouse_code, distinct_skus, total_units, valuation_at_cost
FROM   v_stock_valuation ORDER BY valuation_at_cost DESC;

\echo '--- the materialized view ---'
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF, SUMMARY ON)
SELECT warehouse_code, distinct_skus, total_units, valuation_at_cost
FROM   mv_stock_valuation ORDER BY valuation_at_cost DESC;

\echo ''
\echo '=== refresh cost, both modes ==='
\echo '--- blocking REFRESH ---'
REFRESH MATERIALIZED VIEW mv_stock_valuation;
\echo '--- REFRESH CONCURRENTLY ---'
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stock_valuation;

\echo ''
\echo '=== the lock each mode takes ==='
BEGIN;
REFRESH MATERIALIZED VIEW mv_stock_valuation;
SELECT mode, granted FROM pg_locks
WHERE  relation = 'mv_stock_valuation'::regclass AND locktype = 'relation';
ROLLBACK;

BEGIN;
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stock_valuation;
SELECT mode, granted FROM pg_locks
WHERE  relation = 'mv_stock_valuation'::regclass AND locktype = 'relation';
ROLLBACK;

\echo ''
\echo '=== staleness: the matview is wrong the instant anything moves ==='
SELECT warehouse_code, total_units, valuation_at_cost
FROM   mv_stock_valuation WHERE warehouse_id = 1;

-- One ordinary receipt. The trigger updates stock_levels; the matview does not
-- know and cannot know.
INSERT INTO stock_movements (product_id, warehouse_id, movement_type, quantity,
                             reference_type, notes)
VALUES (1970, 1, 'IN', 5000, 'MANUAL', 'stage 6 staleness probe');

SELECT 'live view'  AS source, total_units, valuation_at_cost
FROM   v_stock_valuation  WHERE warehouse_id = 1
UNION ALL
SELECT 'materialized', total_units, valuation_at_cost
FROM   mv_stock_valuation WHERE warehouse_id = 1;

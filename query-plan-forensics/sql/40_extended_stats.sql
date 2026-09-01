-- ============================================================================
-- Stage 4a — when the planner is wrong, and whether fixing it helps
--
-- product_id and warehouse_id are 85% correlated by construction. Postgres
-- estimates a multi-column AND by multiplying single-column selectivities,
-- which assumes independence -- so it divides by the number of warehouses and
-- lands roughly 6x low.
--
-- CREATE STATISTICS teaches it the joint distribution. The interesting question
-- is not whether the ESTIMATE improves (it must) but whether the PLAN and the
-- RUNTIME change at all. A misestimate only costs you when it drives a bad
-- choice. Measure both, and be willing to report "no effect".
-- ============================================================================

\set ON_ERROR_STOP on

\echo '=== BEFORE: estimate vs actual on the correlated pair ==='
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT movement_type, count(*) AS n, sum(quantity) AS units
FROM   stock_movements
WHERE  product_id = 1970 AND warehouse_id = 3
  AND  created_at >= TIMESTAMP '2026-01-01'
GROUP  BY movement_type
ORDER  BY n DESC;

\echo ''
\echo '=== the independence assumption, stated numerically ==='
SELECT
  (SELECT count(*) FROM stock_movements WHERE product_id = 1970)      AS rows_product,
  (SELECT count(*) FROM stock_movements WHERE warehouse_id = 3)       AS rows_warehouse,
  (SELECT count(*) FROM stock_movements)                              AS rows_total,
  round((SELECT count(*) FROM stock_movements WHERE product_id = 1970)::numeric
      * (SELECT count(*) FROM stock_movements WHERE warehouse_id = 3)
      / (SELECT count(*) FROM stock_movements))                       AS independent_estimate,
  (SELECT count(*) FROM stock_movements
    WHERE product_id = 1970 AND warehouse_id = 3)                     AS actual_both;

\echo ''
\echo '=== teach it the joint distribution ==='
DROP STATISTICS IF EXISTS stx_sm_product_warehouse;
CREATE STATISTICS stx_sm_product_warehouse (ndistinct, dependencies, mcv)
    ON product_id, warehouse_id FROM stock_movements;

ANALYZE stock_movements;

SELECT stxname,
       stxddependencies IS NOT NULL AS has_dependencies,
       stxdmcv          IS NOT NULL AS has_mcv
FROM   pg_statistic_ext
JOIN   pg_statistic_ext_data ON oid = stxoid
WHERE  stxname = 'stx_sm_product_warehouse';

\echo ''
\echo '=== AFTER ==='
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT movement_type, count(*) AS n, sum(quantity) AS units
FROM   stock_movements
WHERE  product_id = 1970 AND warehouse_id = 3
  AND  created_at >= TIMESTAMP '2026-01-01'
GROUP  BY movement_type
ORDER  BY n DESC;

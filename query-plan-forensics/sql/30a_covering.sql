-- ============================================================================
-- Stage 3a — composite vs. covering (INCLUDE)
--
-- Both indexes have the SAME keys. The only difference is that the covering one
-- carries three payload columns, so an index-only scan can answer Q04 without
-- ever touching the heap.
--
-- The gotcha this stage exists to surface: an Index Only Scan is not actually
-- heap-free until the visibility map says the pages are all-visible. Straight
-- after a bulk load the map is stale, so the plan says "Index Only Scan" and
-- then reports "Heap Fetches: <a large number>" -- which is most of the cost it
-- was supposed to remove. VACUUM is what makes the optimization real.
--
-- Measure in this order:
--   make bench STAGE=s3a_composite     (after part 1)
--   make bench STAGE=s3a_covering_dirty (after part 2, BEFORE the vacuum)
--   make bench STAGE=s3a_covering      (after part 3)
-- ============================================================================

\set ON_ERROR_STOP on
\timing on

\echo '--- part 1: composite ---'
DROP INDEX IF EXISTS idx_sm_prod_created_incl;
CREATE INDEX IF NOT EXISTS idx_sm_prod_created
    ON stock_movements (product_id, created_at DESC);
ANALYZE stock_movements;

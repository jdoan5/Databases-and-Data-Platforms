-- ============================================================================
-- Stage 3b — the partial index that works, and the one that cannot be built
--
-- Q09's predicate is syntactically identical to the index predicate, so the
-- planner's implication prover matches it and the index covers only the ~30% of
-- purchase_orders that are actually open.
--
-- Q01's predicate is quantity < reorder_point, which spans stock_levels and
-- products. A partial index predicate may only reference columns of its OWN
-- table, so this index cannot be created at all. That failure is the finding.
-- ============================================================================

\set ON_ERROR_STOP on
\timing on

\echo '--- the partial index that works (Q09) ---'
CREATE INDEX IF NOT EXISTS idx_po_open_expected
    ON purchase_orders (expected_date)
    WHERE status IN ('PLACED', 'PARTIALLY_RECEIVED');

ANALYZE purchase_orders;

SELECT indexrelname,
       pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM   pg_stat_user_indexes
WHERE  relname = 'purchase_orders'
ORDER  BY pg_relation_size(indexrelid) DESC;

\echo ''
\echo '--- the partial index that CANNOT be built (Q01) ---'
DO $$
BEGIN
    EXECUTE 'CREATE INDEX idx_sl_below_reorder_naive
             ON stock_levels (warehouse_id, product_id)
             WHERE quantity < (SELECT reorder_point FROM products p
                               WHERE p.product_id = stock_levels.product_id)';
    RAISE NOTICE 'UNEXPECTED: Postgres accepted a cross-table index predicate.';
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'As expected: %', SQLERRM;
    RAISE NOTICE 'A partial index predicate may only reference columns of its own table.';
    RAISE NOTICE 'Q01 cannot be indexed as written. The fix costs a denormalized';
    RAISE NOTICE 'column, a sync trigger, and a rewrite of v_low_stock_items --';
    RAISE NOTICE 'see 30b_partial_denorm.sql, and measure what that upkeep costs.';
END $$;

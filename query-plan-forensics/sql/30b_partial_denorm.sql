-- ============================================================================
-- Stage 3b, continued — what it costs to make Q01 indexable.
--
-- The honest fix for a cross-table predicate is to stop it being cross-table:
-- copy reorder_point down onto stock_levels, keep it in sync with a trigger,
-- and rewrite the view to reference the local column (the planner cannot match
-- the index predicate otherwise).
--
-- Three new liabilities, all of which get measured rather than waved at:
--   1. a denormalized column that can drift
--   2. a trigger on products that fires on every reorder_point change
--   3. a view rewrite, so the abstraction now leaks the optimization
-- ============================================================================

\set ON_ERROR_STOP on
\timing on

ALTER TABLE stock_levels
    ADD COLUMN IF NOT EXISTS reorder_point INT NOT NULL DEFAULT 0;

\echo '--- backfill (this is the migration cost on a live table) ---'
UPDATE stock_levels sl
SET    reorder_point = p.reorder_point
FROM   products p
WHERE  p.product_id = sl.product_id
  AND  sl.reorder_point IS DISTINCT FROM p.reorder_point;

-- Same-table, immutable predicate: legal.
CREATE INDEX IF NOT EXISTS idx_sl_below_reorder
    ON stock_levels (warehouse_id, product_id)
    WHERE quantity < reorder_point;

CREATE OR REPLACE FUNCTION sync_reorder_point() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE stock_levels SET reorder_point = NEW.reorder_point
    WHERE  product_id = NEW.product_id;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_sync_reorder_point ON products;
CREATE TRIGGER trg_sync_reorder_point
    AFTER UPDATE OF reorder_point ON products
    FOR EACH ROW WHEN (OLD.reorder_point IS DISTINCT FROM NEW.reorder_point)
    EXECUTE FUNCTION sync_reorder_point();

-- The view must reference the LOCAL column or the planner cannot match the
-- index predicate. The optimization has now leaked into the abstraction.
CREATE OR REPLACE VIEW v_low_stock_items AS
SELECT p.sku,
       p.name            AS product_name,
       w.code            AS warehouse_code,
       sl.quantity,
       sl.reorder_point,
       p.reorder_quantity,
       (sl.reorder_point - sl.quantity) AS deficit,
       GREATEST(p.reorder_quantity, sl.reorder_point - sl.quantity)
                                        AS suggested_order_qty
FROM   stock_levels sl
JOIN   products   p ON p.product_id   = sl.product_id
JOIN   warehouses w ON w.warehouse_id = sl.warehouse_id
WHERE  sl.quantity < sl.reorder_point;

ANALYZE stock_levels;

\echo ''
\echo '--- what the trigger costs: bump reorder_point on 500 products ---'
\timing on
UPDATE products SET reorder_point = reorder_point + 1
WHERE  product_id <= 500;
UPDATE products SET reorder_point = reorder_point - 1
WHERE  product_id <= 500;

\echo ''
SELECT indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM   pg_stat_user_indexes WHERE relname = 'stock_levels'
ORDER  BY pg_relation_size(indexrelid) DESC;
